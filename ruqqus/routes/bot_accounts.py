"""Bot accounts (helpers/bots.py rules, helpers/bot_store.py SQL): Settings > Bots, where members and admins propose
bots and manage the ones they may, and the admins' queue at /admin/bots.

Three kinds (helpers/bots.py): a SITE bot runs on free feeds and, once approved, belongs to nobody (the admins run it);
a MEMBER bot needs its member (a key, an API bill) and stays theirs; SELF is automated posting on the member's OWN
account, which they keep using as themselves. Who may change a bot is `bots.may_manage` (`_manageable`): an admin,
any bot; a member, their own member bot and their own account's automated posting, and a site bot they proposed while
it still waits. Anything else is a 404. Every change is a POST with the form key. Whoever is responsible for a bot
(`bots.responsible_id`) is told of every decision about it.

**A SELF row's account is a person's account**: nothing here deletes, deactivates, renames or edits its profile, and
its OAuth grants are the person's own, never a bot's keys (`_not_self`, `settings_bot_delete`).
"""
import html
import secrets
import time
import traceback

import gevent
from flask import abort, g, make_response, redirect, render_template, request
from urllib.parse import quote

from ruqqus.classes import ClientAuth, OauthApp, User
from ruqqus.helpers import bot_store, bots, feed_fetch, feed_items, profile_edit
from ruqqus.helpers.alerts import notify
from ruqqus.helpers.aws import check_csam_url
from ruqqus.helpers.get import get_user
from ruqqus.helpers.i18n_runtime import N_, tr, trn
from ruqqus.helpers.safe_fetch import FetchError
from ruqqus.helpers.word_filter_store import apply_user_severity
from ruqqus.helpers.wrappers import not_qr_limited, admin_level_required, auth_required, is_not_banned, validate_formkey
from ruqqus.__main__ import app, limiter, member_limit_key

# why a bot is paused when nobody gave a reason: stored with the bot (bot_store.set_running), so kept in English
PAUSED_BY_ADMINS = N_("Paused by the site's admins.")
PAUSED_BY_OWNER = N_("Paused by its owner.")

# notices to whoever is responsible for a bot: stored in English, drawn in each reader's language (alerts.notify)
NOTICE_PAUSED_SELF = N_("An admin paused your account's automated posting: %(reason)s")
NOTICE_PAUSED_BOT = N_("An admin paused your bot @%(name)s: %(reason)s")
NOTICE_RUNNING_SELF = N_("Your account's automated posting is running again.")
NOTICE_RUNNING_BOT = N_("Your bot @%(name)s is running again.")
NOTICE_SELF_OFF = N_("An admin switched off your account's automated posting. Your account and your posts are as "
                     "they were.")
NOTICE_APPROVED_SITE = N_("The bot you proposed, [@%(name)s](/@%(name)s), was approved. It is run by the site from now "
                          "on and starts posting from its feeds within a few minutes. Thank you!")
NOTICE_APPROVED_SELF = N_("Your account's automated posting was approved. Your feeds' new posts appear on your profile, "
                          "marked as automated, within a few minutes. Manage it in [Settings > Bots](/settings/bots).")
NOTICE_APPROVED_BOT = N_("Your bot [@%(name)s](/@%(name)s) was approved. It starts posting within a few minutes. "
                         "Manage it in [Settings > Bots](/settings/bots).")
NOTICE_REJECTED_SELF = N_("Automated posting for your account was not approved.")
NOTICE_REJECTED_SELF_NOTE = N_("Automated posting for your account was not approved. The admins said: %(note)s")
NOTICE_REJECTED_BOT = N_("The bot @%(name)s you proposed was not approved.")
NOTICE_REJECTED_BOT_NOTE = N_("The bot @%(name)s you proposed was not approved. The admins said: %(note)s")
NOTICE_FEED_APPROVED_SELF = N_("The feed %(url)s for your account's automated posting was approved: it is read from "
                               "now on.")
NOTICE_FEED_REJECTED_SELF = N_("The feed %(url)s for your account's automated posting was not approved.")
NOTICE_FEED_APPROVED_BOT = N_("The feed %(url)s for your bot @%(name)s was approved: it is read from now on.")
NOTICE_FEED_REJECTED_BOT = N_("The feed %(url)s for your bot @%(name)s was not approved.")


