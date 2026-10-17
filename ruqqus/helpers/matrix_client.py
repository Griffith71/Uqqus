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


class OrphanMatrixAccount(MatrixError):
    """Synapse already has an account for a user id the site has no chat record of (audit D-07)."""


def provision_user(user_id: int, adopt_existing: bool = True) -> str:
    """Makes the member's Matrix account. Synapse answers 400 M_USER_IN_USE when it already exists: with
    `adopt_existing` that counts as success; without it, it is an OrphanMatrixAccount. Matrix ids come from user ids,
    so after the site's database is restored without Synapse's (or the other way round) a NEW member can be given the
    id of an old account that still sits in Synapse, with its rooms and the people who knew it. The caller passes
    adopt_existing=False when it has no record of the account, and chat is refused rather than hand it over."""
    mxid = mxid_for(user_id)
    localpart = f"{app.config['MATRIX_USER_PREFIX']}{user_id}"
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/register",
        json={"type": "m.login.application_service", "username": localpart},
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code == 400 and resp.json().get("errcode") == "M_USER_IN_USE":
        if adopt_existing:
            return mxid
        raise OrphanMatrixAccount(f"{mxid} exists in Synapse with no chat record on the site")
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
            # only the bot invites, kicks or changes the room: without this either person could quietly invite
            # a third account, which both clients would then encrypt to (audit M-02)
            "power_level_content_override": group_power_levels(),
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


def group_power_levels() -> dict:
    """Only the bot changes who is in a group, or anything else about the room (helpers/chat_groups.py): every
    change goes through a route and its rules. Members send messages, reactions, edits and their own deletions.

    The bot is the room's creator, so it is not listed: from room version 12 a creator has unlimited power and
    Synapse refuses a creator in `users`; before it, leaving `users` out keeps Synapse's own {creator: 100}."""
    return {
        "users_default": 0,
        "events_default": 0,
        "state_default": 100,
        "invite": 100,
        "kick": 100,
        "ban": 100,
        "redact": 100,              # deleting someone else's message; your own needs no power
        "events": {
            "m.room.name": 100, "m.room.avatar": 100, "m.room.topic": 100, "m.room.power_levels": 100,
            "m.room.history_visibility": 100, "m.room.encryption": 100, "m.room.join_rules": 100,
            "m.room.canonical_alias": 100, "m.room.tombstone": 100, "m.room.server_acl": 100,
        },
    }


def create_group_room(invitee_mxids) -> str:
    """A group's room: made by the bot (which stays in it, silent, as in a 1:1 chat), encrypted, invite-only,
    and each invited person can read it from their invitation on (history_visibility 'invited')."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/createRoom",
        json={
            "preset": "private_chat",
            "visibility": "private",
            "is_direct": False,
            "invite": list(invitee_mxids),
            "power_level_content_override": group_power_levels(),
            "initial_state": [
                {"type": "m.room.encryption", "content": {"algorithm": "m.megolm.v1.aes-sha2"}},
                {"type": "m.room.history_visibility", "content": {"history_visibility": "invited"}},
                {"type": "m.room.guest_access", "content": {"guest_access": "forbidden"}},
            ],
        },
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"create_group_room failed: {resp.status_code}")
    return resp.json()["room_id"]


def invite_as_bot(room_id: str, target_mxid: str):
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/invite",
        json={"user_id": target_mxid}, headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"invite_as_bot failed: {resp.status_code}")


def kick_as_bot(room_id: str, target_mxid: str, reason: str = ""):
    """Out of the room (they may be added again later: a kick, not a ban). Also withdraws an invitation."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/kick",
        json={"user_id": target_mxid, "reason": reason}, headers=_as_headers(), timeout=10,
    )
    if resp.status_code not in (200, 403):      # 403: they were not in it any more
        raise MatrixError(f"kick_as_bot failed: {resp.status_code}")


def leave_room_as(room_id: str, mxid: str):
    """AS-masqueraded leave: the member leaves, or turns down an invitation."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/leave?user_id={mxid}",
        json={}, headers=_as_headers(), timeout=10,
    )
    if resp.status_code not in (200, 403, 404):
        raise MatrixError(f"leave_room_as failed: {resp.status_code}")


def create_space_room() -> str:
    """A Space's room (helpers/spaces.py): nothing is ever written in it. Being in it together is how the browsers
    in a Space learn each other's devices (Matrix tells the members of a room when someone adds a device), which
    their encrypted signalling needs. Encrypted, so clients track its members' devices; only the bot changes it."""
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/createRoom",
        json={
            "preset": "private_chat",
            "visibility": "private",
            "power_level_content_override": group_power_levels(),
            "initial_state": [
                {"type": "m.room.encryption", "content": {"algorithm": "m.megolm.v1.aes-sha2"}},
                {"type": "m.room.history_visibility", "content": {"history_visibility": "joined"}},
                {"type": "m.room.guest_access", "content": {"guest_access": "forbidden"}},
            ],
        },
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"create_space_room failed: {resp.status_code}")
    return resp.json()["room_id"]


