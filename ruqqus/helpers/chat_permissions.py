"""Messaging permission rules, kept isolated from routes/templates so a
future per-user "who can message me" setting has exactly one place to
extend rather than scattered checks."""

from flask import g
import ruqqus.helpers.matrix_client as matrix_client


def can_message_directly(sender, recipient) -> bool:
    """True if sender's message to recipient should land straight in
    recipient's Inbox; False if it must become a Request.

    Current default (no per-user setting exists yet - see
    User.following_ids/friend_ids in ruqqus/classes/user.py): allowed if
    EITHER follows the other, in either direction. A future "who can
    message me" setting should be layered in here first (e.g. check
    recipient.chat_permission_mode), falling back to this default.
    """
    if sender.id == recipient.id:
        return False
    return (
        recipient.id in sender.following_ids
        or sender.id in recipient.following_ids
    )


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
