"""The notifications page: what each notification is, which ones are one row, and the controls that go with a row. Pure:
no Flask, no database (tests/test_notification_groups.py); helpers/notification_feed.py reads the rows and hands them over.

A `notifications` row only says "this comment (or post) is for you". What it is (a comment on your post, on a post you
follow, on your comment, a mention, a post from an account you follow, a bell notice, any other notice) is worked out here
from the comment or post it points at, so every notification already stored is bundled too, with no migration.

* **Kinds**: COMMENT_ON_POST (a top-level comment on a post: yours, one you follow, or one you have since stopped following,
  so unfollowing from the menu never turns old notifications into "mentions"; `post_state` says which), COMMENT_ON_COMMENT (a
  comment under yours), MENTION (a comment that names you and reached you no other way), POST (a post from an account you follow
  with its bell on), BELL_ACCOUNT and BELL_GUILD (the bells' notices, told apart by their stored message: helpers/bell.py)
  and SYSTEM (every other notice: a tip, a moderation notice, a welcome).
* **What bundles**: the kinds in BUNDLED, by where they come from: the post, the comment, the account, the guild. A mention
  or a system notice is its own row (x.com bundles likes and reposts, not mentions). **New and read never bundle together**:
  "Alex and 39 others commented" must not say 39 when 38 were seen last week.
* **Who is an actor**: an anonymous author is never named and never gets controls; each anonymous item counts as its own
  person (two anonymous comments are not known to be one).
* **Controls** (`controls`): the "..." menu's items for a row, drawn only when they apply. The person responsible for a post
  cannot unfollow it, so a comment on your own post offers no Unfollow; the bell and follow items need the account followed;
  the guild's bell and Leave need a membership. Each control is a pair (on / off) the page flips after it works, so it can be
  undone from the same menu. Names reach a URL only when they look like a name.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from ruqqus.helpers import bell

COMMENT_ON_POST = "comment_on_post"
COMMENT_ON_COMMENT = "comment_on_comment"
MENTION = "mention"
POST = "post"
BELL_ACCOUNT = "bell_account"
BELL_GUILD = "bell_guild"
SYSTEM = "system"
KINDS = (COMMENT_ON_POST, COMMENT_ON_COMMENT, MENTION, POST, BELL_ACCOUNT, BELL_GUILD, SYSTEM)
BUNDLED = (COMMENT_ON_POST, COMMENT_ON_COMMENT, POST, BELL_ACCOUNT, BELL_GUILD)

SHOWN_ACTORS = 3            # avatars in a bundle's stack
_NAME = re.compile(r"^[A-Za-z0-9_]{1,25}$")
_POST = re.compile(r"^[A-Za-z0-9]{1,16}$")


@dataclass(frozen=True)
class Actor:
    """Who did it, as the viewer may be told."""
    name: str                       # the handle, without @ ("" for an anonymous author)
    avatar: str = ""
    link: str = ""
    anonymous: bool = False

    @property
    def identity(self):
        return self.name.lower()


@dataclass
class Item:
    """One `notifications` row, classified."""
    id: int                         # notifications.id: the order of the page
    read: bool
    kind: str
    key: tuple                      # what bundles it (a post, a comment, an account, a guild, or the row itself)
    actor: Actor = None
    created_utc: int = 0
    ref: object = None              # the comment or post it points at, for the template to draw
    post_id: int = None
    post_ref: str = None            # the post's address id (base36), for the follow controls
    post_state: str = None          # the viewer's relation to it: "own", "following" or "can_follow"
    guild: str = ""                 # a guild bell notice's guild
    account: str = ""               # a bell notice's, or a post's, account (the actor's handle)
    excerpt: str = ""               # the plain-text start of the comment or post
    link: str = ""                  # where this one is
    bundle_link: str = ""           # where a bundle of them is (the post, the parent comment, the account, the guild)


def classify(*, source, system=False, msgid=None, params=None, level=1, parent_id=None, parent_by_viewer=False,
             post_id=None, post_state=None, mentions_viewer=False, author_id=None, item_id=0):
    """(kind, key) for one notification. `source` is "comment" or "post"; the rest describes what it points at."""
    if source == "post":
        return POST, ("account", author_id)
    if system:
        params = params or {}
        if msgid in bell.ACCOUNT_NOTICE_IDS and params.get("name"):
            return BELL_ACCOUNT, ("account", str(params["name"]).lower())
        if msgid in bell.GUILD_NOTICE_IDS and params.get("guild"):
            return BELL_GUILD, ("guild", str(params["guild"]).lower())
        return SYSTEM, ("row", item_id)
    if level > 1 and parent_by_viewer and parent_id:
        return COMMENT_ON_COMMENT, ("comment", parent_id)
    if level == 1 and post_id and (post_state in ("own", "following") or not mentions_viewer):
        return COMMENT_ON_POST, ("post", post_id)
    return MENTION, ("row", item_id)


@dataclass
class Group:
    """One row of the page: one or more notifications from the same place, newest first."""
    kind: str
    key: tuple
    items: list = field(default_factory=list)
    menu: list = field(default_factory=list)        # the "..." menu's controls, set by helpers/notification_feed.py

    @property
    def newest(self):
        return self.items[0]

    @property
    def count(self):
        return len(self.items)

    @property
    def unread(self):
        return any(not i.read for i in self.items)

    @property
    def actors(self):
        """The distinct actors, newest first. An anonymous author is a person of their own for each item."""
        seen, out = set(), []
        for i in self.items:
            if i.actor is None:
                continue
            ident = ("anon", i.id) if i.actor.anonymous else i.actor.identity
            if ident not in seen:
                seen.add(ident)
                out.append(i.actor)
        return out

    @property
    def shown(self):
        return self.actors[:SHOWN_ACTORS]

    @property
    def names(self):
        """The one or two actors the sentence names; `rest` is how many more there are ("Alex, Sam and 3 others")."""
        return self.actors[:2]

    @property
    def rest(self):
        return max(0, len(self.actors) - 2)

    @property
    def sole(self):
        """The one actor, when there is exactly one and they may be named; else None."""
        actors = self.actors
        return actors[0] if len(actors) == 1 and not actors[0].anonymous and actors[0].name else None


def group(items):
    """Bundle `items` (newest first) into rows, in the order of each row's newest item."""
    out, index = [], {}
    for item in items:
        if item.kind in BUNDLED:
            full = (item.kind, item.key, item.read)
            found = index.get(full)
            if found is None:
                found = index[full] = Group(item.kind, item.key)
                out.append(found)
            found.items.append(item)
        else:
            out.append(Group(item.kind, (item.kind, item.id, item.read), [item]))
    return out