def _back(path, msg=None, error=None):
    query = f"?msg={quote(msg)}" if msg else f"?error={quote(error)}" if error else ""
    return redirect(path + query)


def _page(account):
    return f"/settings/bots/{account.username}"


def _manageable(name, v):
    """(account, bots row, admin?) for a bot this person may change. Anything else is a 404."""
    row = bot_store.bot_named(g.db, name or "")
    admin = bots.is_admin(v)
    if not bots.may_manage(row, v.id, admin):
        abort(404)
    account = g.db.query(User).filter_by(id=row.user_id).first()
    if account is None or account.is_deleted or not (account.is_bot or bots.is_self(row)):
        abort(404)
    return account, row, admin


def _not_self(row):
    """The routes that edit a bot ACCOUNT (its picture, banner, bio, display name, keys) are not for a person's own
    account: they edit their profile in Settings > Profile, and their own scripts use their own apps."""
    if bots.is_self(row):
        abort(404)


def _feed_title(url):
    """(title, error): read the feed once, now, so a mistyped address is caught while the member is still here."""
    try:
        title, _ = feed_fetch.preview(url)
    except (FetchError, feed_items.FeedError) as error:
        # Translators: a feed's address, then why it could not be read
        return None, tr("%(url)s: %(error)s", url=url, error=error.message)
    return title or url, None


def _guild_names(raw, v):
    """(names, error): guilds the person can name (a private guild they are not in reads as one that does not exist)."""
    from ruqqus.helpers import search_filters

    names, error = bots.parse_guild_names(raw)
    if error:
        return None, error
    found = []
    for name in names:
        board = search_filters.find_guild(name, v)
        if board is None:
            return None, tr("No guild called +%(name)s.", name=name)
        found.append(board.name)
    return found, None


def _tell(user_id, message, **values):
    """A notice (a message marked with N_ and its values) to the person responsible for a bot."""
    if not user_id:
        return
    user = g.db.query(User).filter_by(id=user_id).first()
    if user is not None:
        notify(user, message, **values)


def _tell_paused(row, reason):
    if bots.is_self(row):
        _tell(row.owner_id, NOTICE_PAUSED_SELF, reason=reason)
    else:
        _tell(row.owner_id, NOTICE_PAUSED_BOT, name=row.username, reason=reason)


def _tell_running(row):
    if bots.is_self(row):
        _tell(row.owner_id, NOTICE_RUNNING_SELF)
    else:
        _tell(row.owner_id, NOTICE_RUNNING_BOT, name=row.username)


