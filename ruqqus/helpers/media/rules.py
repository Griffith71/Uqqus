"""Rules for media stored in a member's own linked account (Google Drive, YouTube, ...).

Ruqqus keeps no permanent copy of an upload. A member links an account on a hosting
site once; uploads made here go into that account, and a post only holds a reference:

* an image or audio file lives in the member's storage and is shown through
  `/media/<id>/<token>.<ext>` (routes/media.py fetches it from the provider and
  Cloudflare caches the answer);
* a video lives on a site that plays it itself (YouTube), and the post links to it.

This module is the part with no I/O: what may be uploaded, by whom, how big, how the
address is written and read, and when an asset may be shown. Providers are in the
modules next to this one (base.py is their interface).
"""
import re

MIB = 1024 * 1024

IMAGE, AUDIO, VIDEO = "image", "audio", "video"
KINDS = (IMAGE, AUDIO, VIDEO)

# ext -> (kind, the Content-Type WE send). Never trust the provider's or the browser's
# type: the file's first bytes decide (sniff). No SVG or HTML: they can run script.
TYPES = {
    "jpg": (IMAGE, "image/jpeg"),
    "png": (IMAGE, "image/png"),
    "gif": (IMAGE, "image/gif"),
    "webp": (IMAGE, "image/webp"),
    "mp3": (AUDIO, "audio/mpeg"),
    "m4a": (AUDIO, "audio/mp4"),
    "ogg": (AUDIO, "audio/ogg"),
    "wav": (AUDIO, "audio/wav"),
    "flac": (AUDIO, "audio/flac"),
}

# what Ruqqus serves itself. Video is the hosting site's to limit.
SIZE_MAX = {IMAGE: 20 * MIB, AUDIO: 100 * MIB}
IMAGE_PIXELS_MAX = 50_000_000          # a decompression-bomb guard for the safety check
UPLOADS_PER_HOUR = 60

# asset.status
PENDING = "pending"        # an upload was started
READY = "ready"            # uploaded, checked, may be attached and shown
RESTRICTED = "restricted"  # the hosting site will not show it publicly (YouTube before the app is approved)
REMOVED = "removed"        # taken down by moderation or its owner
GONE = "gone"              # the hosting account no longer has it, or was disconnected
STATUSES = (PENDING, READY, RESTRICTED, REMOVED, GONE)

_NEXT = {
    PENDING: {READY, RESTRICTED, REMOVED, GONE},
    READY: {REMOVED, GONE},
    RESTRICTED: {READY, REMOVED, GONE},
    GONE: {READY, REMOVED},        # the member reconnected the account
    REMOVED: set(),                # final
}

# which provider module handles which kind, per kind of linked account
PROVIDERS = {
    "google": {IMAGE: "gdrive", AUDIO: "gdrive", VIDEO: "youtube"},
    "dev": {IMAGE: "dev", AUDIO: "dev"},
}
# providers whose files Ruqqus serves at /media/...; the others play on their own site
SERVED = ("gdrive", "dev")


