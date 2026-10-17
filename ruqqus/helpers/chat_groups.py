"""Group chats (pure rules, no I/O; the SQL is helpers/chat_group_store.py, the routes routes/chat_groups.py).

A group is one end-to-end encrypted Matrix room, made by the site's bot exactly like a 1:1 chat, with up to
MAX_MEMBERS people. The bot holds every power that changes who is in it (invite, kick, state), so every change goes
through a route and these rules; members can only write, react, edit and delete their own messages.

- **Roles**: one owner (who made it), admins the owner names, members. Owner and admins add and remove people;
  an admin can't remove another admin or the owner. Only the owner names or drops admins. Anyone may leave; when
  the owner leaves, the longest-standing admin takes over, else the longest-standing member (`next_owner`). A group
  with nobody left is closed.
- **Who can be added** (`add_refusal`): not yourself, not a bot (bots never chat), not a deleted or suspended
  account, not someone in a block with the person adding them, either way, and not into a full group. A block is
  never named: every one of those says "@x can't be added to a group." (the name is the one typed).
- **Where an invitation lands**: in Inbox when the two follow each other in either direction, as a 1:1 chat would
  (`chat_permissions.can_add_to_group_directly`: the two follow each other), and they are in at once; otherwise in Requests, where they can read
  the group from their invitation on and accept or delete it.
- **A block doesn't change a group both people are already in**: removing someone would tell them about the block.
  The blocker's own client hides the messages of the people they blocked ("A message from someone you blocked").
- **Its name** is optional plain text (the display-name rules: one line, no `<`, `>`, `@` or invisible characters),
  rated by the word filter; a member whose filter hides it sees the members' names instead.
"""

from ruqqus.helpers import display_names

MAX_MEMBERS = 32
MAX_NAME_LEN = 50
GROUPS_PER_DAY = 10          # groups one account may start in a day
ADDS_PER_DAY = 100           # people one account may add to groups in a day

OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ROLES = (OWNER, ADMIN, MEMBER)
INVITED, JOINED = "invited", "joined"
INBOX, REQUEST = "inbox", "request"

CANT_ADD = "@{name} can't be added to a group."


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_name(value):
    """The name to store ("" for none)."""
    try:
        name = display_names.clean(value)
    except display_names.Refused as e:
        raise Refused(str(e).replace("A display name", "A group name"))
    if len(name) > MAX_NAME_LEN:
        raise Refused(f"A group name can be at most {MAX_NAME_LEN} characters.")
    return name


def may_manage(role):
    return role in (OWNER, ADMIN)


def add_refusal(adder_id, adder_role, target, blocked_either_way, already_in, size, typed_name=None):
    """None when `target` may be added, otherwise the message. `size` counts invited and joined members."""
    name = typed_name or (target.username if target is not None else "")
    if not may_manage(adder_role):
        return "Only the group's owner and admins can add people."
    if target is None:
        return f"No account called @{name}."
    if target.id == adder_id:
        return "You're already in this group."
    if already_in:
        return f"@{target.username} is already in this group."
    if size >= MAX_MEMBERS:
        return f"A group can have at most {MAX_MEMBERS} people."
    if getattr(target, "is_bot", False) or getattr(target, "is_deleted", False) \
            or getattr(target, "is_suspended", False) or blocked_either_way:
        return CANT_ADD.format(name=target.username)
    return None


def remove_refusal(actor_id, actor_role, target_id, target_role):
    if target_id == actor_id:
        return "Use Leave to leave the group."
    if target_role is None:
        return "They aren't in this group."
    if actor_role == OWNER:
        return None
    if actor_role == ADMIN and target_role == MEMBER:
        return None
    if actor_role == ADMIN:
        return "Only the owner can remove an admin."
    return "Only the group's owner and admins can remove people."


def role_refusal(actor_role, target_id, target_role, new_role, actor_id):
    if actor_role != OWNER:
        return "Only the owner can choose the admins."
    if target_id == actor_id or target_role == OWNER:
        return "The owner's role can't be changed."
    if target_role is None:
        return "They aren't in this group."
    if new_role not in (ADMIN, MEMBER):
        return "That isn't a role."
    return None


def tab_for_invite(follow_either_way):
    return INBOX if follow_either_way else REQUEST


def next_owner(members):
    """Who takes over when the owner leaves: the earliest admin to join, else the earliest member. `members` are
    the remaining (user_id, role, status, joined_utc) rows; only joined people can take over. None: nobody left."""
    joined = [m for m in members if m[2] == JOINED]
    for wanted in (ADMIN, MEMBER):
        candidates = sorted((m for m in joined if m[1] == wanted), key=lambda m: (m[3], m[0]))
        if candidates:
            return candidates[0][0]
    return None


def label(name, name_hidden, member_names, total):
    """What the chat list and the header call a group: its name, or the first members' names."""
    if name and not name_hidden:
        return name
    names = [n for n in member_names if n][:3]
    if not names:
        return "Group chat"
    others = total - len(names)
    text = ", ".join(names)
    if others > 0:
        text += f" and {others} other{'s' if others != 1 else ''}"
    return text
