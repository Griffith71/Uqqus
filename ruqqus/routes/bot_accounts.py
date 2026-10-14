"""Bot accounts (helpers/bots.py rules, helpers/bot_store.py SQL): Settings > Bots for the members who run them, and
the admins' queue at /admin/bots.

Every change is a POST with the form key. A member only ever changes a bot they own (`_owned`: anyone else's, or a
name that is not a bot, is a 404); admins (level 3) decide on applications and sources and may pause any bot. The
owner is told of every decision by a notification.
"""
import html
import secrets
import time

from flask import abort, g, make_response, redirect, render_template, request
from urllib.parse import quote

from ruqqus.classes import ClientAuth, OauthApp, User
from ruqqus.helpers import bot_store, bots, feed_fetch, feed_items
from ruqqus.helpers.alerts import send_notification
from ruqqus.helpers.get import get_user
from ruqqus.helpers.safe_fetch import FetchError
from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.helpers.wrappers import admin_level_required, auth_required, is_not_banned, validate_formkey
from ruqqus.__main__ import app


def _back(path, msg=None, error=None):
    query = f"?msg={quote(msg)}" if msg else f"?error={quote(error)}" if error else ""
    return redirect(path + query)


def _owned(name, v):
    """A bot this member runs, as (account, bots row). Anything else is a 404."""
    row = bot_store.bot_named(g.db, name or "")
    if row is None or row.owner_id != v.id or row.status == bots.REJECTED:
        abort(404)
    account = g.db.query(User).filter_by(id=row.user_id).first()
    if account is None or not account.is_bot:
        abort(404)
    return account, row


def _feed_title(url):
    """(title, error): read the feed once, now, so a mistyped address is caught while the member is still here."""
    try:
        title, _ = feed_fetch.preview(url)
    except (FetchError, feed_items.FeedError) as error:
        return None, f"{url}: {error.message}"
    return title or url, None


def _guild_names(raw, v):
    """(names, error): guilds the owner can name (a private guild they are not in reads as one that does not exist)."""
    from ruqqus.helpers import search_filters

    names, error = bots.parse_guild_names(raw)
    if error:
        return None, error
    found = []
    for name in names:
        board = search_filters.find_guild(name, v)
        if board is None:
            return None, f"No guild called +{name}."
        found.append(board.name)
    return found, None


def _tell(user_id, text):
    user = g.db.query(User).filter_by(id=user_id).first()
    if user is not None:
        send_notification(user, text)


# --- the member's own bots -----------------------------------------------------------------------------------

@app.get("/settings/bots")
@auth_required
def settings_bots(v):
    rows = bot_store.bots_of(g.db, v.id)
    refusal = bots.owner_refusal(v, bot_store.active_count(g.db, v.id))
    return render_template("settings_bots.html", v=v, bots_list=rows, refusal=refusal, rules=bots,
                           msg=request.args.get("msg"), error=request.args.get("error"))


@app.post("/settings/bots/apply")
@is_not_banned
@validate_formkey
def settings_bots_apply(v):
    now = int(time.time())
    refusal = bots.owner_refusal(v, bot_store.active_count(g.db, v.id), now)
    if refusal:
        return _back("/settings/bots", error=refusal)

    name, error = bots.parse_username(request.form.get("username"))
    purpose, purpose_error = bots.parse_purpose(request.form.get("purpose"))
    error = error or purpose_error
    if not error and get_user(name, graceful=True):
        error = "That name is taken."
    # feeds are optional: a bot that posts from its owner's own script (a bot key, e.g. for X) has none
    lines = [line for line in (request.form.get("sources") or "").splitlines() if line.strip()]
    if not error and len(lines) > bots.MAX_SOURCES:
        error = f"A bot can read at most {bots.MAX_SOURCES} feeds."
    urls, titles = [], []
    for line in lines if not error else []:
        url, error = bots.parse_feed_url(line, allow_local=feed_fetch.safe_fetch.local_allowed())
        if error:
            break
        title, error = _feed_title(url)
        if error:
            break
        if url not in urls:
            urls.append(url)
            titles.append(title)
    guilds = []
    if not error:
        guilds, error = _guild_names(request.form.get("forward_guilds"), v)
    if error:
        return _back("/settings/bots", error=error)

    account = User(username=name, original_username=name, passhash=bots.UNUSABLE_PASSWORD, tos_agreed_utc=now,
                   is_bot=True, bot_owner_id=v.id, creation_ip=None,
                   following_visibility=0, followers_visibility=0, subscribers_visibility=0, subscriptions_visibility=0)
    apply_user_severity(account)
    g.db.add(account)
    g.db.flush()
    bot_store.add_bot(g.db, account.id, v.id, name, purpose, guilds, now)
    for url, title in zip(urls, titles):
        source_id = bot_store.add_source(g.db, account.id, url, now)
        bot_store.record_check(g.db, source_id, 0, 0, title=title)
    g.db.commit()
    return _back(f"/settings/bots/{name}", msg="Thanks. An admin will look at your bot soon; you will get a notification.")


