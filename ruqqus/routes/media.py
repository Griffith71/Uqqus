"""Media stored in a member's own linked account: the settings page, the upload
endpoints the browser talks to, and the address the files are shown at.

The rules are in helpers/media/rules.py and the providers next to it. Nothing uploaded
is kept on this server: the browser sends the bytes to the provider, and /media/...
fetches them from there when someone looks (a CDN in front of the site keeps the answer).
"""
import hmac
import secrets
import time

from urllib.parse import urlencode

from flask import Response, g, jsonify, redirect, render_template, request, session

from ruqqus.__main__ import app, limiter
from ruqqus.classes import Board, MediaAccount, MediaAsset, User
from ruqqus.helpers import circle_guard, circles
from ruqqus.helpers.media import attach, cdn, dev, google_oauth, oauth, registry, rules, safety
from ruqqus.helpers.media.base import AccountLost, MediaGone, ProviderDown
from ruqqus.helpers.secret_box import encrypt_secret
from ruqqus.helpers.security import generate_hash, validate_hash
from ruqqus.helpers.wrappers import auth_required, get_logged_in_user, validate_formkey

# templates ask this to decide between the linked-storage upload controls and the old ones
app.jinja_env.globals["media_offered"] = lambda: bool(registry.account_kinds())
# the settings page names the provider a kind of file goes to on a linked account ("OneDrive")
app.jinja_env.globals["service_provider"] = rules.provider_for

NO_STORE = "private, no-store"
FOREVER = "public, max-age=31536000, immutable"


def _error(message, code=400, **extra):
    return jsonify({"error": message, **extra}), code


def _media_error(e):
    extra = {"need": e.need, "settings": e.settings} if e.need else {}
    return _error(e.message, e.code, **extra)


def _accounts(v):
    return g.db.query(MediaAccount).filter_by(user_id=v.id).order_by(MediaAccount.id).all()


def _preferred(accounts, kind):
    """The linked-account kind the member chose for this kind of file, if any."""
    for account in accounts.values():
        if kind in (account.setting("default_for") or []):
            return account.provider
    return None


def _account_for(v, kind, wanted=None):
    """The member's linked account that takes this kind, with its provider: `wanted` (a kind
    of linked account) when given, else the one they chose for this kind, else the first."""
    by_kind = {a.provider: a for a in _accounts(v) if a.is_active}
    if wanted:
        by_kind = {name: a for name, a in by_kind.items() if name == wanted}
    for account in (by_kind[name] for name in rules.in_order(by_kind, _preferred(by_kind, kind))):
        name = rules.provider_for(account.provider, kind)
        provider = registry.get(name) if name else None
        if provider is not None:
            return account, provider
    return None, None


def _keep_rotated(account):
    """A service that hands out a new refresh token with each use (Microsoft) wrote it onto the
    row (helpers/media/oauth.py): keep it, or the next request would use a spent one."""
    if account is not None and getattr(account, "_rotated", False):
        account._rotated = False
        g.db.add(account)
        g.db.commit()


def _my_asset(v, raw_id):
    try:
        asset_id = int(raw_id, 36)
    except (TypeError, ValueError):
        return None
    return g.db.query(MediaAsset).filter_by(id=asset_id, user_id=v.id).first()


def _lost(account):
    """The provider no longer accepts the member's permission: remember it, so the settings
    page offers to connect again and nothing keeps asking the provider."""
    if account is not None and account.status == "active":
        account.status = "error"
        account.updated_utc = int(time.time())
        g.db.add(account)
        g.db.commit()


RECONNECT = "Your media storage needs connecting again."
BUSY = "The storage service is not answering right now. Try again in a moment."


def _set_status(asset, status):
    if asset.status != status and rules.can_change(asset.status, status):
        asset.status = status
        asset.updated_utc = int(time.time())
        g.db.add(asset)


# --- settings ----------------------------------------------------------------------

SPACE_SECONDS = 600        # how long a member's free space is remembered (each check is a request to the service)


def _space_left(account, provider):
    """Bytes free in the member's storage, asked of the service at most every few minutes, or None."""
    key = f"media:space:{account.id}:{provider.name}"
    known = oauth.recall(key)
    if known is not None:
        return int(known) if known.lstrip("-").isdigit() and int(known) >= 0 else None
    try:
        free = provider.space_left(account)
    except (AccountLost, ProviderDown, MediaGone):
        return None
    oauth.remember(key, str(free if free is not None else -1), SPACE_SECONDS)
    return free


