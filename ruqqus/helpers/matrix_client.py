"""Thin wrapper around the Matrix Client-Server + Application-Service
APIs, called server-side only. The Application Service token
(MATRIX_AS_TOKEN) lets this backend create/masquerade-as any user in its
registered namespace (@<prefix><user_id>:<server_name>) without ever
creating or knowing a password for that user - see the "?user_id="
masquerading query param on every call below, per the AS spec.

Never log request/response bodies here - they can contain access
tokens. Never persist an access token returned by login_as(); the
browser is the sole owner/persistence point for its own token+device_id
(localStorage) - see ruqqus/templates/chat.html's bundle boot.
"""

import requests
from ruqqus.__main__ import app


class MatrixError(Exception):
    pass


def _as_headers():
    return {"Authorization": f"Bearer {app.config['MATRIX_AS_TOKEN']}"}


def mxid_for(user_id: int) -> str:
    return f"@{app.config['MATRIX_USER_PREFIX']}{user_id}:{app.config['MATRIX_SERVER_NAME']}"


def provision_user(user_id: int) -> str:
    """Idempotent: Synapse returns 400 M_USER_IN_USE if already
    registered, which we treat as success (the user already exists)."""
    mxid = mxid_for(user_id)
    localpart = f"{app.config['MATRIX_USER_PREFIX']}{user_id}"
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/register",
        json={"type": "m.login.application_service", "username": localpart},
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code == 400 and resp.json().get("errcode") == "M_USER_IN_USE":
        return mxid
    if resp.status_code != 200:
        raise MatrixError(f"provision_user failed: {resp.status_code}")
    return mxid


def login_as(mxid: str, device_id: str = None) -> dict:
    """Mints a fresh access token for mxid. If device_id already exists
    for that user, Synapse reassociates a new token with that SAME
    device rather than creating a new one - this is what keeps
    per-browser device count stable across repeated calls from the same
    browser. Returns {"access_token", "device_id", "user_id"}."""
    body = {
        "type": "m.login.application_service",
        "identifier": {"type": "m.id.user", "user": mxid},
    }
    if device_id:
        body["device_id"] = device_id
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/login",
        json=body, headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"login_as failed: {resp.status_code}")
    return resp.json()


def create_dm_room(initiator_mxid: str, invitee_mxid: str) -> str:
    """The Application Service's own bot user (not either real user)
    creates the room, so it - not whichever human happened to click
    "Message" first - holds creator-level power. That's what lets
    ban_user_as_bot() later remove either participant on a block
    regardless of who started the conversation. The bot stays joined but
    silent/invisible in the UI, which only ever renders "the other user".

    history_visibility='invited' lets an invited-but-not-joined recipient
    (a pending Request) peek the triggering message before accepting,
    without granting them full room history access."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/createRoom",
        json={
            "preset": "private_chat",
            "visibility": "private",
            "is_direct": True,
            "invite": [initiator_mxid, invitee_mxid],
            "initial_state": [
                {"type": "m.room.encryption",
                 "content": {"algorithm": "m.megolm.v1.aes-sha2"}},
                {"type": "m.room.history_visibility",
                 "content": {"history_visibility": "invited"}},
            ],
        },
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"create_dm_room failed: {resp.status_code}")
    room_id = resp.json()["room_id"]

    join_room_as(room_id, initiator_mxid)
    return room_id


def join_room_as(room_id: str, mxid: str):
    """AS-masqueraded join, server-side - used both to put the initiator
    into the room they just had the bot create, and to auto-accept an
    allowed contact (either-direction follow) straight into Inbox with no
    visible invite step for the recipient."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/join/{room_id}"
        f"?user_id={mxid}",
        json={}, headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"join_room_as failed: {resp.status_code}")


def ban_user_as_bot(room_id: str, target_mxid: str):
    """Removes target_mxid from the room and prevents it rejoining - the
    enforcement half of blocking (see chat_permissions.on_block_created).
    Done as the AS's own sender/bot identity (no ?user_id= masquerade),
    which always holds creator-level power in every chat room regardless
    of which of the two real participants it is being used against."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/ban",
        json={"user_id": target_mxid, "reason": "Blocked by the other participant"},
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"ban_user_as_bot failed: {resp.status_code}")