def _keys_of(bot_id):
    """A bot's keys: the OAuth grants made for the bot account (the owner's script uses one)."""
    return g.db.query(ClientAuth).filter_by(user_id=bot_id).order_by(ClientAuth.id).all()


def _bot_page(account, row, v, **extra):
    return render_template("settings_bot.html", v=v, bot=row, account=account, rules=bots,
                           sources=bot_store.sources_of(g.db, account.id), guilds=bot_store.guilds_of(row),
                           posts_today=bot_store.posts_today(g.db, account.id, bots.utc_day()),
                           keys=_keys_of(account.id),
                           apps=g.db.query(OauthApp).filter_by(author_id=v.id, is_banned=False).all(),
                           msg=request.args.get("msg"), error=request.args.get("error"), **extra)


@app.get("/settings/bots/<name>")
@auth_required
def settings_bot(name, v):
    account, row = _owned(name, v)
    return _bot_page(account, row, v)


# --- bot keys: the owner's own script posts as the bot (e.g. for X, which has no free feed) -------------------

# what a bot key may do: post, read, edit and delete the bot's own posts. Never vote, and never act as a guildmaster.
KEY_SCOPES = dict(scope_identity=True, scope_read=True, scope_create=True, scope_update=True, scope_delete=True,
                  scope_vote=False, scope_guildmaster=False)


@app.post("/settings/bots/<name>/keys/create")
@is_not_banned
@validate_formkey
def settings_bot_create_key(name, v):
    """A key for the bot account, made through one of the owner's own apps (Settings > Apps): a grant with a refresh
    token, shown this once. The script trades it for an hour's access token at POST /oauth/grant (grant_type=refresh)
    and posts through the API like any app; every bot rule in submit_post applies."""
    account, row = _owned(name, v)
    back = f"/settings/bots/{account.username}"
    if row.status not in (bots.APPROVED, bots.PAUSED):
        return _back(back, error="A bot gets keys once an admin has approved it.")
    if len(_keys_of(account.id)) >= bots.MAX_KEYS:
        return _back(back, error=f"A bot can have at most {bots.MAX_KEYS} keys. Revoke one first.")
    raw = request.form.get("app", "")
    owned_app = None
    if raw.isascii() and raw.isdigit():
        owned_app = g.db.query(OauthApp).filter_by(id=int(raw), author_id=v.id, is_banned=False).first()
    if owned_app is None:
        return _back(back, error="Choose one of your own apps (Settings > Apps).")
    refresh = secrets.token_urlsafe(128)[0:128]
    g.db.add(ClientAuth(oauth_client=owned_app.id, oauth_code=None, user_id=account.id, refresh_token=refresh,
                        access_token=None, access_token_expire_utc=0, **KEY_SCOPES))
    g.db.commit()
    page = make_response(_bot_page(account, row, v, new_key={"refresh_token": refresh, "client_id": owned_app.client_id,
                                                             "app_name": owned_app.app_name}))
    page.headers["Cache-Control"] = "no-store"
    return page


@app.post("/settings/bots/<name>/keys/<int:key_id>/revoke")
@auth_required
@validate_formkey
def settings_bot_revoke_key(name, key_id, v):
    account, row = _owned(name, v)
    key = g.db.query(ClientAuth).filter_by(id=key_id, user_id=account.id).first()
    if key is None:
        abort(404)
    g.db.delete(key)
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg="Revoked. A script using that key can't post any more.")