def _choices(v, accounts):
    """For each kind of file: the linked accounts that can take it and the one it goes to now."""
    out = {}
    for kind in rules.KINDS:
        able = [name for name in rules.services_for(kind, accounts) if registry.get(rules.provider_for(name, kind))]
        current, provider = _account_for(v, kind)
        out[kind] = {"able": able, "current": current.provider if current else None}
    return out


@app.get("/settings/media")
@auth_required
def settings_media(v):
    rows = _accounts(v)
    accounts = {a.provider: a for a in rows if a.is_active}
    # connected before, but the provider stopped accepting it: offer to connect again
    expired = {a.provider for a in rows if a.status == "error"}
    google = accounts.get("google")
    dailymotion = accounts.get("dailymotion")
    space = {}
    for name, account in accounts.items():
        provider = registry.get(rules.provider_for(name, rules.IMAGE) or "")
        if provider is not None and provider.served and name != "dev":
            space[name] = _space_left(account, provider)
    _keep_rotated_all(accounts.values())
    return render_template("settings_media.html", v=v, accounts=accounts, expired=expired,
                           offered=registry.account_kinds(),
                           google_video=bool(google) and google_oauth.SCOPE_YOUTUBE in google.scopes.split(),
                           video_visibility=rules.video_visibility(google.setting("youtube_visibility") if google else None),
                           dailymotion_visibility=rules.video_visibility(dailymotion.setting("video_visibility") if dailymotion else None),
                           choices=_choices(v, accounts), space=space,
                           labels=rules.SERVICE_LABELS, provider_labels=rules.PROVIDER_LABELS,
                           error=request.args.get("error"), msg=request.args.get("msg"))


def _keep_rotated_all(accounts):
    for account in accounts:
        _keep_rotated(account)


# --- connecting, from the settings page or from a popup the composer opened ----------------

POPUP_SECONDS = 900


def _remember_popup():
    """The composer opened this connection in a popup (`popup=1`) with its own `n`: say so at the
    end with a page that tells the composer and closes, instead of the settings page."""
    nonce = request.args.get("n") or ""
    if request.args.get("popup") == "1" and nonce.isascii() and nonce.isalnum() and 8 <= len(nonce) <= 40:
        session["media_connect"] = {"n": nonce, "t": int(time.time())}
    else:
        session.pop("media_connect", None)


def _finish(service, message, ok):
    """The end of a connection: back to the settings page, or the closing page of a popup."""
    popup = session.pop("media_connect", None)
    if isinstance(popup, dict) and int(popup.get("t") or 0) > int(time.time()) - POPUP_SECONDS:
        return render_template("media_connected.html", ok=ok, message=message, payload={
            "type": "ruqqus-media-connected", "ok": bool(ok), "message": message,
            "service": service, "n": popup.get("n")})
    return redirect("/settings/media?" + urlencode({"msg" if ok else "error": message}))


def _google_redirect_uri():
    return cdn.absolute("/settings/media/google/callback")


@app.get("/settings/media/google/connect")
@auth_required
def settings_media_google_connect(v):
    """Send the member to Google to approve. `want=video` also asks for uploading to
    YouTube (only when they first add a video); otherwise only the Drive files this site makes."""
    _remember_popup()
    if not google_oauth.configured():
        return _finish("google", "Google storage is not set up on this site.", False)
    want = "video" if request.args.get("want") == "video" else "storage"
    state = google_oauth.make_state(v.id, want, int(time.time()), generate_hash)
    return redirect(google_oauth.authorize_url(_google_redirect_uri(), state, want))


