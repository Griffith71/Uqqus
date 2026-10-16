"""Whether a stream is live now (helpers/livestreams.py): asked of YouTube's Data API (`YOUTUBE_API_KEY`) and
Twitch's Helix API (`TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`), both fixed first-party hosts, with only the video
id or channel name from the link. Each answer is kept CACHE_SECONDS in Redis for everyone; nothing about a viewer is
ever sent.

**Pages never wait for it**: `cached` reads what is known and starts one background look-up when nothing is (one per
stream at a time); the card says "Stream on Twitch" until the answer is in. Without a key nothing is asked.
"""
import os

CACHE_SECONDS = 60
TIMEOUT = 4


def _redis():
    from ruqqus.__main__ import r
    return r


def _key(ref):
    return f"stream:{ref[0]}:{ref[1]}"


def cached(ref):
    """The known status (live / upcoming / offline) or None, starting a background look-up when unknown."""
    r = _redis()
    if ref is None or r is None:
        return None
    value = r.get(_key(ref))
    if value is not None:
        return value or None
    if r.set(_key(ref) + ":asking", "1", ex=TIMEOUT * 3, nx=True):
        try:
            import gevent
            gevent.spawn(_look_up, ref)
        except ImportError:
            _look_up(ref)
    return None


def _look_up(ref):
    r = _redis()
    try:
        status = youtube(ref[1]) if ref[0] == "youtube" else twitch(ref[1])
    except Exception:
        status = None
    if r is not None:
        r.set(_key(ref), status or "", ex=CACHE_SECONDS)


def youtube(video_id):
    import requests
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        return None
    resp = requests.get("https://www.googleapis.com/youtube/v3/videos",
                        params={"part": "snippet", "id": video_id, "key": key}, timeout=TIMEOUT)
    if resp.status_code != 200:
        return None
    items = resp.json().get("items") or []
    if not items:
        return None
    state = (items[0].get("snippet") or {}).get("liveBroadcastContent")
    return {"live": "live", "upcoming": "upcoming"}.get(state, "offline")


def _twitch_token():
    import requests
    r = _redis()
    if r is not None:
        token = r.get("stream:twitch:token")
        if token:
            return token
    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        return None
    resp = requests.post("https://id.twitch.tv/oauth2/token",
                         data={"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"},
                         timeout=TIMEOUT)
    if resp.status_code != 200:
        return None
    data = resp.json()
    token = data.get("access_token")
    if token and r is not None:
        r.set("stream:twitch:token", token, ex=max(60, int(data.get("expires_in", 3600)) - 300))
    return token


def twitch(channel):
    import requests
    token = _twitch_token()
    if not token:
        return None
    resp = requests.get("https://api.twitch.tv/helix/streams", params={"user_login": channel},
                        headers={"Client-Id": os.environ.get("TWITCH_CLIENT_ID", "").strip(), "Authorization": f"Bearer {token}"},
                        timeout=TIMEOUT)
    if resp.status_code != 200:
        return None
    return "live" if (resp.json().get("data") or []) else "offline"


def describe(post_url):
    """The card for a post's link (helpers/livestreams.card), with what is known now."""
    from ruqqus.helpers import livestreams
    ref = livestreams.stream_ref(post_url)
    if ref is None:
        return None
    return livestreams.card(ref, cached(ref))

