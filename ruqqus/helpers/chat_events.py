"""What the chat appservice webhook may count as a new message.

Reactions and edits are Matrix events of their own that point at an earlier
message. In an encrypted room they reach the server as `m.room.encrypted` like any
message, but their `m.relates_to` (the relation type and the target event, never the
emoji or the new text) stays readable. That is enough to tell them apart: counting
them would show an unread badge and bump the conversation each time someone reacts to
or edits a message.
"""

# relation types that annotate or amend a message rather than add one
NOT_NEW_MESSAGE_RELATIONS = ("m.annotation", "m.replace")


def is_new_message(event):
    """True for a message (or an encrypted event that may be one); False for a reaction or an edit."""
    content = event.get("content") or {}
    relation = content.get("m.relates_to") or {}
    if not isinstance(relation, dict):
        return True
    return relation.get("rel_type") not in NOT_NEW_MESSAGE_RELATIONS