@app.get("/settings/media/google/callback")
@auth_required
def settings_media_google_callback(v):
    """Google sends the member back here with a code, or with why not."""
    def back(message, key="error"):
        return _finish("google", message, key == "msg")

    if not google_oauth.configured():
        return back("Google storage is not set up on this site.")
    want = google_oauth.read_state(request.args.get("state"), v.id, int(time.time()), validate_hash)
    if want is None:
        return back("That connection attempt expired. Try again.")
    if request.args.get("error") or not request.args.get("code"):
        return back("Nothing was connected: Google access was not approved.")

    try:
        granted = google_oauth.exchange(request.args["code"], _google_redirect_uri())
    except ValueError as e:
        return back(str(e))
    except ProviderDown:
        return back("Google is not answering right now. Try again in a moment.")
    if google_oauth.SCOPE_DRIVE not in granted["scopes"]:
        return back("To store your uploads, tick the box that lets this site manage the files it creates in your Google Drive.")

    account = _link(v, "google", external_id=granted["sub"], scopes=" ".join(granted["scopes"]),
                    refresh_token_encrypted=encrypt_secret(granted["refresh_token"]))
    google_oauth.forget(account)
    g.db.commit()
    if want == "video" and google_oauth.SCOPE_YOUTUBE not in granted["scopes"]:
        return back("Pictures and audio are connected. Video was not: YouTube access was not approved.", "msg")
    return back("Media storage is on. Your uploads now go to your Google account.", "msg")


def _service_redirect_uri(name):
    return cdn.absolute(f"/settings/media/{name}/callback")


@app.get("/settings/media/<any(microsoft, dailymotion):name>/connect")
@auth_required
def settings_media_service_connect(name, v):
    """Send the member to the service to approve (OneDrive for Microsoft, video for Dailymotion)."""
    _remember_popup()
    service = registry.SERVICES[name]
    if not service.configured():
        return _finish(name, f"{service.label} is not set up on this site.", False)
    want = next(iter(service.wants))
    state = service.make_state(v.id, want, int(time.time()), generate_hash)
    return redirect(service.authorize_url(_service_redirect_uri(name), state, want))


@app.get("/settings/media/<any(microsoft, dailymotion):name>/callback")
@auth_required
def settings_media_service_callback(name, v):
    """The service sends the member back here with a code, or with why not."""
    service = registry.SERVICES[name]
    if not service.configured():
        return _finish(name, f"{service.label} is not set up on this site.", False)
    want = service.read_state(request.args.get("state"), v.id, int(time.time()), validate_hash)
    if want is None:
        return _finish(name, "That connection attempt expired. Try again.", False)
    if request.args.get("error") or not request.args.get("code"):
        return _finish(name, f"Nothing was connected: {service.label} access was not approved.", False)
    try:
        granted = service.exchange(request.args["code"], _service_redirect_uri(name), want)
    except ValueError as e:
        return _finish(name, str(e), False)
    except ProviderDown:
        return _finish(name, f"{service.label} is not answering right now. Try again in a moment.", False)
    if not service.has_scope(granted["scopes"], service.required):
        return _finish(name, f"Nothing was connected: the access this site needs at {service.label} was not approved.", False)

    account = _link(v, name, external_id=granted["sub"], scopes=" ".join(granted["scopes"]),
                    refresh_token_encrypted=encrypt_secret(granted["refresh_token"]))
    service.forget(account)
    g.db.commit()
    kinds = rules.PROVIDERS.get(name, {})
    what = "Your videos now go to your Dailymotion channel." if rules.VIDEO in kinds and len(kinds) == 1 else \
        f"Your pictures and audio can now go to your {rules.PROVIDER_LABELS.get(kinds.get(rules.IMAGE), service.label)}."
    return _finish(name, f"{service.label} is connected. {what}", True)


@app.post("/settings/media/dev/enable")
@auth_required
@validate_formkey
def settings_media_dev_enable(v):
    """Link the stand-in storage (development only, see helpers/media/dev.py)."""
    if not dev.enabled():
        if request.values.get("json"):
            return _error("That storage is not available here.", 404)
        return redirect("/settings/media?error=That+storage+is+not+available+here.")
    _link(v, "dev", external_id=f"dev-{v.id}")
    g.db.commit()
    if request.values.get("json"):            # the composer links it without leaving the page
        return jsonify({"message": "Test storage is on."})
    return redirect("/settings/media?msg=Media+storage+is+on.")


@app.post("/settings/media/<provider>/disconnect")
@auth_required
@validate_formkey
def settings_media_disconnect(provider, v):
    account = g.db.query(MediaAccount).filter_by(user_id=v.id, provider=provider).first()
    if not account or not account.is_active:
        return redirect("/settings/media")
    _unlink(account)
    g.db.commit()
    return redirect("/settings/media?msg=Disconnected.")