# --- the "..." menu ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Control:
    action: str                     # post-follow, user-bell, user-follow, user-mute, guild-bell, guild-member, guild-mute
    target: str                     # the post's id, the account's or the guild's name
    on: bool                        # the state it is drawn in (following, bell on, muted ...)
    url_on: str                     # what clicking does while `on`
    url_off: str                    # and while not
    field: str = ""                 # the form field the mute routes read (the name goes in it)

    @property
    def toggles(self):
        return self.url_on == self.url_off


def _q(name):
    return quote(name, safe="")


def _post_follow(post_ref, post_state):
    """Unfollow while you follow it; Follow once you have stopped (the person responsible for a post cannot unfollow)."""
    if post_state in ("following", "can_follow") and post_ref and _POST.match(post_ref):
        return [Control("post-follow", post_ref, post_state == "following", f"/api/post/{_q(post_ref)}/unfollow",
                        f"/api/post/{_q(post_ref)}/follow")]
    return []


def _account(name, follow, muted, with_follow=True):
    """Bell, Unfollow and Mute for an account the page names."""
    out = []
    if not name or not _NAME.match(name):
        return out
    if with_follow and follow and follow.get("following"):
        out.append(Control("user-bell", name, bool(follow.get("bell")), f"/@{_q(name)}/toggle_bell", f"/@{_q(name)}/toggle_bell"))
        out.append(Control("user-follow", name, True, f"/api/unfollow/{_q(name)}", f"/api/follow/{_q(name)}"))
    out.append(Control("user-mute", name, bool(muted), "/settings/unmute", "/settings/mute", field="username"))
    return out


def _guild(name, info):
    out = []
    if not name or not _NAME.match(name):
        return out
    info = info or {}
    if info.get("member"):
        out.append(Control("guild-bell", name, bool(info.get("bell")), f"/+{_q(name)}/toggle_bell", f"/+{_q(name)}/toggle_bell"))
        out.append(Control("guild-member", name, True, f"/api/unsubscribe/{_q(name)}", f"/api/subscribe/{_q(name)}"))
    out.append(Control("guild-mute", name, bool(info.get("mute")), "/settings/unmute_guild", "/settings/mute_guild", field="board"))
    return out


def controls(grp, state):
    """The menu items for a row. `state` holds what the viewer has already set: {"follow": {name: {"following", "bell"}},
    "mute": {names}, "guild": {name: {"member", "bell", "mute"}}}, every key by lower-case name."""
    follow = state.get("follow", {})
    muted = state.get("mute", set())
    guilds = state.get("guild", {})
    first = grp.newest
    sole = grp.sole

    def by_name(actor_name, with_follow=True):
        low = actor_name.lower()
        return _account(actor_name, follow.get(low), low in muted, with_follow)

    if grp.kind == COMMENT_ON_POST:
        return _post_follow(first.post_ref, first.post_state) + (by_name(sole.name, with_follow=False) if sole else [])
    if grp.kind in (COMMENT_ON_COMMENT, MENTION):
        return by_name(sole.name, with_follow=False) if sole else []
    if grp.kind in (POST, BELL_ACCOUNT):
        if first.actor is not None and first.actor.anonymous:
            return []
        return by_name(first.account) if first.account else []
    if grp.kind == BELL_GUILD:
        low = first.guild.lower()
        return _guild(first.guild, guilds.get(low))
    return []
