import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PIL import Image as PILimage
from flask import g
from io import BytesIO
import time
import gevent

from .get import *
from ruqqus.__main__ import app, db_session

def expand_url(post_url, fragment_url):

    # convert src into full url
    if fragment_url.startswith("https://"):
        return fragment_url
    elif fragment_url.startswith("http://"):
        return f"https://{fragment_url.split('http://')[1]}"
    elif fragment_url.startswith('//'):
        return f"https:{fragment_url}"
    elif fragment_url.startswith('/'):
        parsed_url = urlparse(post_url)
        return f"https://{parsed_url.netloc}{fragment_url}"
    else:
        return f"{post_url}{'/' if not post_url.endswith('/') else ''}{fragment_url}"

def thumbnail_thread(pid, debug=False):

    #define debug print functionf
    def print_(x):
        if debug:
            try:
                print(x)
            except:
                pass

    db = db_session()

    post = get_post(pid, graceful=True, session=db)
    if not post:
        # account for possible follower lag
        time.sleep(60)
        post = get_post(pid, session=db)


    fetch_url=post.url

    #get the content

    #mimic chrome browser agent
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.72 Safari/537.36"}

    try:
        print_(f"loading {fetch_url}")
        x=requests.get(fetch_url, headers=headers)
    except:
        print_(f"unable to connect to {fetch_url}")
        db.close()
        return False, "Unable to connect to source"

    if x.status_code != 200:
        db.close()
        return False, f"Source returned status {x.status_code}."
    
    #detect if there was a redirect
    # requested_domain = post.domain
    # fetched_domain = urlparse(x.url).netloc
    
    # if requested_domain.lower() != fetched_domain.lower() and not post.domain_obj:
    #     post.is_banned=True
    #     post.ban_reason="No redirection services"
    #     g.db.add(post)
    #     g.db.add(post.submission_aux)
    #     g.db.commit()
    #     return

    #if content is image, stick with that. Otherwise, parse html.

    if x.headers.get("Content-Type","").startswith("text/html"):
        #parse html, find image, load image
        soup=BeautifulSoup(x.content, 'html.parser')
        #parse html

        #first, set metadata
        try:
            meta_title=soup.find('title')
            if meta_title:
                post.submission_aux.meta_title=str(meta_title.string)[0:500]

            meta_desc = soup.find('meta', attrs={"name":"description"})
            if meta_desc:
                post.submission_aux.meta_description=meta_desc['content'][0:1000]

            if meta_title or meta_desc:
                db.add(post.submission_aux)
                db.commit()

        except Exception as e:
            print(f"Error while parsing for metadata: {e}")
            pass

        #create list of urls to check
        thumb_candidate_urls=[]

        #iterate through desired meta tags
        meta_tags = [
            "ruqqus:thumbnail",
            "twitter:image",
            "og:image",
            "thumbnail"
            ]

        for tag_name in meta_tags:
            
            print_(f"Looking for meta tag: {tag_name}")


            tag = soup.find(
                'meta', 
                attrs={
                    "name": tag_name, 
                    "content": True
                    }
                )
            if not tag:
                tag = soup.find(
                    'meta',
                    attrs={
                        'property': tag_name,
                        'content': True
                        }
                    )
            if tag:
                thumb_candidate_urls.append(expand_url(post.url, tag['content']))

        #parse html doc for <img> elements
        for tag in soup.find_all("img", attrs={'src':True}):
            thumb_candidate_urls.append(expand_url(post.url, tag['src']))


        #now we have a list of candidate urls to try
        for url in thumb_candidate_urls:
            print_(f"Trying url {url}")

            try:
                image_req=requests.get(url, headers=headers)
            except:
                print_(f"Unable to connect to candidate url {url}")
                continue

            if image_req.status_code >= 400:
                print_(f"status code {x.status_code}")
                continue

            if not image_req.headers.get("Content-Type","").startswith("image/"):
                print_(f'bad type {image_req.headers.get("Content-Type","")}, try next')
                continue

            if image_req.headers.get("Content-Type","").startswith("image/svg"):
                print_("svg, try next")
                continue

            image = PILimage.open(BytesIO(image_req.content))
            if image.width < 30 or image.height < 30:
                print_("image too small, next")
                continue

            print_("Found a usable preview image")
            post.submission_aux.preview_image_url = url
            break

        else:
            #getting here means we are out of candidate urls (or there never were any)
            print_("Unable to find image")
            db.close()
            return False, "No usable images"

    elif x.headers.get("Content-Type","").startswith("image/"):
        #post url is itself a direct image - display it hotlinked, never re-hosted
        print_("post url is direct image")
        post.is_image = True

    else:

        print_(f'Unknown content type {x.headers.get("Content-Type")}')
        db.close()
        return False, f'Unknown content type {x.headers.get("Content-Type")} for submitted content'

    db.add(post)
    db.add(post.submission_aux)
    db.commit()
    db.close()

    return True, "Success"