def put_in_room(room_id: str, mxid: str):
    """The bot invites, the member joins (an invitation to someone already in it is refused: that is fine)."""
    try:
        invite_as_bot(room_id, mxid)
    except MatrixError:
        pass
    join_room_as(room_id, mxid)


def lock_down_room(room_id: str):
    """Gives an existing room the group power levels (only the bot invites, kicks or changes the room), keeping the
    writers of a channel. For 1:1 rooms made before audit M-02 (scripts/lock_dm_rooms.py)."""
    url = f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels"
    current = requests.get(url, headers=_as_headers(), timeout=10)
    if current.status_code != 200:
        raise MatrixError(f"lock_down_room read failed: {current.status_code}")
    levels = group_power_levels()
    # members go back to 0; only a non-member entry at 100 (the bot, listed as creator in rooms before version 12)
    # is kept
    bot = f"@{app.config['MATRIX_BOT_LOCALPART']}:{app.config['MATRIX_SERVER_NAME']}"
    kept = {k: v for k, v in (current.json().get("users") or {}).items() if k == bot}
    if kept:
        levels["users"] = kept
    resp = requests.put(url, json=levels, headers=_as_headers(), timeout=10)
    if resp.status_code != 200:
        raise MatrixError(f"lock_down_room failed: {resp.status_code}")


def room_member_ids(room_id: str):
    """The Matrix ids joined or invited in a room (as the bot)."""
    resp = requests.get(f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/members",
                        headers=_as_headers(), timeout=10)
    if resp.status_code != 200:
        raise MatrixError(f"room_member_ids failed: {resp.status_code}")
    return [e.get("state_key") for e in resp.json().get("chunk", [])
            if (e.get("content") or {}).get("membership") in ("join", "invite")]


def channel_power_levels(writer_mxids) -> dict:
    """A broadcast channel (helpers/broadcasts.py): the owner and helpers write (50), members react and take back
    their own reactions (0); the bot (the creator) alone changes the room or who is in it."""
    levels = group_power_levels()
    levels["events_default"] = 50
    levels["users"] = {mxid: 50 for mxid in writer_mxids}
    levels["events"] = {**levels["events"], "m.reaction": 0, "m.room.redaction": 0}
    return levels


def create_channel_room(owner_mxid: str, encrypted: bool) -> str:
    state = [
        {"type": "m.room.history_visibility", "content": {"history_visibility": "shared"}},
        {"type": "m.room.guest_access", "content": {"guest_access": "forbidden"}},
    ]
    if encrypted:
        state.insert(0, {"type": "m.room.encryption", "content": {"algorithm": "m.megolm.v1.aes-sha2"}})
    resp = requests.post(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/createRoom",
        json={"preset": "private_chat", "visibility": "private", "invite": [owner_mxid],
              "power_level_content_override": channel_power_levels([owner_mxid]), "initial_state": state},
        headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"create_channel_room failed: {resp.status_code}")
    room_id = resp.json()["room_id"]
    join_room_as(room_id, owner_mxid)
    return room_id


def set_writers(room_id: str, writer_mxids):
    """Who may write in a channel: the room's power levels as they are, with exactly these people at 50 (anyone
    else at 50 goes back to 0; the creator and anyone above 50 are left alone)."""
    url = f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/state/m.room.power_levels"
    current = requests.get(url, headers=_as_headers(), timeout=10)
    if current.status_code != 200:
        raise MatrixError(f"set_writers read failed: {current.status_code}")
    levels = current.json()
    users = {k: v for k, v in (levels.get("users") or {}).items() if v != 50}
    users.update({mxid: 50 for mxid in writer_mxids})
    levels["users"] = users
    resp = requests.put(url, json=levels, headers=_as_headers(), timeout=10)
    if resp.status_code != 200:
        raise MatrixError(f"set_writers failed: {resp.status_code}")


def send_as(room_id: str, mxid: str, content: dict, txn_id: str) -> str:
    """A message in a room as one of its members (AS masquerade). Returns its event id."""
    resp = requests.put(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}?user_id={mxid}",
        json=content, headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"send_as failed: {resp.status_code}")
    return resp.json()["event_id"]


def redact_as_bot(room_id: str, event_id: str, txn_id: str, reason: str = ""):
    resp = requests.put(
        f"{app.config['MATRIX_INTERNAL_URL']}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{txn_id}",
        json={"reason": reason}, headers=_as_headers(), timeout=10,
    )
    if resp.status_code != 200:
        raise MatrixError(f"redact_as_bot failed: {resp.status_code}")
