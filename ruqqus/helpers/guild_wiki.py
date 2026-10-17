"""Guild wikis and pinned resources (pure rules, no I/O; the SQL is helpers/guild_wiki_store.py, the routes
routes/guild_wiki.py).

A guild's guildmasters switch its wiki on (`boards.wiki_enabled`), as Reddit's subreddit wikis: pages of markdown at
`/+<guild>/wiki/<slug>`, `index` first, every edit kept as a revision anyone who can see the guild reads (History, a
diff between two revisions), and Restore for guildmasters (a new revision with the old text, so nothing is lost).

- **Who edits a page** is chosen per page (`edit_level`): guildmasters with content permission (MODS), also the guild's
  approved contributors (APPROVED), or any member who joined at least MEMBER_DAYS ago (MEMBERS). Never a suspended
  account, a bot, an exile, or anyone in a block with the guild's founder (the block is never named: one message).
  A locked page is edited by guildmasters only, whatever its level.
- **Pages** are markdown up to WIKI_MAX characters, rendered and sanitized like a comment, and rated by the word filter
  (a reader whose filter hides a revision sees a notice). MAX_PAGES a guild, EDITS_PER_DAY edits a person a guild.
- **Who reads** is whoever can see the guild (`Board.can_view`, `board_hidden`): a private or Circle guild's wiki is for
  its members, a banned guild's for nobody.

Pinned resources (Reddit's community bookmarks): up to MAX_RESOURCES buttons under the guild's header, each a link
(checked like a profile's website), a wiki page, or a post (left out for a reader who may not open it).
"""
import difflib
import re

WIKI_MAX = 40_000
TITLE_MAX = 80
REASON_MAX = 140
SLUG_MAX = 40
MAX_PAGES = 100
EDITS_PER_DAY = 30
MEMBER_DAYS = 7
MAX_RESOURCES = 8
LABEL_MAX = 40

MODS, APPROVED, MEMBERS = "mods", "approved", "members"
LEVELS = (MODS, APPROVED, MEMBERS)
LEVEL_LABELS = {MODS: "Guildmasters", APPROVED: "Guildmasters and approved members",
                MEMBERS: f"Any member who joined {MEMBER_DAYS}+ days ago"}

LINK, WIKI, POST = "link", "wiki", "post"
RESOURCE_KINDS = (LINK, WIKI, POST)

CANT_EDIT = "You can't edit this page."
INDEX = "index"

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_SPACE = re.compile(r"\s+")


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_slug(value):
    """A page's address: lowercase letters, digits and dashes, SLUG_MAX characters. Spaces become dashes."""
    slug = re.sub(r"[\s_]+", "-", str(value or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not _SLUG.match(slug):
        raise Refused(f"A page's address is 1 to {SLUG_MAX} letters, digits or dashes.")
    return slug


def clean_title(value, slug):
    title = _SPACE.sub(" ", str(value or "")).strip()
    if not title:
        title = slug.replace("-", " ").capitalize()
    if len(title) > TITLE_MAX:
        raise Refused(f"A title can be at most {TITLE_MAX} characters.")
    if any(c in title for c in "<>") or any(ord(c) < 32 for c in title):
        raise Refused("A title is plain text.")
    return title


def clean_body(value):
    body = str(value or "").replace("\r\n", "\n").strip()
    if len(body) > WIKI_MAX:
        raise Refused(f"A page can be at most {WIKI_MAX:,} characters.")
    return body


def clean_reason(value):
    return _SPACE.sub(" ", str(value or "")).strip()[:REASON_MAX]


def clean_level(value):
    level = str(value or "").strip()
    if level not in LEVELS:
        raise Refused("Choose who may edit the page.")
    return level


def edit_refusal(*, level, locked, is_content_gm, is_contributor, member_days, exiled, suspended, bot,
                 blocked_with_founder, edits_today):
    """Why this person can't edit the page now, or None. `member_days`: how long ago they joined (None: not a member)."""
    if suspended or bot or exiled or blocked_with_founder:
        return CANT_EDIT
    if is_content_gm:
        return None
    if locked or level == MODS:
        return "Only the guild's guildmasters can edit this page."
    if level == APPROVED and not is_contributor:
        return "Only the guild's guildmasters and approved members can edit this page."
    if level == MEMBERS and not is_contributor and (member_days is None or member_days < MEMBER_DAYS):
        return f"Members can edit this page {MEMBER_DAYS} days after joining the guild."
    if edits_today >= EDITS_PER_DAY:
        return f"You can make {EDITS_PER_DAY} wiki edits a day in a guild. Try again tomorrow."
    return None


def page_refusal(pages_now):
    return f"A guild can have at most {MAX_PAGES} wiki pages." if pages_now >= MAX_PAGES else None


def diff(old, new):
    """The lines that changed between two revisions, as (kind, text): kind "same", "added" or "removed". Plain text;
    the template escapes it and marks each line with a sign as well as a colour."""
    out = []
    for line in difflib.ndiff(str(old or "").splitlines(), str(new or "").splitlines()):
        code, rest = line[:2], line[2:]
        if code == "  ":
            out.append(("same", rest))
        elif code == "+ ":
            out.append(("added", rest))
        elif code == "- ":
            out.append(("removed", rest))
    return out


# --- pinned resources -------------------------------------------------------------------------------------------------

def clean_label(value):
    label = _SPACE.sub(" ", str(value or "")).strip()
    if not label:
        raise Refused("Give the resource a label.")
    if len(label) > LABEL_MAX:
        raise Refused(f"A label can be at most {LABEL_MAX} characters.")
    if any(c in label for c in "<>") or any(ord(c) < 32 for c in label):
        raise Refused("A label is plain text.")
    return label


def clean_resource_target(kind, value):
    """(kind, target): a link (checked like a profile's website), a wiki page's slug, or a post's base 36 id."""
    if kind not in RESOURCE_KINDS:
        raise Refused("Choose a link, a wiki page or a post.")
    value = str(value or "").strip()
    if kind == LINK:
        from ruqqus.helpers import profile_details
        try:
            url = profile_details.clean_website(value)
        except profile_details.Refused as e:
            raise Refused(str(e).replace("The website", "The link").replace("website", "link"))
        if not url:
            raise Refused("Give the link's address.")
        return kind, url
    if kind == WIKI:
        return kind, clean_slug(value)
    value = value.lower()
    found = re.search(r"/post/([0-9a-z]{1,10})(?:[/?#]|$)", value) or re.fullmatch(r"([0-9a-z]{1,10})", value)
    if not found:
        raise Refused("Give the post's address or id.")
    return kind, found.group(1)


def resources_refusal(count):
    return f"A guild can have at most {MAX_RESOURCES} resources." if count >= MAX_RESOURCES else None
