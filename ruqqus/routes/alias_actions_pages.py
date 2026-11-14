"""Exile and chat-mute an alias: a guildmaster acts on "Anonymous #3" without learning who it is (the Log plan's decision 24; slice D3f).

The rules and what each part is for are in `helpers/alias_actions.py`. The routes:
* `POST /mod/exile_alias/<guild>` and `POST /mod/chatban_alias/<guild>` take `thing` (an anonymous post or comment made in this guild): the SERVER maps it to
  the item's author and applies the ordinary exile or chat mute, marked with the thread and number. **Every outcome answers the same** (`ACCEPTED`): a success, an
  alias that can't be acted on (a guildmaster, an admin, an approved contributor, the guildmaster's own, one already exiled or muted, one with no number) and an item
  that isn't anonymous or isn't from this guild, so the answer tells nothing. 20 an hour per guildmaster.
* `POST /mod/unexile_alias/<guild>/<ban id>` and `POST /mod/unchatban_alias/<guild>/<ban id>` lift one from the lists, which draw the alias.

Never copy `mod_ban_bid_user`'s logic wholesale: this applies the same ban (the ban row, the exile notice, the channel rooms) and no more."""
import time

from flask import abort, g, jsonify, redirect, request

from ruqqus.__main__ import app, limiter, member_limit_key
from ruqqus.classes import BanRelationship, ChatBan, Comment, ModAction, Submission, User
from ruqqus.helpers import action_log, alias_actions, exposure, log_notices, sanction_evidence
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.get import get_from_fullname
from ruqqus.helpers.i18n_runtime import N_, msg, tr
from ruqqus.helpers.wrappers import auth_required, is_guildmaster, validate_formkey


def _accepted():
    return jsonify({"message": msg(alias_actions.ACCEPTED)})


def _needs_reason():
    """A 400 when the form has no reason (the owner, 2026-11-14: every exile and chat mute needs one). Asked FIRST, before the target is looked at, so the
    refusal says nothing about anyone: it is the same whatever `thing` names."""
    if not action_log.clean_reason(request.form.get("reason")):
        return jsonify({"error": tr("A reason is required.")}), 400
    return None


def _target(board, v):
    """`(user, item, thread post id, number)` for what the guildmaster named, or None when there is nothing to act on, for any reason: the answer is the same."""
    thing = request.values.get("thing") or ""
    item = get_from_fullname(thing, v=v, graceful=True) if "_" in thing else None
    if not isinstance(item, (Submission, Comment)):
        return None
    found = alias_actions.resolve(g.db, board.id, item)
    if found is None:
        return None
    user_id, thread, number = found
    user = g.db.query(User).filter_by(id=user_id).first()
    if user is None or user.is_deleted or user.id == v.id:
        return None
    # what an exile by name refuses too, said to nobody: another guildmaster, an admin, an approved contributor
    if board.has_mod(user) or board.has_contributor(user) or (user.admin_level or 0) >= 3:
        return None
    return user, item, thread, number


def _logged(kind, board, v, item, number, user, *, with_item):
    """The guild's mod-log row and its entry for an action on an alias: no account in the row, the number in the Log, the account for the staff and the appeal only."""
    label = alias_actions.label(number)
    ma = ModAction(kind=kind, user_id=v.id, board_id=board.id, note=label,
                   target_submission_id=item.id if with_item and isinstance(item, Submission) else None,
                   target_comment_id=item.id if with_item and isinstance(item, Comment) else None)
    g.db.add(ma)
    g.db.flush()
    return action_log.annotate(g.db, ma.id, rule=request.form.get("rule"), reason=request.form.get("reason"), alias_n=number,
                               subject_user_id=user.id, staff_note=f"by alias, the account is @{user.username}")


@app.route("/mod/exile_alias/<guildname>", methods=["POST"])
@limiter.limit(alias_actions.PER_HOUR, key_func=member_limit_key)
@auth_required
@is_guildmaster("access")
@validate_formkey
def mod_exile_alias(guildname, board, v):
    """Exile the author of an anonymous post or comment, by the number it is drawn with. Needs a `reason`; the anonymous item named by `thing` is
    what the exile cites, and the site keeps a private copy of it as it was (helpers/sanction_evidence.py)."""
    refused = _needs_reason()
    if refused:
        return refused
    found = _target(board, v)
    if found is None:
        return _accepted()
    user, item, thread, number = found
    active = g.db.query(BanRelationship).filter_by(user_id=user.id, board_id=board.id, is_active=True).first()
    if active:
        if not active.alias_n and active.banning_mod_id == v.id:
            exposure.note(g.db, board.id, v.id, user.id, exposure.ALIAS_AFTER_EXILE)     # the same guildmaster exiled this account by name: a guess tested the other way round
        return _accepted()                                      # already exiled, by name or by alias: nothing changes, and nothing is said
    existing = g.db.query(BanRelationship).filter_by(user_id=user.id, board_id=board.id, is_active=False).first()
    now = int(time.time())
    note = action_log.clean_reason(request.form.get("reason"))[:128]          # the exile list's Reason column
    if existing is not None:
        existing.is_active, existing.created_utc, existing.banning_mod_id = True, now, v.id
        existing.alias_post_id, existing.alias_n, existing.mod_note = thread, number, note
        g.db.add(existing)
    else:
        g.db.add(BanRelationship(user_id=user.id, board_id=board.id, banning_mod_id=v.id, is_active=True, alias_post_id=thread, alias_n=number,
                                 mod_note=note))
    link = item.permalink
    notify(user, N_("You have been exiled from +%(guild)s.\n\nNone of your existing posts or comments have been removed, however, you will not be able to forward "
                    "posts to +%(guild)s or comment in it.\n\nYou were exiled for [this item](%(link)s)."), guild=board.name, link=link)
    entry = _logged(alias_actions.EXILE, board, v, item, number, user, with_item=True)
    sanction_evidence.keep(g.db, entry, board.id, user.id, [item])          # the anonymous item it cites, as it was: read through the copy pages only
    g.db.commit()
    if entry:
        log_notices.tell_appeal_way(g.db, entry, user)
        log_notices.mark_sent(g.db, entry)
        g.db.commit()
    from ruqqus.helpers import guild_channel_access
    guild_channel_access.remove_from_rooms(g.db, board.id, user.id, drop_roles=True)
    g.db.commit()
    return _accepted()


