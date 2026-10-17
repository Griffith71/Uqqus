"""Guild roles (pure rules, no I/O; the SQL is helpers/guild_role_store.py, the routes routes/guild_roles.py).

A role is a named, coloured group a guildmaster (with the config permission) gives to members of the guild. It carries
three permissions and, per channel, may allow or deny reading and writing over the channel's own ladder
(helpers/guild_channels.py):

- **Permissions** (`PERMS`): not slowed by slow mode, remove messages in the channels they may read, pin messages. A
  guildmaster's own permissions are theirs, not a role's: roles add to what a member can do, never to what a guildmaster
  can do to roles.
- **Overwrites** (`ALLOW`, `DENY`, `INHERIT` per channel and role, for reading and for writing): a person's roles are
  combined and **an allow beats a deny** (as Discord's role overwrites do); no overwrite leaves the channel's ladder as it
  is. An overwrite never gets round the absolute refusals (a guild they may not see, an exile, a bot): a role can't
  bring someone into a channel the guild has closed to them. Allowing a person to write lets them read.
- **Who may have one**: someone in the guild (joined, approved or a guildmaster), not exiled, not a bot, not a deleted
  account. Leaving the guild or being exiled takes a person's roles. Up to MAX_ROLES a guild, MAX_MEMBERS in a role.
- **Colours** are the flair palette (helpers/guild_flair.py), drawn by `.flair-chip` / `.flair-<colour>` in both
  stylesheets: never free CSS. A role's name is rated by the word filter and read as "A role" by someone whose filter
  hides it.
"""
from ruqqus.helpers import display_names, guild_flair

MAX_ROLES = 20
NAME_MAX = 30
MAX_MEMBERS = 500
SHOWN_MEMBERS = 50
COLOURS = guild_flair.COLOURS
DEFAULT_COLOUR = guild_flair.DEFAULT_COLOUR

PERMS = ("slow_exempt", "remove_messages", "pin")
PERM_LABELS = {"slow_exempt": "Not slowed by slow mode", "remove_messages": "Remove messages", "pin": "Pin messages"}

INHERIT, ALLOW, DENY = 0, 1, 2
OVERWRITE_LABELS = {INHERIT: "Channel's own rules", ALLOW: "Allow", DENY: "Deny"}


class Refused(ValueError):
    """Shown to the guildmaster as it is."""


def clean_name(value):
    try:
        name = display_names.clean(str(value or "").strip())
    except display_names.Refused as e:
        raise Refused(str(e).replace("A display name", "A role name"))
    if not name:
        raise Refused("Give the role a name.")
    if len(name) > NAME_MAX:
        raise Refused(f"A role name can be at most {NAME_MAX} characters.")
    return name


def clean_colour(value):
    try:
        return guild_flair.clean_colour(value or DEFAULT_COLOUR)
    except guild_flair.Refused:
        raise Refused("Choose one of the colours.")


def clean_perms(form_has):
    """{perm: bool} from a function that says whether the form ticked it."""
    return {perm: bool(form_has(f"perm_{perm}")) for perm in PERMS}


def make_refusal(count, name_taken):
    if count >= MAX_ROLES:
        return f"A guild can have at most {MAX_ROLES} roles."
    if name_taken:
        return "The guild already has a role with that name."
    return None


def rename_refusal(name_taken):
    return "The guild already has a role with that name." if name_taken else None


def parse_overwrite(value):
    """INHERIT, ALLOW or DENY from a form value."""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise Refused("Choose allow, deny or the channel's own rules.")
    if number not in (INHERIT, ALLOW, DENY):
        raise Refused("Choose allow, deny or the channel's own rules.")
    return number


def resolve(allow, deny):
    """What a person's roles say about one thing: True (allowed), False (denied) or None (nothing). Allow wins."""
    if allow:
        return True
    if deny:
        return False
    return None


def member_refusal(*, in_guild, exiled, bot, gone, count, already):
    """Why this person can't be given the role (None: they can)."""
    if gone:
        return "That account isn't available."
    if bot:
        return "Bots can't have roles."
    if exiled or not in_guild:
        return "They need to be in the guild, and not exiled from it."
    if already:
        return "They already have this role."
    if count >= MAX_MEMBERS:
        return f"A role can have at most {MAX_MEMBERS} people."
    return None
