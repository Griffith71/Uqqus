import boto3
import requests
from os import environ, remove
import piexif
import time
from urllib.parse import urlparse
from PIL import Image
import imagehash
from sqlalchemy import func

from ruqqus.classes.images import BadPic
from ruqqus.__main__ import db_session
from .base36 import hex2bin
from .i18n_runtime import N_
import gevent

BUCKET = environ.get("S3_BUCKET_NAME", "").strip()      # no default: never another site's bucket
CF_KEY = environ.get("CLOUDFLARE_KEY").lstrip().rstrip()
CF_ZONE = environ.get("CLOUDFLARE_ZONE").lstrip().rstrip()

# setup AWS connection
S3 = boto3.client("s3",
                  aws_access_key_id=environ.get(
                      "AWS_ACCESS_KEY_ID").lstrip().rstrip(),
                  aws_secret_access_key=environ.get(
                      "AWS_SECRET_ACCESS_KEY").lstrip().rstrip()
                  )

def check_phash(db, name):

    return db.query(BadPic).filter(
        func.levenshtein(
            BadPic.phash,
            hex2bin(str(imagehash.phash(Image.open(name))))
            ) < 10
        ).first()


def upload_from_url(name, url):

    #print('upload from url')

    x = requests.get(url)

    #print('got content')

    tempname = name.replace("/", "_")

    with open(tempname, "wb") as file:
        for chunk in x.iter_content(1024):
            file.write(chunk)

    if tempname.split('.')[-1] in ['jpg', 'jpeg']:
        piexif.remove(tempname)

    S3.upload_file(tempname,
                   Bucket=BUCKET,
                   Key=name,
                   ExtraArgs={'ACL': 'public-read',
                              "ContentType": "image/png",
                              "StorageClass": "INTELLIGENT_TIERING"
                              }
                   )

    remove(tempname)


PNG_MODES = ("1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA")


def crop_and_resize(img, resize):

    # a phone's portrait photo is stored sideways with a note saying so: the pop-up's preview (the browser) already honours it
    from PIL import ImageOps            # here, not at the top: a test stubs PIL with only Image (tests/test_csam_ban.py)
    i = ImageOps.exif_transpose(img)

    # the result is saved as a PNG, which a CMYK photo (a print JPEG) can't be: it used to fail the save after the old picture was gone
    if i.mode not in PNG_MODES:
        i = i.convert("RGBA" if "A" in i.mode else "RGB")

    # get constraining dimension
    org_ratio = i.width / i.height
    new_ratio = resize[0] / resize[1]

    if new_ratio > org_ratio:
        crop_height = int(i.width / new_ratio)
        box = (0, (i.height // 2) - (crop_height // 2),
               i.width, (i.height // 2) + (crop_height // 2))
    else:
        crop_width = int(new_ratio * i.height)
        box = ((i.width // 2) - (crop_width // 2), 0,
               (i.width // 2) + (crop_width // 2), i.height)

    return i.resize(resize, box=box)


def upload_file(name, file, resize=None):

    # temp save for exif stripping
    tempname = name.replace("/", "_")

    file.save(tempname)

    if tempname.split('.')[-1] in ['jpg', 'jpeg']:
        piexif.remove(tempname)

    if resize:
        i = Image.open(tempname)
        i = crop_and_resize(i, resize)
        i.save(tempname)

    S3.upload_file(tempname,
                   Bucket=BUCKET,
                   Key=name,
                   ExtraArgs={'ACL': 'public-read',
                              "ContentType": "image/png"
                              }
                   )

    remove(tempname)


def upload_from_file(name, filename, resize=None):

    tempname = name.replace("/", "_")

    if filename.split('.')[-1] in ['jpg', 'jpeg']:
        piexif.remove(tempname)

    if resize:
        i = Image.open(tempname)
        i = crop_and_resize(i, resize)
        i.save(tempname)

    S3.upload_file(tempname,
                   Bucket=BUCKET,
                   Key=name,
                   ExtraArgs={'ACL': 'public-read',
                              "ContentType": "image/png"
                              }
                   )

    remove(filename)


def delete_file(name):

    if not BUCKET:
        return              # no bucket configured: nothing was stored there (bug hunt; boto3 refuses an empty name)

    S3.delete_object(Bucket=BUCKET,
                     Key=name)

    # After deleting a file from S3, dump CloudFlare cache

    headers = {"Authorization": f"Bearer {CF_KEY}",
               "Content-Type": "application/json"}
    data = {'files': [f"https://{BUCKET}/{name}"]}
    url = f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE}/purge_cache"

    x = requests.post(url, headers=headers, json=data)


def _load_user(db, user):
    """`user` belongs to the request's session, which may be over; the hold is written by the scan's own, so it loads the row again."""
    from ruqqus.classes.user import User

    return db.get(User, user.id)


def check_csam_url(url, v, delete_content_function):

    parsed_url = urlparse(url)

    if not BUCKET or parsed_url.netloc != BUCKET:
        return

    headers = {"User-Agent": "Ruqqus webserver"}
    for i in range(10):
        x = requests.get(url, headers=headers)

        if x.status_code in [200, 451]:
            break
        else:
            time.sleep(20)

    db=db_session()

    if x.status_code == 451:
        # the uploader and their alts are put on hold, never banned: an admin decides what follows (helpers/auto_quarantine.py)
        from ruqqus.helpers import scan_bans
        scan_bans.quarantine_uploader(db, _load_user(db, v), detail="the CDN answered 451 for an uploaded picture", legal=True)

        delete_content_function()

        db.commit()
        scan_bans.tell(db)
        db.close()
        delete_file(parsed_url.path.lstrip('/'))
        return

    tempname=f"test_from_url_{parsed_url.path}"
    tempname=tempname.replace('/','_')

    with open(tempname, "wb") as file:
        for chunk in x.iter_content(1024):
            file.write(chunk)

    h=check_phash(db, tempname)
    if h:
        from ruqqus.helpers import scan_bans

        # put the user and their alts on hold, never ban them (helpers/auto_quarantine.py); the picture goes either way, and a limited QR sign-in holds
        # nobody on the site's own list's word (helpers/qr_login.py). The match above, which is the child-abuse list's, holds whoever it is:
        # CLAUDE.md "QR sign-in"
        if not getattr(v, "qr_limited", False):
            scan_bans.quarantine_uploader(db, _load_user(db, v),
                                          detail=f"matched a picture on the site's banned list: {h.ban_reason}")

        delete_content_function()

        db.commit()
        scan_bans.tell(db)

        # nuke aws
        delete_file(parsed_url.path.lstrip('/'))

    remove(tempname)
    db.close()