def _link(v, provider, external_id, scopes="", refresh_token_encrypted=""):
    """Create or revive the member's account row for a provider. Files that went dark when
    the same account was disconnected come back; a different account starts clean."""
    now = int(time.time())
    account = g.db.query(MediaAccount).filter_by(user_id=v.id, provider=provider).first()
    same = bool(account) and account.external_id == external_id
    if not account:
        account = MediaAccount(user_id=v.id, provider=provider, created_utc=now)
    account.external_id = external_id
    account.scopes = scopes
    if refresh_token_encrypted:
        account.refresh_token_encrypted = refresh_token_encrypted
    account.status = "active"
    account.updated_utc = now
    if not same:
        account.settings = "{}"
    g.db.add(account)
    g.db.flush()
    if same:
        g.db.query(MediaAsset).filter_by(account_id=account.id, status=rules.GONE).update(
            {"status": rules.READY, "updated_utc": now}, synchronize_session=False)
    return account


def _unlink(account):
    """Give the access back and stop showing what Ruqqus can no longer fetch. Files on a
    site that shows them itself (videos) are not ours to hide."""
    now = int(time.time())
    for name in set(rules.PROVIDERS.get(account.provider, {}).values()):
        provider = registry.get(name)
        if provider is not None:
            try:
                provider.revoke(account)
            except Exception:
                pass                     # the access is forgotten here whatever the provider answers
    account.status = "revoked"
    account.refresh_token_encrypted = ""
    account.updated_utc = now
    g.db.add(account)
    g.db.query(MediaAsset).filter(
        MediaAsset.account_id == account.id,
        MediaAsset.provider.in_(rules.SERVED),
        MediaAsset.status.in_((rules.READY, rules.PENDING)),
    ).update({"status": rules.GONE, "updated_utc": now}, synchronize_session=False)


# --- uploading ---------------------------------------------------------------------

@app.get("/api/media/status")
@auth_required
def media_status(v):
    """What this member can upload right now, for the upload buttons."""
    rows = _accounts(v)
    accounts = {a.provider: a for a in rows if a.is_active}
    expired = {a.provider for a in rows if a.status == "error"}
    offered = registry.account_kinds()
    kinds, using = [], {}
    for kind in rules.KINDS:
        account, provider = _account_for(v, kind)
        if provider is not None:
            kinds.append(kind)
            using[kind] = {"service": account.provider, "label": provider.label}
    # every service the site offers, per kind: what the composer can offer to connect
    services = []
    for name in offered:
        able = [k for k in rules.KINDS if rules.provider_for(name, k) and registry.get(rules.provider_for(name, k))]
        if not able:
            continue
        services.append({
            "name": name,
            "label": rules.SERVICE_LABELS.get(name, name),
            "kinds": able,
            "names": {k: rules.PROVIDER_LABELS.get(rules.provider_for(name, k), name) for k in able},
            "hints": {k: rules.SERVICE_HINTS.get((name, k), "") for k in able},
            "linked": name in accounts,
            "expired": name in expired and name not in accounts,
            "connect": "/settings/media/dev/enable" if name == "dev" else f"/settings/media/{name}/connect",
            "method": "post" if name == "dev" else "popup",
        })
    google = accounts.get("google")
    dailymotion = accounts.get("dailymotion")
    return jsonify({"offered": bool(offered), "kinds": kinds, "using": using, "services": services,
                    # the site can take video at all (a linked account for it is asked for at the first video)
                    "video": any(registry.get(p) is not None for p in rules.HOSTED),
                    "video_visibility": rules.video_visibility(google.setting("youtube_visibility") if google else None),
                    "video_visibility_by": {
                        "google": rules.video_visibility(google.setting("youtube_visibility") if google else None),
                        "dailymotion": rules.video_visibility(dailymotion.setting("video_visibility") if dailymotion else None)},
                    "limits": {k: rules.SIZE_MAX[k] for k in kinds if k in rules.SIZE_MAX},
                    "settings": "/settings/media"})


