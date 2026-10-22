"""Messaging permission rules, kept isolated from routes/templates so a
future per-user "who can message me" setting has exactly one place to
extend rather than scattered checks."""

from flask import g
import ruqqus.helpers.matrix_client as matrix_client


def can_message_directly(sender, recipient) -> bool:
    """True if sender's message to recipient should land straight in recipient's Inbox; False if it must become a
    Request. **The recipient decides**: only when the recipient follows the sender (audit M-01: a stranger who
    followed someone used to land in their Inbox, put them in groups and ring them). Calls follow the same rule, since
    a call needs a chat both people have in their Inbox."""
    if sender.id == recipient.id:
        return False
    return sender.id in recipient.following_ids


def can_add_to_group_directly(adder, target) -> bool:
    """A group add joins someone at once only when the two follow each other; anyone else gets an invitation in
    their Requests ("@x added you to this group", Accept / Delete)."""
    if adder.id == target.id:
        return False
    return target.id in adder.following_ids and adder.id in target.following_ids


def is_blocked(sender, recipient) -> bool:
    return bool(sender.any_block_exists(recipient))


def on_block_created(blocker, blocked):
    """Enforcement half of blocking: /api/chat/start's is_blocked() check
    only stops a NEW conversation from being created - it does nothing
    about a conversation the two users already have, since messages in an
    existing room flow client-side straight to Matrix and never pass
    through that route. This bans the blocked user from that room outright
    so they can no longer send into it, satisfying "a blocked user must
    not be able to continue messaging the blocker." Best-effort: a
    Matrix-side hiccup here must never fail the underlying block action
    itself, since the DB block row (and is_blocked() gating future
    conversations) is the authoritative enforcement.
    """
    from ruqqus.classes import ChatConversation, ChatIdentity

    a_id, b_id = min(blocker.id, blocked.id), max(blocker.id, blocked.id)
    convo = g.db.query(ChatConversation).filter_by(user_a_id=a_id, user_b_id=b_id).first()
    if not convo:
        return

    identity = g.db.query(ChatIdentity).filter_by(user_id=blocked.id).first()
    if not identity:
        return

    try:
        matrix_client.ban_user_as_bot(convo.matrix_room_id, identity.matrix_user_id)
    except matrix_client.MatrixError:
        pass
    end_call_in(convo.matrix_room_id)


def on_block_removed(a, b):
    """The other half of on_block_created: once no block stands between the two either way, the bot lifts the ban
    and puts both back in their 1:1 room (bug hunt: nothing ever unbanned, so after an unblock the chat stayed broken
    for good: chat_start handed back a room one of them couldn't enter). Best effort, as the ban is."""
    from ruqqus.classes import ChatConversation, ChatIdentity

    if a.any_block_exists(b):
        return
    a_id, b_id = min(a.id, b.id), max(a.id, b.id)
    convo = g.db.query(ChatConversation).filter_by(user_a_id=a_id, user_b_id=b_id).first()
    if not convo:
        return
    for identity in g.db.query(ChatIdentity).filter(ChatIdentity.user_id.in_([a_id, b_id])).all():
        try:
            matrix_client.unban_user_as_bot(convo.matrix_room_id, identity.matrix_user_id)
        except matrix_client.MatrixError:
            pass                        # not banned (only the blocked one was)
        try:
            matrix_client.put_in_room(convo.matrix_room_id, identity.matrix_user_id)
        except matrix_client.MatrixError:
            pass


def end_call_in(room_id):
    """A call in this 1:1 chat ends at once (a block): both windows are told (helpers/call_store.py). Best effort,
    like the ban above: the block itself never fails because of it."""
    try:
        from ruqqus.__main__ import r
        from ruqqus.helpers import call_store, live_bus
        info = call_store.end(r, room_id) if r is not None else None
        if info:
            live_bus.publish(r, live_bus.topic_channel(f"call:{info['call_id']}"), "call.ended", {"reason": "ended"})
    except Exception:
        pass