@app.post("/settings/bots/<name>/sources/add")
@is_not_banned
@validate_formkey
def settings_bot_add_source(name, v):
    account, row = _owned(name, v)
    back = f"/settings/bots/{account.username}"
    if bot_store.source_count(g.db, account.id) >= bots.MAX_SOURCES:
        return _back(back, error=f"A bot can read at most {bots.MAX_SOURCES} feeds.")
    url, error = bots.parse_feed_url(request.form.get("url"), allow_local=feed_fetch.safe_fetch.local_allowed())
    title = None
    if not error:
        title, error = _feed_title(url)
    if error:
        return _back(back, error=error)
    source_id = bot_store.add_source(g.db, account.id, url)
    if source_id is None:
        return _back(back, error="The bot already reads that feed.")
    bot_store.record_check(g.db, source_id, 0, 0, title=title)
    g.db.commit()
    return _back(back, msg="Added. It starts once an admin approves it.")


@app.post("/settings/bots/<name>/sources/<int:source_id>/remove")
@auth_required
@validate_formkey
def settings_bot_remove_source(name, source_id, v):
    account, row = _owned(name, v)
    if not bot_store.remove_source(g.db, account.id, source_id):
        abort(404)
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg="Removed. The bot no longer reads that feed.")


@app.post("/settings/bots/<name>/sources/<int:source_id>/restart")
@is_not_banned
@validate_formkey
def settings_bot_restart_source(name, source_id, v):
    """A source paused after too many failed reads starts again (an admin approved it before; that still holds)."""
    account, row = _owned(name, v)
    source = bot_store.source(g.db, source_id)
    if source is None or source.bot_id != account.id:
        abort(404)
    bot_store.set_source_paused(g.db, source_id, False)
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg="It will be read again within a few minutes.")


@app.post("/settings/bots/<name>/pause")
@auth_required
@validate_formkey
def settings_bot_pause(name, v):
    account, row = _owned(name, v)
    bot_store.set_running(g.db, account.id, False, "Paused by its owner.")
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg="Paused. Nothing is posted until you resume it.")


@app.post("/settings/bots/<name>/resume")
@is_not_banned
@validate_formkey
def settings_bot_resume(name, v):
    account, row = _owned(name, v)
    if not bot_store.set_running(g.db, account.id, True):
        return _back(f"/settings/bots/{account.username}",
                     error="An admin paused this bot; only an admin can start it again.")
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg="Running again.")


@app.post("/settings/bots/<name>/profile")
@is_not_banned
@validate_formkey
def settings_bot_profile(name, v):
    """The bot's bio (plain text: no links, so a bot's profile can't be used for link spam) and its guilds."""
    account, row = _owned(name, v)
    back = f"/settings/bots/{account.username}"
    guilds, error = _guild_names(request.form.get("forward_guilds"), v)
    if error:
        return _back(back, error=error)
    bio = bots.plain_text(request.form.get("bio"), bots.BIO_MAX)
    account.bio = bio
    account.bio_html = f"<p>{html.escape(bio)}</p>" if bio else ""
    apply_user_severity(account)
    g.db.add(account)
    bot_store.set_forward_guilds(g.db, account.id, guilds)
    g.db.commit()
    return _back(back, msg="Saved.")


@app.post("/settings/bots/<name>/delete")
@auth_required
@validate_formkey
def settings_bot_delete(name, v):
    """The bot stops for good. Its posts stay, under a deactivated account, like any account's."""
    account, row = _owned(name, v)
    bot_store.forget_bot(g.db, account.id)
    for key in _keys_of(account.id):                # its keys stop working too
        g.db.delete(key)
    account.is_deleted = True
    g.db.add(account)
    g.db.commit()
    return _back("/settings/bots", msg=f"@{account.username} has been switched off for good.")


# --- the admins' queue ---------------------------------------------------------------------------------------

