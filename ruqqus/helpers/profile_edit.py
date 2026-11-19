"""What is checked before a profile is saved, once for every place that saves one: Settings > Profile
(routes/settings.py), the Edit profile pop-up (routes/profile_edit_pages.py, assets/js/profile_edit.js) and the bots'
forms (routes/bot_accounts.py). Nothing here assigns anything to an account or touches the bucket: a check answers with
a value or a refusal, and the route decides what to write. tests/test_profile_edit.py.

* **Display name and details** are the rules of helpers/display_names.py and helpers/profile_details.py, with the two
  checks that need the database (another account's @handle, the banned-domain list) apart from the cleaning, so a
  caller can skip them for a value that did not change.
* **The bio** is rendered and sanitized as for Settings: `check_bio`. It is also refused when the rendered HTML would not
  fit its column (`users.bio_html` is varchar(1000); a link-heavy 256-character bio used to fail at the commit, a 500
  after the picture had already been changed).
* **A picture** is opened and decoded in full before anything is stored (`check_image`): a file that only looks like a
  picture used to be found out halfway through the upload.
"""
import io
import re

from sqlalchemy import text

from ruqqus.helpers import display_name_store, display_names, profile_details, text_length
from ruqqus.helpers.i18n_runtime import msg, tr

BIO_MAX = 256                 # as members count (helpers/text_length.py)
BIO_STORED_MAX = 300          # users.bio
BIO_HTML_MAX = 1000           # users.bio_html

PICTURE_MAX_BYTES = 16 * 1024 * 1024        # what Settings has always said (nothing enforced it)
REQUEST_MAX_BYTES = 2 * PICTURE_MAX_BYTES + 1024 * 1024    # an avatar, a banner and the text
MAX_PIXELS = 40_000_000       # decoded in full: a small file can hold a very large picture
IMAGE_FORMATS = ("PNG", "JPEG", "MPO", "GIF", "WEBP")      # never SVG. MPO is how Pillow names a JPEG holding more than one picture (most phones' photos)

LOCK_NAMESPACE = 1886547814   # "prof": the first key of the advisory lock (the second is the account)

_NEWLINES = re.compile(r"\r\n?")


def newlines(value):
    """Line breaks as the browser's counter sees them: a form sends CRLF, a text box counts one."""
    return _NEWLINES.sub("\n", value or "")


def lock_account(db, user_id):
    """One edit of an account's profile at a time, until the request's commit: two overlapping saves would take the same
    picture number and the same temporary file. Only the pop-up's route takes it (Settings' own picture forms and the bots'
    do not), so this orders pop-up saves against each other. A no-op off Postgres (the tests' SQLite)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    # a save that hangs (the bucket) must not queue the member's other saves for ever: the wait gives up, the request fails
    db.execute(text("SET LOCAL lock_timeout = '15s'"))
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :user)"), {"ns": LOCK_NAMESPACE, "user": int(user_id)})


# --- display name ------------------------------------------------------------------------------------------------

def clean_display_name(raw):
    """(name, message): the name as it would be saved, or the sentence for why it can't be."""
    try:
        return display_names.clean(raw), None
    except display_names.Refused as refused:
        return "", str(refused)


def display_name_refusal(db, user_id, name, site_names=(), admin=False, handle_message=None):
    """The sentence for a clean name that still can't be worn: another account's @handle (or a look-alike), or the
    staff's or the site's own name (admins are exempt from that one). None when it's fine. `handle_message` is
    the caller's own wording (a bot's name is not "your" display name)."""
    if not name:
        return None
    if display_name_store.takes_a_handle(db, user_id, name):
        return handle_message or tr("That is another account's username, so it can't be your display name.")
    if admin:
        return None
    return display_names.reserved_refusal(name, tuple(site_names))


# --- job, location, website -----------------------------------------------------------------------------------------

DETAIL_FIELDS = ("profile_job", "profile_location", "profile_website")


def clean_details(job, location, website):
    """(job, location, website, errors): each cleaned (helpers/profile_details.py), and `errors` maps the field's name
    (`profile_job`...) to its sentence for each one that can't be saved, in the order they are written."""
    errors = {}
    values = []
    for field, raw, clean in (("profile_job", job, lambda x: profile_details.clean_text(x, "Job")),
                              ("profile_location", location, lambda x: profile_details.clean_text(x, "Location")),
                              ("profile_website", website, profile_details.clean_website)):
        try:
            values.append(clean(raw))
        except profile_details.Refused as refused:
            errors[field] = str(refused)
            values.append("")
    return values[0], values[1], values[2], errors


def website_refusal(website):
    """The banned-domain list, asked as for a link in a bio. The sentence, or None."""
    if not website:
        return None
    from markupsafe import escape
    from ruqqus.helpers.filters import filter_comment_html
    bans = filter_comment_html(f'<a href="{escape(website)}">x</a>')
    if not bans:
        return None
    if bans[0].reason:
        return tr("%(domain)s can't be linked from a profile. %(reason)s", domain=bans[0].domain,
                  reason=msg(bans[0].reason_text))
    return tr("%(domain)s can't be linked from a profile.", domain=bans[0].domain)


