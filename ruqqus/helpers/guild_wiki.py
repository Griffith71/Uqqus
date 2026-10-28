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

from babel.support import LazyProxy

from ruqqus.helpers.i18n_runtime import number_text, tr, tr_lazy, trn

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
LEVEL_LABELS = {MODS: tr_lazy("Guildmasters"), APPROVED: tr_lazy("Guildmasters and approved members"),
                MEMBERS: LazyProxy(lambda: trn("Any member who joined %(num)s+ day ago", "Any member who joined %(num)s+ days ago",
                                               MEMBER_DAYS), enable_cache=False)}

LINK, WIKI, POST = "link", "wiki", "post"
RESOURCE_KINDS = (LINK, WIKI, POST)

CANT_EDIT = tr_lazy("You can't edit this page.")
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
        raise Refused(trn("A page's address is 1 to %(num)s letter, digit or dash.",
                           "A page's address is 1 to %(num)s letters, digits or dashes.", SLUG_MAX))
    return slug


def clean_title(value, slug):
    title = _SPACE.sub(" ", str(value or "")).strip()
    if not title:
        title = slug.replace("-", " ").capitalize()
    if len(title) > TITLE_MAX:
        raise Refused(trn("A title can be at most %(num)s character.", "A title can be at most %(num)s characters.", TITLE_MAX))
    if any(c in title for c in "<>") or any(ord(c) < 32 for c in title):
        raise Refused(tr("A title is plain text."))
    return title


def clean_body(value):
    body = str(value or "").replace("\r\n", "\n").strip()
    if len(body) > WIKI_MAX:
        raise Refused(trn("A page can be at most %(num)s character.", "A page can be at most %(num)s characters.", WIKI_MAX,
                           num=number_text(WIKI_MAX)))
    return body


def clean_reason(value):
    return _SPACE.sub(" ", str(value or "")).strip()[:REASON_MAX]


def clean_level(value):
    level = str(value or "").strip()
    if level not in LEVELS:
        raise Refused(tr("Choose who may edit the page."))
    return level


def edit_refusal(*, level, locked, is_content_gm, is_contributor, member_days, exiled, suspended, bot,
                 blocked_with_founder, edits_today):
    """Why this person can't edit the page now, or None. `member_days`: how long ago they joined (None: not a member)."""
    if suspended or bot or exiled or blocked_with_founder:
        return CANT_EDIT
    if is_content_gm:
        return None
    if locked or level == MODS:
        return tr("Only the guild's guildmasters can edit this page.")
    if level == APPROVED and not is_contributor:
        return tr("Only the guild's guildmasters and approved members can edit this page.")
    if level == MEMBERS and not is_contributor and (member_days is None or member_days < MEMBER_DAYS):
        return trn("Members can edit this page %(num)s day after joining the guild.",
                   "Members can edit this page %(num)s days after joining the guild.", MEMBER_DAYS)
    if edits_today >= EDITS_PER_DAY:
        return trn("You can make %(num)s wiki edit a day in a guild. Try again tomorrow.",
                   "You can make %(num)s wiki edits a day in a guild. Try again tomorrow.", EDITS_PER_DAY)
    return None


def page_refusal(pages_now):
    if pages_now < MAX_PAGES:
        return None
    return trn("A guild can have at most %(num)s wiki page.", "A guild can have at most %(num)s wiki pages.", MAX_PAGES)


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
        raise Refused(tr("Give the resource a label."))
    if len(label) > LABEL_MAX:
        raise Refused(trn("A label can be at most %(num)s character.", "A label can be at most %(num)s characters.", LABEL_MAX))
    if any(c in label for c in "<>") or any(ord(c) < 32 for c in label):
        raise Refused(tr("A label is plain text."))
    return label


def clean_resource_target(kind, value):
    """(kind, target): a link (checked like a profile's website), a wiki page's slug, or a post's base 36 id."""
    if kind not in RESOURCE_KINDS:
        raise Refused(tr("Choose a link, a wiki page or a post."))
    value = str(value or "").strip()
    if kind == LINK:
        from ruqqus.helpers import profile_details
        try:
            url = profile_details.clean_website(value, link=True)
        except profile_details.Refused as e:
            raise Refused(str(e))
        if not url:
            raise Refused(tr("Give the link's address."))
        return kind, url
    if kind == WIKI:
        return kind, clean_slug(value)
    value = value.lower()
    found = re.search(r"/post/([0-9a-z]{1,10})(?:[/?#]|$)", value) or re.fullmatch(r"([0-9a-z]{1,10})", value)
    if not found:
        raise Refused(tr("Give the post's address or id."))
    return kind, found.group(1)


def resources_refusal(count):
    if count < MAX_RESOURCES:
        return None
    return trn("A guild can have at most %(num)s resource.", "A guild can have at most %(num)s resources.", MAX_RESOURCES)