@app.route("/mod/unexile_alias/<guildname>/<int:ban_id>", methods=["POST"])
@auth_required
@is_guildmaster("access")
@validate_formkey
def mod_unexile_alias(guildname, ban_id, board, v):
    """Lift an exile that was made by alias, from the list that draws it as the alias."""
    ban = g.db.query(BanRelationship).filter_by(id=ban_id, board_id=board.id, is_active=True).first()
    if ban is None or not ban.alias_n:
        abort(404)                                              # an exile by name has its own route, and no number here
    ban.is_active = False
    g.db.add(ban)
    user = g.db.query(User).filter_by(id=ban.user_id).first()
    _logged(alias_actions.UNEXILE, board, v, None, ban.alias_n, user, with_item=False)
    g.db.commit()
    log_notices.tell_lifted(g.db, user.id, log_notices.EXILE_LIFTED, actor_id=v.id, guild=board.name)       # to the account: it says what it can do again, never the number
    return "", 204


@app.route("/mod/chatban_alias/<guildname>", methods=["POST"])
@limiter.limit(alias_actions.PER_HOUR, key_func=member_limit_key)
@auth_required
@is_guildmaster("content")
@validate_formkey
def mod_chatban_alias(guildname, board, v):
    """Mute the author of an anonymous post or comment in the guild's channels, by the number it is drawn with. Needs a `reason`."""
    refused = _needs_reason()
    if refused:
        return refused
    found = _target(board, v)
    if found is None:
        return _accepted()
    user, item, thread, number = found
    muted = g.db.query(ChatBan).filter_by(user_id=user.id, board_id=board.id).first()
    if muted:
        if not muted.alias_n and muted.banning_mod_id == v.id:
            exposure.note(g.db, board.id, v.id, user.id, exposure.ALIAS_AFTER_MUTE)
        return _accepted()
    g.db.add(ChatBan(user_id=user.id, board_id=board.id, banning_mod_id=v.id, alias_post_id=thread, alias_n=number))
    entry = _logged(alias_actions.CHATBAN, board, v, item, number, user, with_item=True)
    g.db.commit()
    log_notices.tell_imposed(g.db, user.id, log_notices.MUTED, entry, guild=board.name, rule_key=action_log.parse_rule(request.form.get("rule")),
                             reason=request.form.get("reason"), actor_id=v.id)
    _leave_encrypted_rooms(board, user)
    return _accepted()


def _leave_encrypted_rooms(board, user):
    """The site can't stop a muted person writing in an encrypted room, so they leave it, as the mute by name does (routes/guild_channels.py)."""
    from ruqqus.helpers import guild_channel_store as store, matrix_client as matrix
    for cid, room in store.encrypted_rooms_of(g.db, board.id, user.id):
        try:
            matrix.kick_as_bot(room, matrix.mxid_for(user.id), "Muted in the guild's channels")
            store.leave(g.db, cid, user.id)
        except matrix.MatrixError:
            pass                                                # the sync step does it within minutes
    g.db.commit()


@app.route("/mod/unchatban_alias/<guildname>/<int:ban_id>", methods=["POST"])
@auth_required
@is_guildmaster("content")
@validate_formkey
def mod_unchatban_alias(guildname, ban_id, board, v):
    ban = g.db.query(ChatBan).filter_by(id=ban_id, board_id=board.id).first()
    if ban is None or not ban.alias_n:
        abort(404)
    user = g.db.query(User).filter_by(id=ban.user_id).first()
    number = ban.alias_n
    g.db.delete(ban)
    _logged(alias_actions.UNCHATBAN, board, v, None, number, user, with_item=False)
    g.db.commit()
    log_notices.tell_lifted(g.db, user.id, log_notices.MUTE_LIFTED, actor_id=v.id, guild=board.name)
    if request.args.get("back") == "channels":
        return redirect(f"/+{board.name}/mod/channels")             # from the channel manager's own list (a form, not a script)
    return "", 204
