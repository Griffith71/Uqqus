import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from PIL import Image as PILimage
from flask import g
from io import BytesIO
import time
import gevent

from .get import *
from .embed import detect_video_embed, known_provider_embed
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

    # Named-platform embeds (YouTube, Vimeo, TikTok, etc - see
    # KNOWN_PROVIDER_HANDLERS in embed.py) are checked before fetching
    # fetch_url at all, not just before parsing it. These call their own
    # dedicated API/URL directly and don't need fetch_url's page content -
    # which matters because several platforms' own web pages (TikTok
    # confirmed; likely others) sit behind bot-detection that blocks this
    # plain requests.get() outright, even though their independent oEmbed
    # endpoint (a different host) answers it just fine. Checking fetch_url's
    # fetchability first would make those platforms fail for a reason
    # that has nothing to do with whether the embed itself actually works.
    if not post.embed_url:
        known_type, known_src, known_thumb = known_provider_embed(fetch_url)
        if known_type:
            post.submission_aux.embed_url = known_src
            post.submission_aux.embed_type = known_type
            if known_thumb:
                post.submission_aux.preview_image_url = known_thumb
            db.add(post)
            db.add(post.submission_aux)
            db.commit()
            db.close()
            return True, "Success"

    try:
        print_(f"loading {fetch_url}")
        x=requests.get(fetch_url, headers=headers, timeout=10)
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

    content_type = x.headers.get("Content-Type", "")

    #if content is image, stick with that. Otherwise, parse html.

    if content_type.startswith("text/html"):
        #parse html, find image, load image
        soup=BeautifulSoup(x.content, 'html.parser')
        #parse html

        # Generic video detection (oEmbed/og:video/direct-file fallback for
        # domains with no Domain.embed_function configured) - skipped if a
        # domain-specific embed was already set synchronously at submit
        # time (ruqqus/routes/posts.py), which always takes priority.
        embed_thumb = None
        if not post.embed_url:
            embed_type, embed_src, embed_thumb = detect_video_embed(post.url, x, soup)
            if embed_type:
                # Writing submission_aux directly, not the Submission.embed_url
                # property setter - that setter calls g.db.add(...), which
                # breaks here since this greenlet has no active Flask
                # request/app context (it manages its own db session above).
                post.submission_aux.embed_url = embed_src
                post.submission_aux.embed_type = embed_type

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

        #create list of urls to check - an oEmbed-provided thumbnail (if
        #any) goes first since it's generally higher quality than a
        #scraped <img> tag
        thumb_candidate_urls=[embed_thumb] if embed_thumb else []

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
                image_req=requests.get(url, headers=headers, timeout=10)
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
            #getting here means we are out of candidate urls (or there never were any).
            #Not a hard failure if we already have a video embed - the
            #.embed-lg placeholder background covers the no-thumbnail case.
            print_("Unable to find image")
            if not post.embed_url:
                db.close()
                return False, "No usable images"

    elif content_type.startswith("video/"):
        #direct video file - no html to scrape for a thumbnail; handled by
        #detect_video_embed below, the .embed-lg placeholder background
        #covers the no-thumbnail case same as any other missing thumbnail.
        if not post.embed_url:
            embed_type, embed_src, _ = detect_video_embed(post.url, x, None)
            if embed_type:
                post.submission_aux.embed_url = embed_src
                post.submission_aux.embed_type = embed_type

    elif content_type.startswith("image/"):
        #post url is itself a direct image - display it hotlinked, never re-hosted
        print_("post url is direct image")
        post.is_image = True

    else:

        print_(f'Unknown content type {content_type}')
        db.close()
        return False, f'Unknown content type {x.headers.get("Content-Type")} for submitted content'

    db.add(post)
    db.add(post.submission_aux)
    db.commit()
    db.close()

    return True, "Success"