class MediaError(Exception):
    """A message that is safe to show the member."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def can_change(status, new):
    return new in _NEXT.get(status, set())


def provider_for(account_provider, kind):
    """The provider module name for this kind on this linked account, or None."""
    return PROVIDERS.get(account_provider, {}).get(kind)


def kind_of(ext):
    entry = TYPES.get((ext or "").lower())
    return entry[0] if entry else None


def content_type(ext):
    return TYPES[ext][1]


def sniff(head):
    """The real type of a file from its first bytes: an ext from TYPES, or None."""
    head = bytes(head or b"")
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp" and head[8:12] in (b"M4A ", b"M4B ", b"mp42", b"isom", b"dash"):
        return "m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    return None


def ext_from_name(filename):
    name = (filename or "").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {"jpeg": "jpg", "jpe": "jpg", "oga": "ogg", "mp4a": "m4a"}.get(ext, ext)


def may_upload(user):
    """Everyone logged in may upload through their own linked account (the storage is
    theirs). Bans and suspensions apply as for any post."""
    return bool(user) and not getattr(user, "is_suspended", False) and not getattr(user, "is_deleted", False)


def check_request(kind, filename, size):
    """What a new upload must satisfy before the provider is asked for an address.
    Returns the ext to expect. Raises MediaError with a message for the member."""
    if kind not in KINDS:
        raise MediaError("Choose an image, audio or video file.")
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise MediaError("The file's size is missing.")
    if size <= 0:
        raise MediaError("That file is empty.")

    if kind == VIDEO:
        return ""        # the video site decides formats and length

    ext = ext_from_name(filename)
    if kind_of(ext) != kind:
        allowed = ", ".join(sorted(e for e, (k, _) in TYPES.items() if k == kind))
        raise MediaError(f"That file type is not supported here. Use one of: {allowed}.")
    limit = SIZE_MAX[kind]
    if size > limit:
        raise MediaError(f"That file is too big. The limit for {kind} files is {limit // MIB} MB.", 413)
    return ext


def check_uploaded(kind, head, size):
    """After the upload: the bytes themselves decide. Returns the real ext."""
    ext = sniff(head)
    if ext is None or kind_of(ext) != kind:
        raise MediaError(f"That does not look like {'an' if kind in (IMAGE, AUDIO) else 'a'} {kind} file.")
    if size is not None and int(size) > SIZE_MAX[kind]:
        raise MediaError(f"That file is too big. The limit for {kind} files is {SIZE_MAX[kind] // MIB} MB.", 413)
    return ext


# --- addresses -----------------------------------------------------------------------

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_REF = re.compile(r"/media/([0-9a-z]{1,13})/([A-Za-z0-9_-]{16,64})\.([a-z0-9]{2,4})(?![A-Za-z0-9])")


def b36(number):
    number = int(number)
    out = ""
    while True:
        number, digit = divmod(number, 36)
        out = _ALPHABET[digit] + out
        if not number:
            return out


def media_path(asset_id, token, ext):
    """The site-relative address of a served asset. It ends in the real extension so a
    CDN in front of the site treats it as a cacheable file."""
    return f"/media/{b36(asset_id)}/{token}.{ext}"


def parse_path(path):
    """(asset id, token, ext) for an address made by media_path, else None."""
    m = _REF.fullmatch(path or "")
    return (int(m.group(1), 36), m.group(2), m.group(3)) if m else None


def find_refs(*texts):
    """Every media address mentioned in a post's link or text, in order, once each.
    A match is only a claim: the caller checks the token and the owner."""
    seen, out = set(), []
    for text in texts:
        for m in _REF.finditer(text or ""):
            ref = (int(m.group(1), 36), m.group(2), m.group(3))
            if ref not in seen:
                seen.add(ref)
                out.append(ref)
    return out


# --- showing -------------------------------------------------------------------------

PUBLIC, PRIVATE = "public", "private"


def access(status, attached_live, is_owner):
    """How an asset may be fetched right now.

    PUBLIC: it is part of a live post or comment, anyone may see it and a CDN may keep it.
    PRIVATE: only its owner (the preview while writing), never cached.
    None: nobody. An asset nobody attached is not served to the public, so the site
    cannot be used as a free image host for somewhere else."""
    if status != READY:
        return None
    if attached_live:
        return PUBLIC
    return PRIVATE if is_owner else None


def parse_range(header, size):
    """A single `bytes=a-b` range as (start, end) inclusive, or None for the whole file.
    Anything else (several ranges, nonsense, out of bounds) is treated as no range."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or size is None or size <= 0:
        return None
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return None
    if not first:                       # the last N bytes
        length = int(last)
        if length <= 0:
            return None
        return (max(size - length, 0), size - 1)
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        return None
    return (start, end)
