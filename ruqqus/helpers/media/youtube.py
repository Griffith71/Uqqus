"""YouTube as the home of a member's videos.

A video uploaded here goes to the member's own YouTube channel and plays through
YouTube's player: the post just links to it (the site already embeds YouTube links).
Nothing of the video is stored or served by Ruqqus.

What Google requires before this works for the public (helpers/media/google_oauth.py):
* the youtube.upload scope is "sensitive": Google must verify the OAuth app;
* the API project must pass YouTube's compliance audit. Until it does, YouTube keeps
  every video uploaded through the API private, and a private video cannot be embedded.
  That case is reported to the member as "restricted" rather than as a working upload.

The member needs a YouTube channel on the Google account they linked.
"""
import json

from . import google_oauth, rules
from .base import Provider, ProviderDown

UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CATEGORY_PEOPLE_AND_BLOGS = "22"
SESSION_SECONDS = 2 * 24 * 60 * 60

CONNECT_VIDEO = "/settings/media/google/connect?want=video"


def _json(response):
    try:
        return response.json() or {}
    except ValueError:
        return {}


def _reasons(response):
    """The machine-readable reasons in a YouTube error answer."""
    error = _json(response).get("error") or {}
    return {e.get("reason") for e in (error.get("errors") or []) if isinstance(e, dict)}


def _session_key(asset):
    return f"media:youtube:session:{asset.id}"


class YouTubeProvider(Provider):
    name = "youtube"
    served = False

    def can_upload(self, account):
        return google_oauth.SCOPE_YOUTUBE in (account.scopes or "").split()

    def begin_upload(self, account, asset, origin, details=None):
        if not self.can_upload(account):
            raise rules.MediaError("To add videos, allow uploads to your YouTube channel once.", 409,
                                   need="video", settings=CONNECT_VIDEO)
        details = details or {}
        wanted = rules.video_visibility(details.get("visibility") or account.setting("youtube_visibility"))
        body = {
            "snippet": {"title": rules.video_title(details.get("title")),
                        "description": rules.video_description(details.get("site")),
                        "categoryId": CATEGORY_PEOPLE_AND_BLOGS},
            "status": {"privacyStatus": wanted, "embeddable": True, "selfDeclaredMadeForKids": False},
        }
        r = google_oauth.api(account, "POST", UPLOAD, json=body, headers={
            "X-Upload-Content-Type": "video/*",
            "X-Upload-Content-Length": str(int(asset.size)),
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": origin,          # the browser that will send the bytes comes from here
        })
        location = r.headers.get("Location")
        if r.status_code != 200 or not location:
            reasons = _reasons(r)
            if "youtubeSignupRequired" in reasons:
                raise rules.MediaError("This Google account has no YouTube channel yet. Create one at youtube.com, then try again.", 409)
            if reasons & {"insufficientPermissions", "forbidden"} and r.status_code == 403:
                raise rules.MediaError("To add videos, allow uploads to your YouTube channel once.", 409,
                                       need="video", settings=CONNECT_VIDEO)
            if reasons & {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"}:
                raise rules.MediaError("YouTube is not taking more uploads right now. Try again later.", 429)
            raise ProviderDown(f"YouTube did not start the upload ({r.status_code})")

        # the video's id is only known once the bytes are in: remember the session to ask it
        google_oauth.remember(_session_key(asset), json.dumps({"url": location, "wanted": wanted}), SESSION_SECONDS)
        return {"url": location, "method": "PUT", "headers": {}}

    def finish_upload(self, account, asset):
        raw = google_oauth.recall(_session_key(asset))
        if not raw:
            raise rules.MediaError("That upload expired. Add the video again.")
        session = json.loads(raw)
        r = google_oauth.api(account, "PUT", session["url"], headers={
            "Content-Length": "0", "Content-Range": f"bytes */{int(asset.size)}"})
        if r.status_code == 308:
            raise rules.MediaError("The video did not finish uploading. Add it again.")
        if r.status_code == 404:
            raise rules.MediaError("That upload expired. Add the video again.")
        video = _json(r)
        if r.status_code not in (200, 201) or not video.get("id"):
            raise ProviderDown(f"YouTube answered {r.status_code}")

        status = video.get("status") or {}
        if status.get("uploadStatus") in ("rejected", "failed"):
            raise rules.MediaError("YouTube did not accept that video.")
        google_oauth.forget_key(_session_key(asset))
        # asked for unlisted or public, got private: the API project is not approved yet
        restricted = status.get("privacyStatus") == "private" and session.get("wanted") != "private"
        return {"ref": video["id"], "size": int(asset.size), "checksum": "", "restricted": restricted}

    def link(self, asset):
        return f"https://www.youtube.com/watch?v={asset.provider_ref}"

    def revoke(self, account):
        return None          # the Google permission is given back once, by the Drive provider