def _image(file):
    """(file, error) for a picture or banner sent for a bot: a real picture (PNG, JPEG, GIF or WebP) of at most
    IMAGE_MAX_BYTES, checked by opening it, not by its name (helpers/profile_edit.py, as a member's own is).
    (None, None) when nothing was sent."""
    picture, code = profile_edit.check_image(file, bots.IMAGE_MAX_BYTES, bots.IMAGE_FORMATS)
    if code:
        return None, profile_edit.picture_message(code, bots.IMAGE_MAX_BYTES // (1024 * 1024))
    return picture, None


def _set_picture(account, file, which, v):
    """Store a bot's picture or banner in the site's own storage, as a member's avatar is, and run the CSAM check on
    it with `v` (who sent it) answerable. False when the storage refused it."""
    try:
        if which == "profile":
            account.set_profile(file)
        else:
            account.set_banner(file)
    except Exception:
        traceback.print_exc()
        return False
    url, remove = (account.profile_url, account.del_profile) if which == "profile" else (account.banner_url, account.del_banner)
    gevent.spawn(check_csam_url, url, v, remove)
    return True


def _display_name(raw, account_id):
    """(display name, error) for what a member typed for a bot: the same rules as for a member
    (helpers/profile_edit.py, helpers/display_names.py), including never being another account's @handle."""
    name, error = profile_edit.clean_display_name(raw)
    if error:
        return "", error
    error = profile_edit.display_name_refusal(
        g.db, account_id, name, (app.config["SITE_NAME"], "ruqqus", "uqqus"),
        handle_message=tr("That is another account's username, so it can't be a display name."))
    if error:
        return "", error
    return name, None


def _set_bio(account, raw):
    """Plain text, no links, so a bot's profile can't be used for link spam."""
    bio = bots.plain_text(raw, bots.BIO_MAX)
    account.bio = bio
    account.bio_html = f"<p>{html.escape(bio)}</p>" if bio else ""


# --- proposing ------------------------------------------------------------------------------------------------

@app.get("/settings/bots")
@auth_required
def settings_bots(v):
    rows = bot_store.bots_of(g.db, v.id)
    refusal = bots.owner_refusal(v, bot_store.active_count(g.db, v.id))
    own = bot_store.own_automation(g.db, v.id)
    return render_template("settings_bots.html", v=v, bots_list=rows, refusal=refusal, rules=bots, admin=bots.is_admin(v),
                           own=own, own_refusal=bots.self_refusal(v, own is not None and own.status != bots.REJECTED),
                           msg=request.args.get("msg"), error=request.args.get("error"))


def _feeds(raw):
    """(urls, titles, error) for the feeds typed one per line: each checked and read once now."""
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if len(lines) > bots.MAX_SOURCES:
        return [], [], trn("A bot can read at most %(num)s feed.", "A bot can read at most %(num)s feeds.",
                           bots.MAX_SOURCES)
    urls, titles = [], []
    for line in lines:
        url, error = bots.parse_feed_url(line, allow_local=feed_fetch.safe_fetch.local_allowed())
        if error:
            return [], [], error
        title, error = _feed_title(url)
        if error:
            return [], [], error
        if url not in urls:
            urls.append(url)
            titles.append(title)
    return urls, titles, None


@app.post("/settings/bots/automate")
@limiter.limit("10/hour", key_func=member_limit_key)    # each reads outside feeds now (bug hunt)
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bots_automate(v):
    """Automated posting on the member's OWN account (helpers/bots.py SELF): no new account, nothing about their
    profile changes. Their feeds' new items are posted as them, each marked automated, once an admin approves it.
    One per account, apart from the bots they run. A rejected one is replaced by the new proposal."""
    now = int(time.time())
    own = bot_store.own_automation(g.db, v.id)
    refusal = bots.self_refusal(v, own is not None and own.status != bots.REJECTED, now)
    if refusal:
        return _back("/settings/bots", error=refusal)
    purpose, error = bots.parse_purpose(request.form.get("purpose"))
    urls, titles = [], []
    if not error:
        urls, titles, error = _feeds(request.form.get("sources"))
    error = error or bots.kind_refusal(bots.SELF, len(urls))
    guilds = []
    if not error:
        guilds, error = _guild_names(request.form.get("forward_guilds"), v)
    if error:
        return _back("/settings/bots", error=error)
    if own is not None:                          # a rejected proposal: its row and feeds make way for this one
        bot_store.forget_bot(g.db, v.id)
    bot_store.add_bot(g.db, v.id, v.id, v.username, purpose, guilds, now, kind=bots.SELF, proposed_by=v.id)
    for url, title in zip(urls, titles):
        source_id = bot_store.add_source(g.db, v.id, url, now)
        bot_store.record_check(g.db, source_id, 0, 0, title=title)
    approved = bots.is_admin(v) and request.form.get("approve_now") == "1"
    if approved:
        bot_store.review_bot(g.db, v.id, bots.APPROVED, v.id, "", now)
    g.db.commit()
    return _back(f"/settings/bots/{v.username}", msg=tr("Your account posts from these feeds from now on.") if approved else
                 tr("Thanks. An admin will look at it soon; you will get a notification."))


@app.post("/settings/bots/apply")
@limiter.limit("10/hour", key_func=member_limit_key)    # each reads outside feeds now (bug hunt)
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bots_apply(v):
    """Propose a bot: its kind, name, what it posts, bio, picture, banner, feeds and guilds. An admin may approve it
    at once (`approve_now`): that is how the admins make site bots of their own."""
    now = int(time.time())
    refusal = bots.owner_refusal(v, bot_store.active_count(g.db, v.id), now)
    if refusal:
        return _back("/settings/bots", error=refusal)

    kind = bots.parse_kind(request.form.get("kind"))
    name, error = bots.parse_username(request.form.get("username"))
    purpose, purpose_error = bots.parse_purpose(request.form.get("purpose"))
    display, display_error = _display_name(request.form.get("display_name"), None)
    error = error or purpose_error or display_error
    if not error and get_user(name, graceful=True):
        error = tr("That name is taken.")
    urls, titles = [], []
    if not error:
        urls, titles, error = _feeds(request.form.get("sources"))
    if not error and kind == bots.SELF:
        error = tr("Automated posting on your own account is set up in its own section.")
    error = error or bots.kind_refusal(kind, len(urls))
    guilds = []
    if not error:
        guilds, error = _guild_names(request.form.get("forward_guilds"), v)
    profile, banner = None, None
    if not error:
        profile, error = _image(request.files.get("profile"))
    if not error:
        banner, error = _image(request.files.get("banner"))
    if error:
        return _back("/settings/bots", error=error)

    member = kind == bots.MEMBER
    account = User(username=name, original_username=name, display_name=display, passhash=bots.UNUSABLE_PASSWORD, tos_agreed_utc=now,
                   is_bot=True, bot_owner_id=v.id if member else None, creation_ip=None,
                   following_visibility=0, followers_visibility=0, subscribers_visibility=0, subscriptions_visibility=0)
    _set_bio(account, request.form.get("bio"))
    apply_user_severity(account)
    g.db.add(account)
    g.db.flush()
    bot_store.add_bot(g.db, account.id, v.id if member else None, name, purpose, guilds, now, kind=kind, proposed_by=v.id)
    for url, title in zip(urls, titles):
        source_id = bot_store.add_source(g.db, account.id, url, now)
        bot_store.record_check(g.db, source_id, 0, 0, title=title)
    stored = all(_set_picture(account, f, which, v) for f, which in ((profile, "profile"), (banner, "banner")) if f)
    approved = bots.is_admin(v) and request.form.get("approve_now") == "1"
    if approved:
        bot_store.review_bot(g.db, account.id, bots.APPROVED, v.id, "", now)
    g.db.commit()
    if approved and stored:
        message = tr("@%(name)s is approved and running.", name=name)
    elif approved:
        message = tr("@%(name)s is approved and running. The picture could not be stored; try again from the bot's "
                     "page.", name=name)
    elif stored:
        message = tr("Thanks. An admin will look at the bot soon; you will get a notification.")
    else:
        message = tr("Thanks. An admin will look at the bot soon; you will get a notification. The picture could not "
                     "be stored; try again from the bot's page.")
    return _back(_page(account), msg=message)


# --- one bot's page --------------------------------------------------------------------------------------------

def _keys_of(bot_id):
    """A bot's keys: the OAuth grants made for the bot account (its member's script uses one)."""
    return g.db.query(ClientAuth).filter_by(user_id=bot_id).order_by(ClientAuth.id).all()


def _is_owner(row, v):
    return row.kind == bots.MEMBER and row.owner_id == v.id


def _bot_page(account, row, v, admin, **extra):
    names = {}
    for uid in {row.owner_id, row.proposed_by_id} - {None}:
        person = g.db.query(User).filter_by(id=uid).first()
        names[uid] = person.username if person else None
    return render_template("settings_bot.html", v=v, bot=row, account=account, rules=bots, admin=admin,
                           is_owner=_is_owner(row, v), names=names,
                           sources=bot_store.sources_of(g.db, account.id), guilds=bot_store.guilds_of(row),
                           posts_today=bot_store.posts_today(g.db, account.id, bots.utc_day()),
                           keys=_keys_of(account.id) if row.kind == bots.MEMBER else [],
                           apps=g.db.query(OauthApp).filter_by(author_id=v.id, is_banned=False).all() if _is_owner(row, v) else [],
                           msg=request.args.get("msg"), error=request.args.get("error"), **extra)


@app.get("/settings/bots/<name>")
@auth_required
def settings_bot(name, v):
    account, row, admin = _manageable(name, v)
    return _bot_page(account, row, v, admin)


@app.post("/settings/bots/<name>/profile")
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_profile(name, v):
    account, row, admin = _manageable(name, v)
    guilds, error = _guild_names(request.form.get("forward_guilds"), v)
    if bots.is_self(row):                        # a person's own account: only where its automated posts go
        if error:
            return _back(_page(account), error=error)
        bot_store.set_forward_guilds(g.db, account.id, guilds)
        g.db.commit()
        return _back(_page(account), msg=tr("Saved."))
    if not error and request.form.get("display_name") is not None:
        account.display_name, error = _display_name(request.form.get("display_name"), account.id)
    if error:
        return _back(_page(account), error=error)
    _set_bio(account, request.form.get("bio"))
    apply_user_severity(account)
    g.db.add(account)
    bot_store.set_forward_guilds(g.db, account.id, guilds)
    g.db.commit()
    return _back(_page(account), msg=tr("Saved."))


@app.post("/settings/bots/<name>/image/<which>")
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_picture(name, which, v):
    """A new picture (`which` = picture) or banner for the bot."""
    if which not in ("picture", "banner"):
        abort(404)
    account, row, admin = _manageable(name, v)
    _not_self(row)
    field = "profile" if which == "picture" else "banner"
    file, error = _image(request.files.get(field))
    if error or file is None:
        return _back(_page(account), error=error or tr("Choose a picture to upload."))
    if not _set_picture(account, file, field, v):
        return _back(_page(account), error=tr("The picture could not be stored. Try again later."))
    g.db.commit()
    return _back(_page(account), msg=tr("Saved."))


@app.post("/settings/bots/<name>/image/<which>/remove")
@auth_required
@not_qr_limited
@validate_formkey
def settings_bot_picture_remove(name, which, v):
    if which not in ("picture", "banner"):
        abort(404)
    account, row, admin = _manageable(name, v)
    _not_self(row)
    try:
        account.del_profile() if which == "picture" else account.del_banner()
    except Exception:
        traceback.print_exc()                   # the stored file may stay behind; the flag below is what pages read
    if which == "picture":
        account.has_profile = False
    else:
        account.has_banner = False
    g.db.add(account)
    g.db.commit()
    return _back(_page(account), msg=tr("Removed."))


# --- feeds -----------------------------------------------------------------------------------------------------

@app.post("/settings/bots/<name>/sources/add")
@limiter.limit("30/hour", key_func=member_limit_key)    # each reads outside feeds now (bug hunt)
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_add_source(name, v):
    """A member's new feed waits for an admin; one an admin adds is approved at once."""
    account, row, admin = _manageable(name, v)
    if bot_store.source_count(g.db, account.id) >= bots.MAX_SOURCES:
        return _back(_page(account), error=trn("A bot can read at most %(num)s feed.",
                                               "A bot can read at most %(num)s feeds.", bots.MAX_SOURCES))
    url, error = bots.parse_feed_url(request.form.get("url"), allow_local=feed_fetch.safe_fetch.local_allowed())
    title = None
    if not error:
        title, error = _feed_title(url)
    if error:
        return _back(_page(account), error=error)
    source_id = bot_store.add_source(g.db, account.id, url, approved_by=v.id if admin else None)
    if source_id is None:
        return _back(_page(account), error=tr("The bot already reads that feed."))
    bot_store.record_check(g.db, source_id, 0, 0, title=title)
    g.db.commit()
    return _back(_page(account), msg=tr("Added.") if admin or row.status == bots.PENDING else
                 tr("Added. It starts once an admin approves it."))


@app.post("/settings/bots/<name>/sources/<int:source_id>/remove")
@auth_required
@not_qr_limited
@validate_formkey
def settings_bot_remove_source(name, source_id, v):
    account, row, admin = _manageable(name, v)
    if not bot_store.remove_source(g.db, account.id, source_id):
        abort(404)
    g.db.commit()
    return _back(_page(account), msg=tr("Removed. The bot no longer reads that feed."))


@app.post("/settings/bots/<name>/sources/<int:source_id>/restart")
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_restart_source(name, source_id, v):
    """A source paused after too many failed reads starts again (an admin approved it before; that still holds)."""
    account, row, admin = _manageable(name, v)
    source = bot_store.source(g.db, source_id)
    if source is None or source.bot_id != account.id:
        abort(404)
    bot_store.set_source_paused(g.db, source_id, False)
    g.db.commit()
    return _back(_page(account), msg=tr("It will be read again within a few minutes."))


# --- running ---------------------------------------------------------------------------------------------------

@app.post("/settings/bots/<name>/pause")
@auth_required
@not_qr_limited
@validate_formkey
def settings_bot_pause(name, v):
    account, row, admin = _manageable(name, v)
    reason = bots.plain_text(request.form.get("reason"), 300) or (PAUSED_BY_ADMINS if admin else PAUSED_BY_OWNER)
    if not bot_store.set_running(g.db, account.id, False, reason, by_admin=admin):
        return _back(_page(account), error=tr("Only a running bot can be paused."))
    g.db.commit()
    if admin and row.kind in (bots.MEMBER, bots.SELF) and row.owner_id != v.id:
        _tell_paused(row, reason)
    return _back(_page(account), msg=tr("Paused. Nothing is posted until it is resumed."))


@app.post("/settings/bots/<name>/resume")
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_resume(name, v):
    account, row, admin = _manageable(name, v)
    if not bot_store.set_running(g.db, account.id, True, by_admin=admin):
        return _back(_page(account), error=tr("An admin paused this bot; only an admin can start it again."))
    g.db.commit()
    if admin and row.kind in (bots.MEMBER, bots.SELF) and row.owner_id != v.id:
        _tell_running(row)
    return _back(_page(account), msg=tr("Running again."))


@app.post("/settings/bots/<name>/delete")
@auth_required
@not_qr_limited
@validate_formkey
def settings_bot_delete(name, v):
    """A proposal that still waits is withdrawn: the account goes (it never posted). A bot that ran stops for good:
    its posts stay, under a deactivated account, like any account's, and its keys stop working. A person's own
    account only stops posting automatically: the account, its posts and its own apps' grants are untouched."""
    account, row, admin = _manageable(name, v)
    if bots.is_self(row):
        bot_store.forget_bot(g.db, account.id)
        g.db.commit()
        if admin and row.owner_id != v.id:
            _tell(row.owner_id, NOTICE_SELF_OFF)
            return _back("/admin/bots", msg=f"@{row.username}'s automated posting is switched off.")
        return _back("/settings/bots", msg=tr("Your account no longer posts automatically. Its posts stay."))
    username = account.username
    to_admins = admin and not _is_owner(row, v)          # the admins' queue stays in English
    for key in _keys_of(account.id):
        g.db.delete(key)
    bot_store.forget_bot(g.db, account.id)
    if row.status == bots.PENDING:
        g.db.delete(account)
        message = f"The proposal for @{username} has been withdrawn." if to_admins else \
            tr("The proposal for @%(name)s has been withdrawn.", name=username)
    else:
        account.is_deleted = True
        g.db.add(account)
        message = f"@{username} has been switched off for good." if to_admins else \
            tr("@%(name)s has been switched off for good.", name=username)
    g.db.commit()
    return _back("/admin/bots" if to_admins else "/settings/bots", msg=message)


# --- bot keys: a member's own script posts as their bot (e.g. for X, which has no free feed) ---------------------

# what a bot key may do: post, read, edit and delete the bot's own posts. Never vote, and never act as a guildmaster.
KEY_SCOPES = dict(scope_identity=True, scope_read=True, scope_create=True, scope_update=True, scope_delete=True,
                  scope_vote=False, scope_guildmaster=False)


@app.post("/settings/bots/<name>/keys/create")
@is_not_banned
@not_qr_limited
@validate_formkey
def settings_bot_create_key(name, v):
    """A key for a member bot, made by its own member through one of their own apps (Settings > Apps): a grant with a
    refresh token, shown this once. The script trades it for an hour's access token at POST /oauth/grant
    (grant_type=refresh) and posts through the API like any app; every bot rule in submit_post applies. A site bot
    has no keys: it runs on free feeds."""
    account, row, admin = _manageable(name, v)
    if not _is_owner(row, v):
        abort(404)
    if bots._age_locked(v):
        return _back(_page(account), error=tr("Your account can't run a bot while it is age-restricted."))
    if row.status not in (bots.APPROVED, bots.PAUSED):
        return _back(_page(account), error=tr("A bot gets keys once an admin has approved it."))
    if len(_keys_of(account.id)) >= bots.MAX_KEYS:
        return _back(_page(account), error=trn("A bot can have at most %(num)s key. Revoke it first.",
                                               "A bot can have at most %(num)s keys. Revoke one first.", bots.MAX_KEYS))
    raw = request.form.get("app", "")
    owned_app = None
    if raw.isascii() and raw.isdigit():
        owned_app = g.db.query(OauthApp).filter_by(id=int(raw), author_id=v.id, is_banned=False).first()
    if owned_app is None:
        return _back(_page(account), error=tr("Choose one of your own apps (Settings > Apps)."))
    refresh = secrets.token_urlsafe(128)[0:128]
    g.db.add(ClientAuth(oauth_client=owned_app.id, oauth_code=None, user_id=account.id, refresh_token=refresh,
                        access_token=None, access_token_expire_utc=0, **KEY_SCOPES))
    g.db.commit()
    page = make_response(_bot_page(account, row, v, admin, new_key={
        "refresh_token": refresh, "client_id": owned_app.client_id, "app_name": owned_app.app_name}))
    page.headers["Cache-Control"] = "no-store"
    return page


@app.post("/settings/bots/<name>/keys/<int:key_id>/revoke")
@auth_required
@not_qr_limited
@validate_formkey
def settings_bot_revoke_key(name, key_id, v):
    account, row, admin = _manageable(name, v)
    _not_self(row)
    key = g.db.query(ClientAuth).filter_by(id=key_id, user_id=account.id).first()
    if key is None:
        abort(404)
    g.db.delete(key)
    g.db.commit()
    return _back(_page(account), msg=tr("Revoked. A script using that key can't post any more."))


# --- the admins ------------------------------------------------------------------------------------------------

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


def _admin_back(row, msg):
    """Back to the bot's own page when the decision was made there, else to the queue."""
    if request.form.get("next") == "page" and row.status != bots.REJECTED:
        return _back(f"/settings/bots/{row.username}", msg=msg)
    return _back("/admin/bots", msg=msg)


@app.post("/admin/bots/<int:uid>/approve")
@admin_level_required(3)
@validate_formkey
def admin_bot_approve(uid, v):
    row = bot_store.bot_of(g.db, uid)
    if row is None or not bot_store.review_bot(g.db, uid, bots.APPROVED, v.id, bots.plain_text(request.form.get("note"), bots.NOTE_MAX)):
        abort(404)
    g.db.commit()
    if row.kind == bots.SITE:
        _tell(row.proposed_by_id, NOTICE_APPROVED_SITE, name=row.username)
    elif bots.is_self(row):
        _tell(row.owner_id, NOTICE_APPROVED_SELF)
    else:
        _tell(row.owner_id, NOTICE_APPROVED_BOT, name=row.username)
    return _admin_back(bot_store.bot_of(g.db, uid), f"@{row.username} approved.")


@app.post("/admin/bots/<int:uid>/reject")
@admin_level_required(3)
@validate_formkey
def admin_bot_reject(uid, v):
    row = bot_store.bot_of(g.db, uid)
    note = bots.plain_text(request.form.get("note"), bots.NOTE_MAX)
    if row is None or not bot_store.review_bot(g.db, uid, bots.REJECTED, v.id, note):
        abort(404)
    # a bot account never posted anything: it goes, so the name is free again (the row keeps the name and the note).
    # A person's own account is never a bot account (is_bot), so nothing here can touch it.
    if not bots.is_self(row):
        account = g.db.query(User).filter_by(id=uid, is_bot=True).first()
        if account is not None:
            g.db.delete(account)
    g.db.commit()
    if bots.is_self(row):
        _tell(bots.responsible_id(row), NOTICE_REJECTED_SELF_NOTE if note else NOTICE_REJECTED_SELF, note=note)
    else:
        _tell(bots.responsible_id(row), NOTICE_REJECTED_BOT_NOTE if note else NOTICE_REJECTED_BOT, name=row.username,
              note=note)
    return _back("/admin/bots", msg=f"@{row.username} rejected.")


@app.post("/admin/bots/<int:uid>/rename")
@admin_level_required(3)
@validate_formkey
def admin_bot_rename(uid, v):
    """Before approval an admin may give a proposed bot a better name."""
    row = bot_store.bot_of(g.db, uid)
    account = g.db.query(User).filter_by(id=uid, is_bot=True).first()
    if row is None or account is None or row.status != bots.PENDING:
        abort(404)
    name, error = bots.parse_username(request.form.get("username"))
    if not error and name.lower() != account.username.lower() and get_user(name, graceful=True):
        error = "That name is taken."
    if error:
        return _back(f"/settings/bots/{account.username}", error=error)
    account.username = name
    account.original_username = name
    apply_user_severity(account)
    g.db.add(account)
    bot_store.rename(g.db, uid, name)
    g.db.commit()
    return _back(f"/settings/bots/{name}", msg=f"Renamed to @{name}.")


@app.post("/admin/bots/<int:uid>/kind")
@admin_level_required(3)
@validate_formkey
def admin_bot_kind(uid, v):
    """Before approval an admin may decide a proposal is the other kind: a site bot has no owner, a member bot's owner
    is the member who proposed it."""
    row = bot_store.bot_of(g.db, uid)
    account = g.db.query(User).filter_by(id=uid, is_bot=True).first()
    kind = bots.parse_kind(request.form.get("kind"))
    if row is None or account is None or row.status != bots.PENDING or kind in (None, bots.SELF):
        abort(404)                               # a bot account is never turned into a person's own account
    if kind == bots.SITE and not bot_store.source_count(g.db, uid):
        return _back(f"/settings/bots/{account.username}", error="A site bot reads at least one feed: add one first.")
    owner_id = row.proposed_by_id if kind == bots.MEMBER else None
    bot_store.set_kind(g.db, uid, kind, owner_id)
    account.bot_owner_id = owner_id
    g.db.add(account)
    g.db.commit()
    return _back(f"/settings/bots/{account.username}", msg=f"It is a {bots.KIND_LABELS[kind].lower()} now.")


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
    if row is not None and row.kind in (bots.MEMBER, bots.SELF):
        approved = status == bots.APPROVED
        if bots.is_self(row):
            _tell(row.owner_id, NOTICE_FEED_APPROVED_SELF if approved else NOTICE_FEED_REJECTED_SELF, url=source.url)
        else:
            _tell(row.owner_id, NOTICE_FEED_APPROVED_BOT if approved else NOTICE_FEED_REJECTED_BOT, url=source.url,
                  name=row.username)
    return _admin_back(row, "Done.") if row is not None else _back("/admin/bots", msg="Done.")


@app.post("/admin/bots/<int:uid>/pause")
@admin_level_required(3)
@validate_formkey
def admin_bot_pause(uid, v):
    row = bot_store.bot_of(g.db, uid)
    reason = bots.plain_text(request.form.get("reason"), 300) or PAUSED_BY_ADMINS
    if row is None or not bot_store.set_running(g.db, uid, False, reason, by_admin=True):
        abort(404)
    g.db.commit()
    if row.kind in (bots.MEMBER, bots.SELF):
        _tell_paused(row, reason)
    return _admin_back(row, f"@{row.username} paused.")


@app.post("/admin/bots/<int:uid>/resume")
@admin_level_required(3)
@validate_formkey
def admin_bot_resume(uid, v):
    row = bot_store.bot_of(g.db, uid)
    if row is None or not bot_store.set_running(g.db, uid, True, by_admin=True):
        abort(404)
    g.db.commit()
    if row.kind in (bots.MEMBER, bots.SELF):
        _tell_running(row)
    return _admin_back(row, f"@{row.username} is running again.")


def bots_waiting():
    """The count beside Admin > Bots on the admin page."""
    return bot_store.waiting_count(g.db)


def automates(user):
    """Does this person's own account post from feeds by itself right now? Its profile says so."""
    return bool(user) and bot_store.automates(g.db, user.id)


# bot_rules: the limits /help/bots shows
app.jinja_env.globals.update(bots_waiting=bots_waiting, bot_rules=bots, automates=automates)