def _set_default(v, kind, service):
    """Send this kind of file to this linked account from now on. False when it can't take it."""
    accounts = {a.provider: a for a in _accounts(v) if a.is_active}
    target = accounts.get(service)
    if kind not in rules.KINDS or target is None or not rules.provider_for(service, kind):
        return False
    now = int(time.time())
    for account in accounts.values():
        chosen = [k for k in (account.setting("default_for") or []) if k in rules.KINDS and k != kind]
        if account is target:
            chosen.append(kind)
        account.set_setting("default_for", chosen)
        account.updated_utc = now
        g.db.add(account)
    return True


@app.post("/api/media/default")
@auth_required
@validate_formkey
def media_default(v):
    """The composer's "Change": where this kind of file goes from now on."""
    kind, service = request.form.get("kind", ""), request.form.get("service", "")
    if not _set_default(v, kind, service):
        return _error("That account can't take this kind of file.", 400)
    g.db.commit()
    return jsonify({"message": f"From now on your {kind} files go to your "
                               f"{rules.PROVIDER_LABELS.get(rules.provider_for(service, kind), service)}."})


@app.post("/settings/media/default")
@auth_required
@validate_formkey
def settings_media_default(v):
    """The settings page's choice of where each kind of file goes."""
    changed = False
    for kind in rules.KINDS:
        service = request.form.get(kind)
        if service:
            changed = _set_default(v, kind, service) or changed
    g.db.commit()
    return redirect("/settings/media?msg=Saved." if changed else "/settings/media")


@app.post("/api/media/uploads")
@limiter.limit("60/minute")
@auth_required
@validate_formkey
def media_upload_begin(v):
    """Start an upload into the member's linked account.

Form data: `kind` (image, audio or video), `filename`, `size` (bytes); for a video also
`title` and `visibility` (unlisted or public; left out: the member's setting).
Answers with where the browser sends the bytes (`upload.url`, `upload.method`,
`upload.headers`) and the asset's `id`. Then call `/api/media/uploads/<id>/complete`.
"""
    if not rules.may_upload(v):
        return _error("Your account cannot upload right now.", 403)

    kind = (request.form.get("kind") or "").strip()
    try:
        ext = rules.check_request(kind, request.form.get("filename"), request.form.get("size"))
    except rules.MediaError as e:
        return _error(e.message, e.code)

    account, provider = _account_for(v, kind, request.form.get("account") or None)
    if provider is None:
        return _error("Turn on media storage to upload.", 409, need="storage", settings="/settings/media")

    now = int(time.time())
    recent = g.db.query(MediaAsset).filter(MediaAsset.user_id == v.id, MediaAsset.created_utc > now - 3600).count()
    if recent >= rules.UPLOADS_PER_HOUR:
        return _error("You have uploaded a lot in the last hour. Try again a little later.", 429)

    asset = MediaAsset(user_id=v.id, account_id=account.id, provider=provider.name, kind=kind,
                       token=secrets.token_urlsafe(18), ext=ext, size=int(request.form.get("size")),
                       status=rules.PENDING, created_utc=now, updated_utc=now)
    g.db.add(asset)
    g.db.flush()
    details = {"title": request.form.get("title") or request.form.get("filename"),
               "visibility": request.form.get("visibility"), "site": app.config.get("SITE_NAME")}
    try:
        upload = provider.begin_upload(account, asset, request.host_url.rstrip("/"), details)
    except rules.MediaError as e:
        g.db.rollback()
        return _media_error(e)
    except AccountLost:
        g.db.rollback()
        _lost(account)
        return _error(RECONNECT, 409, need="storage", settings="/settings/media")
    except ProviderDown:
        g.db.rollback()
        return _error(BUSY, 503)
    account._rotated = False
    g.db.add(account)          # a provider may have remembered something on it (its folders, a new refresh token)
    g.db.add(asset)
    g.db.commit()
    return jsonify({"id": rules.b36(asset.id), "upload": upload,
                    "to": {"service": account.provider, "label": provider.label}})


