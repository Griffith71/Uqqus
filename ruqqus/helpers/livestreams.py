"""Livestreams: a post that links to a member's own stream on YouTube or Twitch shows whether it is live (pure rules;
the status is asked in helpers/livestream_status.py, the card is partials/livestream_card.html).

Live video costs bandwidth, and media on this site must cost it nothing (the owner's rule), so a stream lives on the
member's own YouTube or Twitch (their camera page or OBS) and its replay stays there. The site shows a card, never
a new player frame (the quote-card rule: no social site's frame), and the post's own live thread and comments beside
it (helpers/live_threads.py). A YouTube link already draws the site's existing video player; a Twitch link is a card.

- `stream_ref(url)`: ("youtube", video id) for a YouTube video or live link, ("twitch", channel) for a Twitch channel
  link, else None. Twitch's own pages (directory, settings, videos...) are not channels.
- A YouTube link shows the card only while YouTube says it is live or upcoming (most YouTube links are not streams);
  a Twitch channel link always does, as Live, Offline or, without the site's Twitch key, just "Stream on Twitch".
"""
import re
from urllib.parse import urlsplit

from ruqqus.helpers.social_embeds import youtube_id

YOUTUBE, TWITCH = "youtube", "twitch"
LIVE, UPCOMING, OFFLINE = "live", "upcoming", "offline"
LABELS = {YOUTUBE: "YouTube", TWITCH: "Twitch"}

_TWITCH_CHANNEL = re.compile(r"^[A-Za-z0-9_]{3,25}$")
_TWITCH_PAGES = {"directory", "settings", "videos", "p", "jobs", "store", "downloads", "turbo", "search", "login",
                 "signup", "subscriptions", "inventory", "wallet", "drops", "friends", "messages", "payments", "prime"}


def stream_ref(url):
    if not url:
        return None
    video = youtube_id(url)
    if video:
        return (YOUTUBE, video)
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower()
    if parts.scheme in ("http", "https") and host in ("twitch.tv", "www.twitch.tv", "m.twitch.tv"):
        pieces = [p for p in parts.path.split("/") if p]
        if len(pieces) == 1 and pieces[0].lower() not in _TWITCH_PAGES and _TWITCH_CHANNEL.match(pieces[0]):
            return (TWITCH, pieces[0].lower())
    return None


def watch_url(ref):
    service, key = ref
    return f"https://www.youtube.com/watch?v={key}" if service == YOUTUBE else f"https://www.twitch.tv/{key}"


def card(ref, status):
    """What the card says, or None when there is nothing to say (a YouTube link that is not a stream)."""
    if ref is None:
        return None
    service, _ = ref
    if service == YOUTUBE and status not in (LIVE, UPCOMING):
        return None
    label = LABELS[service]
    if status == LIVE:
        text = f"Live now on {label}"
    elif status == UPCOMING:
        text = f"Starting soon on {label}"
    elif status == OFFLINE:
        text = f"Offline on {label}"
    else:
        text = f"Stream on {label}"
    return {"service": service, "label": label, "status": status, "text": text, "url": watch_url(ref)}
