"""Stories: the rules, with no I/O.

A story is a picture (from the member's linked storage), a text card, or - on a PUBLIC story only - a video from YouTube.
It lasts 24 hours, then it is only the member's own (their archive) unless they keep it in a Highlight. Who may see a story
is the same rule as for a post (helpers/circles.py `can_see`): Public, Subscribers or Close Friends, and always its author.
Viewers are remembered only so the ring can turn grey and the author can see how many watched: never who.

A video cannot be kept private: a YouTube link works for anyone who holds it, so a video story is public or it is not made.
"""
import re

from ruqqus.helpers import circles

TTL = 24 * 60 * 60
TEXT_MAX = 300
DAILY_MAX = 30
TITLE_MAX = 30
HIGHLIGHTS_MAX = 20
HIGHLIGHT_STORIES_MAX = 50
REASON_MAX = 200
ARCHIVE_LIMIT = 100

IMAGE, TEXT, VIDEO = "image", "text", "video"
KINDS = (IMAGE, TEXT, VIDEO)

# text cards: the name is all that is stored (the colours live in the stylesheets as .story-bg-<name>)
BACKGROUNDS = ("ocean", "sunset", "forest", "berry", "slate", "gold")

UNSEEN, SEEN = "unseen", "seen"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIGITS = re.compile(r"[0-9]{1,10}")
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def parse_kind(raw):
    """(kind, None) or (None, message)."""
    text = (raw or "").strip().lower()
    if text in KINDS:
        return text, None
    return None, "Choose a picture, a text card or a video."


def parse_background(raw):
    """A text card's colour name; anything that is not one of ours is the first."""
    text = (raw or "").strip().lower()
    return text if text in BACKGROUNDS else BACKGROUNDS[0]


def clean_text(raw):
    """(text, None) or (None, message): control characters out, ends trimmed, at most TEXT_MAX characters."""
    text = _CONTROL.sub("", raw or "").strip()
    if len(text) > TEXT_MAX:
        return None, f"A story can say {TEXT_MAX} characters at most."
    return text, None


def parse_video(raw):
    """The YouTube video id in an id or link, or ''. Only the id is stored and drawn (always the same embed address)."""
    text = (raw or "").strip()
    if _YOUTUBE_ID.fullmatch(text):
        return text
    match = re.match(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", text)
    return match.group(1) if match else ""


def refusal(kind, audience, text, has_picture, video_ref):
    """Why this story cannot be made, or None."""
    if kind == TEXT and not text:
        return "Write something for your text card."
    if kind == IMAGE and not has_picture:
        return "Add a picture."
    if kind == VIDEO:
        if not video_ref:
            return "Paste a YouTube link."
        if audience != circles.PUBLIC:
            return "A video can only go on a public story: anyone with its link can watch it, so it can't be kept for your Circle."
    return None


def expires_at(now):
    return int(now) + TTL


def is_live(expires_utc, deleted_utc, now):
    """Up now: not deleted and not past its 24 hours."""
    return not deleted_utc and expires_utc > now


def ring(story_ids, seen_ids):
    """The ring around an avatar: UNSEEN if any story the viewer may watch is new to them, SEEN if they watched all, None if none."""
    ids = list(story_ids)
    if not ids:
        return None
    return UNSEEN if any(i not in seen_ids for i in ids) else SEEN


def parse_title(raw):
    """(title, None) or (None, message) for a Highlight."""
    text = _CONTROL.sub("", raw or "").strip()
    if not text:
        return None, "Give the highlight a name."
    if len(text) > TITLE_MAX:
        return None, f"A highlight's name can be {TITLE_MAX} characters at most."
    return text, None


def clean_ids(raws, limit=HIGHLIGHT_STORIES_MAX):
    """Plain ASCII-digit ids, each once, in order, at most `limit` (anything else is dropped)."""
    seen, out = set(), []
    for raw in raws:
        text = str(raw).strip()
        if _DIGITS.fullmatch(text) and int(text) not in seen:
            seen.add(int(text))
            out.append(int(text))
        if len(out) >= limit:
            break
    return out


def parse_reason(raw):
    return _CONTROL.sub("", raw or "").strip()[:REASON_MAX]


def describe_left(expires_utc, now):
    """How long a story has left, in words ("23h left", "40m left", "ended")."""
    seconds = int(expires_utc) - int(now)
    if seconds <= 0:
        return "ended"
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m left"
    return f"{seconds // 3600}h left"