@app.post("/api/media/uploads/<aid>/complete")
@limiter.limit("60/minute")
@auth_required
@validate_formkey
def media_upload_complete(aid, v):
    """The browser finished sending the bytes: check the file and make it usable."""
    asset = _my_asset(v, aid)
    if not asset:
        return _error("That upload was not found.", 404)
    if asset.status == rules.READY:
        return jsonify(asset.json)
    if asset.status != rules.PENDING:
        return _error("That upload can no longer be used.", 410)

    account = g.db.query(MediaAccount).filter_by(id=asset.account_id).first()
    provider = registry.get(asset.provider)
    if provider is None or not account or not account.is_active:
        return _error("Your media storage is no longer connected.", 409, need="storage", settings="/settings/media")

    temp = None
    restricted = False
    try:
        if provider.wants_answer:
            info = provider.finish_upload(account, asset, (request.form.get("answer") or "")[:2048])
        else:
            info = provider.finish_upload(account, asset)
        asset.provider_ref = info["ref"]
        asset.size = int(info["size"])
        asset.checksum = info["checksum"]
        restricted = bool(info.get("restricted"))
        if provider.served:
            head = b"".join(provider.open(account, asset, (0, min(asset.size, 64) - 1)).chunks)
            asset.ext = rules.check_uploaded(asset.kind, head, asset.size)
            if asset.kind == rules.IMAGE:
                temp = safety.fetch_to_temp(provider, account, asset, rules.SIZE_MAX[rules.IMAGE])
                asset.width, asset.height = safety.image_size(temp)
                match = safety.banned_match(g.db, temp)
                if match is not None:
                    # a picture on the site's banned list: same consequence as the old upload path
                    safety.ban_uploader(g.db, v, match.ban_reason, match.ban_time or 0)
                    _set_status(asset, rules.REMOVED)
                    g.db.commit()
                    return _error("That image is not allowed here.", 403)
    except rules.MediaError as e:
        _set_status(asset, rules.REMOVED)
        g.db.commit()
        return _media_error(e)
    except AccountLost:
        g.db.rollback()
        _lost(account)
        return _error(RECONNECT, 409, need="storage", settings="/settings/media")
    except MediaGone:
        _set_status(asset, rules.REMOVED)
        g.db.commit()
        return _error("The upload did not arrive. Try again.", 400)
    except ProviderDown:
        g.db.rollback()
        return _error(BUSY, 503)          # still pending: the browser may ask again
    finally:
        if temp:
            safety.discard(temp)

    if restricted:
        # the hosting site took the file but will not show it to anyone else
        _set_status(asset, rules.RESTRICTED)
        g.db.commit()
        return _error("Your video was uploaded to YouTube, but YouTube is keeping it private because this site's "
                      "access is still waiting for YouTube's approval. It cannot be shown in a post yet.", 409,
                      restricted=True, asset=asset.json)

    _set_status(asset, rules.READY)
    account._rotated = False
    g.db.add(account)
    g.db.commit()
    return jsonify(asset.json)


@app.post("/settings/media/dailymotion/video")
@auth_required
@validate_formkey
def settings_media_dailymotion_video(v):
    """How the member's videos show on their Dailymotion channel by default."""
    account = g.db.query(MediaAccount).filter_by(user_id=v.id, provider="dailymotion").first()
    if not account or not account.is_active:
        return redirect("/settings/media")
    account.set_setting("video_visibility", rules.video_visibility(request.form.get("visibility")))
    account.updated_utc = int(time.time())
    g.db.add(account)
    g.db.commit()
    return redirect("/settings/media?msg=Saved.")


@app.post("/settings/media/google/video")
@auth_required
@validate_formkey
def settings_media_google_video(v):
    """How the member's videos show on their YouTube channel by default."""
    account = g.db.query(MediaAccount).filter_by(user_id=v.id, provider="google").first()
    if not account or not account.is_active:
        return redirect("/settings/media")
    account.set_setting("youtube_visibility", rules.video_visibility(request.form.get("visibility")))
    account.updated_utc = int(time.time())
    g.db.add(account)
    g.db.commit()
    return redirect("/settings/media?msg=Saved.")


@app.put("/api/media/dev_upload/<aid>/<token>")
@auth_required
def media_dev_upload(aid, token, v):
    """Where the browser sends the bytes for the stand-in storage (development only).
    A real provider's own address takes this place."""
    if not dev.enabled():
        return _error("Not found.", 404)
    asset = _my_asset(v, aid)
    if not asset or asset.provider != "dev" or asset.status != rules.PENDING or not hmac.compare_digest(asset.token, token):
        return _error("That upload was not found.", 404)
    limit = rules.SIZE_MAX.get(asset.kind, 0)
    written = 0
    with open(dev.file_path(asset), "wb") as out:
        while True:
            block = request.stream.read(64 * 1024)
            if not block:
                break
            written += len(block)
            if written > limit:
                break
            out.write(block)
    if written > limit:
        safety.discard(dev.file_path(asset))
        return _error("That file is too big.", 413)
    return jsonify({"received": written})


