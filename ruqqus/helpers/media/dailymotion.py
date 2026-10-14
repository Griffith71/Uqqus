"""Dailymotion as the home of a member's videos.

A video uploaded here goes to the member's own Dailymotion channel and plays through
Dailymotion's player: the post links to it (the site already embeds Dailymotion links,
helpers/embed.py). Nothing of the video is stored or served by Ruqqus.

Why it is offered next to YouTube: Dailymotion's upload limits belong to the member's account
(15 videos and 10 hours a day on a standard account), while YouTube's API allows a whole site
100 uploads a day until its audit passes.

The upload, in three steps:
1. the server asks Dailymotion for an upload address (`GET /file/upload`) and hands only
   that address to the browser (no Dailymotion credential reaches a browser);
2. the browser sends the file there as a form (`form_field`), and Dailymotion answers with
   the address of the uploaded file, which the browser passes back (`answer: True`);
3. the server creates the video on the member's channel from that address and publishes it
   (unlisted becomes a private video, watchable only through its private link).

A video shows the member's channel, so it is refused on anonymous posts and Circle posts,
as YouTube videos are (helpers/media/attach.py own_video, helpers/circles.py post_refusal).
"""
import json
from urllib.parse import urlparse

from . import dailymotion_oauth, oauth, rules
from .base import Provider, ProviderDown

API = dailymotion_oauth.API
CHANNEL = "people"                     # Dailymotion's category for a member's own videos
SESSION_SECONDS = 2 * 24 * 60 * 60
UPLOAD_HOST = ".dailymotion.com"       # where an uploaded file's address must point


def _json(response):
    try:
        return response.json() or {}
    except ValueError:
        return {}


def _session_key(asset):
    return f"media:dailymotion:session:{asset.id}"


def _limit_reached(response):
    error = _json(response).get("error") or {}
    text = json.dumps(error).lower() if isinstance(error, dict) else str(error).lower()
    return response.status_code in (403, 429) and ("limit" in text or "quota" in text)


def upload_address_ok(url):
    """Is this the address of a file on Dailymotion's own upload servers (https)? Only such an
    address is passed on to Dailymotion as the video's source."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.endswith(UPLOAD_HOST) and not parsed.username


class DailymotionProvider(Provider):
    name = "dailymotion"
    label = "Dailymotion"
    served = False
    wants_answer = True                # finishing needs what Dailymotion answered the browser

    def _api(self, account, method, url, **kwargs):
        return dailymotion_oauth.SERVICE.api(account, method, url, **kwargs)

    def begin_upload(self, account, asset, origin, details=None):
        details = details or {}
        r = self._api(account, "GET", f"{API}/file/upload")
        address = _json(r).get("upload_url")
        if r.status_code != 200 or not address:
            if _limit_reached(r):
                raise rules.MediaError("Dailymotion is not taking more uploads from your account today. Try again tomorrow.", 429)
            raise ProviderDown(f"Dailymotion did not start the upload ({r.status_code})")
        wanted = rules.video_visibility(details.get("visibility") or account.setting("video_visibility"))
        oauth.remember(_session_key(asset), json.dumps({
            "title": rules.video_title(details.get("title")),
            "description": rules.video_description(details.get("site")),
            "wanted": wanted,
        }), SESSION_SECONDS)
        return {"url": address, "method": "POST", "headers": {}, "form_field": "file", "answer": True}

    def finish_upload(self, account, asset, answer=None):
        raw = oauth.recall(_session_key(asset))
        if not raw:
            raise rules.MediaError("That upload expired. Add the video again.")
        if not upload_address_ok(answer):
            raise rules.MediaError("The video did not finish uploading to Dailymotion. Add it again.")
        session = json.loads(raw)
        unlisted = session.get("wanted") != "public"
        r = self._api(account, "POST", f"{API}/me/videos", params={"fields": "id,private_id,private,status"}, data={
            "url": answer,
            "title": session.get("title") or "Video",
            "description": session.get("description") or "",
            "channel": CHANNEL,
            "published": "true",
            "is_created_for_kids": "false",
            "private": "true" if unlisted else "false",
        })
        video = _json(r)
        if r.status_code not in (200, 201) or not video.get("id"):
            if _limit_reached(r):
                raise rules.MediaError("Dailymotion is not taking more uploads from your account today. Try again tomorrow.", 429)
            raise ProviderDown(f"Dailymotion answered {r.status_code}")
        if video.get("status") in ("rejected", "deleted"):
            raise rules.MediaError("Dailymotion did not accept that video.")
        oauth.forget_key(_session_key(asset))
        # an unlisted video plays only through its private id; that id is what the post links to
        ref = video.get("private_id") if unlisted and video.get("private_id") else video["id"]
        return {"ref": ref, "size": int(asset.size), "checksum": ""}

    def link(self, asset):
        return f"https://www.dailymotion.com/video/{asset.provider_ref}"

    def revoke(self, account):
        dailymotion_oauth.SERVICE.revoke(account)