@app.get("/admin/bots")
@admin_level_required(3)
def admin_bots(v):
    pending = bot_store.pending_bots(g.db)
    sources = {row.user_id: bot_store.sources_of(g.db, row.user_id) for row in pending}
    checked = None
    check_id = request.args.get("check", "")
    if check_id.isascii() and check_id.isdigit():
        source = bot_store.source(g.db, int(check_id))
        if source is not None:
            try:
                title, items = feed_fetch.preview(source.url)
                checked = {"id": source.id, "url": source.url, "title": title, "titles": items, "error": None}
            except (FetchError, feed_items.FeedError) as error:
                checked = {"id": source.id, "url": source.url, "title": None, "titles": [], "error": error.message}
    return render_template("admin/bots.html", v=v, pending=pending, sources=sources, checked=checked,
                           new_sources=bot_store.pending_sources(g.db), running=bot_store.running_bots(g.db),
                           guilds_of=bot_store.guilds_of, rules=bots, msg=request.args.get("msg"))


@app.post("/admin/bots/<int:uid>/approve")
@admin_level_required(3)
@validate_formkey
def admin_bot_approve(uid, v):
    row = bot_store.bot_of(g.db, uid)
    if row is None or not bot_store.review_bot(g.db, uid, bots.APPROVED, v.id, bots.plain_text(request.form.get("note"), bots.NOTE_MAX)):
        abort(404)
    g.db.commit()
    _tell(row.owner_id, f"Your bot [@{row.username}](/@{row.username}) was approved. It starts posting from its feeds "
                        "within a few minutes. Manage it in [Settings > Bots](/settings/bots).")
    return _back("/admin/bots", msg=f"@{row.username} approved.")


@app.post("/admin/bots/<int:uid>/reject")
@admin_level_required(3)
@validate_formkey
def admin_bot_reject(uid, v):
    row = bot_store.bot_of(g.db, uid)
    note = bots.plain_text(request.form.get("note"), bots.NOTE_MAX)
    if row is None or not bot_store.review_bot(g.db, uid, bots.REJECTED, v.id, note):
        abort(404)
    # the account never posted anything: it goes, so the name is free again (the row keeps the name and the note)
    account = g.db.query(User).filter_by(id=uid, is_bot=True).first()
    if account is not None:
        g.db.delete(account)
    g.db.commit()
    _tell(row.owner_id, f"Your application for the bot @{row.username} was not approved."
                        + (f" The admins said: {note}" if note else ""))
    return _back("/admin/bots", msg=f"@{row.username} rejected.")


@app.post("/admin/bots/sources/<int:source_id>/<decision>")
@admin_level_required(3)
@validate_formkey
def admin_bot_source(source_id, decision, v):
    if decision not in ("approve", "reject"):
        abort(404)
    source = bot_store.source(g.db, source_id)
    status = bots.APPROVED if decision == "approve" else bots.REJECTED
    if source is None or not bot_store.review_source(g.db, source_id, status, v.id):
        abort(404)
    row = bot_store.bot_of(g.db, source.bot_id)
    g.db.commit()
    if row is not None:
        _tell(row.owner_id, f"The feed {source.url} for your bot @{row.username} was "
                            + ("approved: it is read from now on." if status == bots.APPROVED else "not approved."))
    return _back("/admin/bots", msg="Done.")


@app.post("/admin/bots/<int:uid>/pause")
@admin_level_required(3)
@validate_formkey
def admin_bot_pause(uid, v):
    row = bot_store.bot_of(g.db, uid)
    reason = bots.plain_text(request.form.get("reason"), 300) or "Paused by the site's admins."
    if row is None or not bot_store.set_running(g.db, uid, False, reason, by_admin=True):
        abort(404)
    g.db.commit()
    _tell(row.owner_id, f"An admin paused your bot @{row.username}: {reason}")
    return _back("/admin/bots", msg=f"@{row.username} paused.")


@app.post("/admin/bots/<int:uid>/resume")
@admin_level_required(3)
@validate_formkey
def admin_bot_resume(uid, v):
    row = bot_store.bot_of(g.db, uid)
    if row is None or not bot_store.set_running(g.db, uid, True, by_admin=True):
        abort(404)
    g.db.commit()
    _tell(row.owner_id, f"Your bot @{row.username} is running again.")
    return _back("/admin/bots", msg=f"@{row.username} is running again.")


def bots_waiting():
    """The count beside Admin > Bots on the admin page."""
    return bot_store.waiting_count(g.db)


app.jinja_env.globals.update(bots_waiting=bots_waiting, bot_rules=bots)     # bot_rules: the limits /help/bots shows