# --- showing -----------------------------------------------------------------------

def _refuse(code):
    response = Response("", status=code)
    response.headers["Cache-Control"] = NO_STORE
    return response


@app.get("/media/<aid>/<name>")
def media_file(aid, name):
    """An uploaded image or audio file, fetched from its owner's storage.

Part of a live post or comment: anyone may fetch it and a CDN may keep it for good (the
address never means a different file). Not attached to anything yet: its owner only,
never cached. Anything else is refused, so this is not a file host for other sites.
"""
    parsed = rules.parse_path(request.path)
    if not parsed:
        return _refuse(404)
    asset_id, token, ext = parsed
    asset = g.db.query(MediaAsset).filter_by(id=asset_id).first()
    if not asset or asset.ext != ext or not hmac.compare_digest(asset.token, token):
        return _refuse(404)
    if asset.status != rules.READY:
        return _refuse(410)

    live = attach.is_live(g.db, asset)
    # a file in a post or story made for an audience is only for the people who may see it
    audience, circle_owner, board_id, circle_ids = attach.audience_context(g.db, asset) if live else (0, None, None, ())
    is_owner = False
    allowed = False
    if not live or audience:
        # only now does it matter who is asking; a public file never looks at the session
        v, _ = get_logged_in_user()
        is_owner = bool(v) and v.id == asset.user_id
        if audience == circles.PRIVATE_GUILD:
            # the post page's own rule (Submission.in_private_guild_for): its author, or whoever may view the guild
            board = g.db.query(Board).filter_by(id=board_id).first()
            allowed = bool(v) and (v.id == circle_owner or bool(board and board.can_view(v)))
        else:
            allowed = bool(audience) and circle_guard.may_see(g.db, audience, circle_owner, v, board_id=board_id, circle_ids=circle_ids)
        if allowed and asset.story_id and not is_owner:
            # the profile's own gates, as the story routes ask them (bug hunt: a suspended, private or deleted
            # account's story pictures were still served to whoever held the address)
            from ruqqus.routes.stories import _profile_closed as story_profile_closed
            owner = g.db.query(User).filter_by(id=circle_owner).first()
            allowed = owner is not None and not story_profile_closed(owner, v)
    mode = rules.access(asset.status, live, is_owner, audience, allowed)
    if mode is None:
        return _refuse(404)

    account = g.db.query(MediaAccount).filter_by(id=asset.account_id).first()
    provider = registry.get(asset.provider)
    if provider is None or not provider.served or not account or not account.is_active:
        return _refuse(410)

    byte_range = rules.parse_range(request.headers.get("Range"), asset.size)
    try:
        # the file must still be the one that was checked: its owner can change it in their storage
        if not hmac.compare_digest(provider.checksum(account, asset), asset.checksum):
            raise MediaGone()
        stream = provider.open(account, asset, byte_range)
        _keep_rotated(account)
    except AccountLost:
        _lost(account)                    # nothing about this one file: the whole account is unreachable
        return _refuse(410)
    except MediaGone:
        _set_status(asset, rules.GONE)
        g.db.commit()
        return _refuse(410)
    except ProviderDown:
        return _refuse(503)               # temporary, and never cached
    byte_range = stream.byte_range         # the provider may have answered with the whole file

    response = Response(stream.chunks, status=206 if byte_range else 200)
    response.headers["Content-Type"] = rules.content_type(asset.ext)
    response.headers["Content-Length"] = str(stream.size)
    response.headers["Accept-Ranges"] = "bytes"
    if byte_range:
        response.headers["Content-Range"] = f"bytes {byte_range[0]}-{byte_range[1]}/{stream.total}"
    # whatever the bytes are, the browser treats them as this type and runs nothing in them
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = FOREVER if mode == rules.PUBLIC else NO_STORE
    if mode == rules.PUBLIC:
        g.skip_session_cookie = True      # one answer for everyone: nobody's cookie rides on it
    return response