# --- bio -----------------------------------------------------------------------------------------------------------

class BioCheck:
    """The result of `check_bio`. `code` is None when the bio can be saved (`text` and `html` are what to store);
    otherwise "too_long", "unchanged", "links" (a banned domain; `malicious` when it is the worst kind, which the
    caller answers by putting the account on hold: helpers/auto_quarantine.py) or "formatting" (rendered, it would not fit), and `message` is the sentence."""

    def __init__(self, text="", html="", code=None, message=None, malicious=False):
        self.text, self.html, self.code, self.message, self.malicious = text, html, code, message, malicious


def render_bio(bio):
    """A bio as HTML: markdown, then the narrow list of tags bios may keep (links and text styles, no headings, tables
    or pictures). Standard markdown: only a post's or a comment's text takes single line breaks."""
    import mistletoe
    from ruqqus.helpers.markdown import CustomRenderer
    from ruqqus.helpers.sanitize import sanitize
    with CustomRenderer() as renderer:
        html = renderer.render(mistletoe.Document(bio))
    return sanitize(html, bio=True, linkgen=True)


def find_bans(html):
    from ruqqus.helpers.filters import filter_comment_html
    return filter_comment_html(html)


def check_bio(raw, current, render=None, bans=None):
    """Everything Settings checks about a bio, in its order: the length as members count it, whether it changed,
    the banned-domain list. `current` is the stored text. `render` and `bans` are for the tests."""
    bio = newlines(raw)
    stored = newlines(current)
    # the bio you already have is not looked at again: a limit lowered since must not turn "no change" into a refusal
    if bio == stored:
        return BioCheck(text=bio, code="unchanged", message=tr("You didn't change anything"))
    if text_length.over_limit(bio, BIO_MAX, stored_max=BIO_STORED_MAX):
        return BioCheck(code="too_long", message=text_length.limit_message("A bio", BIO_MAX))

    from ruqqus.helpers.markdown import preprocess
    prepared = preprocess(bio)
    if prepared == stored:
        return BioCheck(text=prepared, code="unchanged", message=tr("You didn't change anything"))

    html = (render or render_bio)(prepared)

    found = (bans or find_bans)(html)
    if found:
        ban = found[0]
        if ban.reason:
            message = tr("Remove the %(domain)s link from your bio and try again. %(reason)s", domain=ban.domain,
                         reason=msg(ban.reason_text))
        else:
            message = tr("Remove the %(domain)s link from your bio and try again.", domain=ban.domain)
        return BioCheck(prepared, html, "links", message, malicious=any(x.reason == 4 for x in found))

    if len(html) > BIO_HTML_MAX:
        return BioCheck(prepared, html, "formatting",
                        tr("That bio has too many links or too much formatting. Shorten it."))
    return BioCheck(prepared, html)


def looks_like_link_spam(account, bio_html, now):
    """The "seo profile spam" rule: an account under a day old, with no post and no comment, whose bio holds a link.
    The routes put it on hold (after saving the bio: helpers/auto_quarantine.py) and never say which rule it was."""
    if now - account.created_utc >= 60 * 60 * 24 or account.post_count or account.comment_count:
        return False
    from bs4 import BeautifulSoup
    return bool(BeautifulSoup(bio_html, "html.parser").find("a"))


# --- pictures --------------------------------------------------------------------------------------------------------

def check_image(file, max_bytes=PICTURE_MAX_BYTES, formats=IMAGE_FORMATS):
    """(file, code) for a picture sent in a form: a real PNG, JPEG, GIF or WebP of at most `max_bytes`, found out by
    opening and decoding it, not by its name. `code` is None when it can be stored, else "too_big", "not_a_picture",
    "format" or "pixels" (`picture_message` has the sentences). (None, None) when nothing was sent. The stream is
    left at the start for the upload."""
    if not file or not getattr(file, "filename", ""):
        return None, None
    data = file.stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None, "too_big"
    from PIL import Image               # outside the try: a missing Pillow is a broken install, not "not a picture"
    try:
        picture = Image.open(io.BytesIO(data))
        kind = picture.format
        width, height = picture.size
    except Exception:
        return None, "not_a_picture"
    if kind not in formats:
        return None, "format"
    if width * height > MAX_PIXELS:
        return None, "pixels"
    try:
        picture.verify()
        whole = Image.open(io.BytesIO(data))
        whole.load()                    # a truncated file fails here, not halfway through the upload
    except Exception:
        return None, "not_a_picture"
    file.stream.seek(0)
    return file, None


def picture_message(code, megabytes):
    """The sentence for a refusal of `check_image`; the size is the caller's limit (8 for a bot, 16 for a member)."""
    if code == "too_big":
        # a bot's 8 MB keeps its own sentence; a member's is the one every other upload says (already translated)
        return tr("A picture can be 8 MB at most.") if megabytes == 8 else tr("That file is too big. The limit for image files is %(size)s MB.", size=megabytes)
    if code == "format":
        return tr("A picture must be a PNG, JPEG, GIF or WebP file.")
    if code == "pixels":
        return tr("That file is too big.")
    return tr("That file is not a picture.")
