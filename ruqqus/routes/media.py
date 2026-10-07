"""Media stored in a member's own linked account: the settings page, the upload
endpoints the browser talks to, and the address the files are shown at.

The rules are in helpers/media/rules.py and the providers next to it. Nothing uploaded
is kept on this server: the browser sends the bytes to the provider, and /media/...
fetches them from there when someone looks (a CDN in front of the site keeps the answer).
"""
import hmac
import secrets
import time

from flask import Response, g, jsonify, redirect, render_template, request

from ruqqus.__main__ import app, limiter
from ruqqus.classes import MediaAccount, MediaAsset
from ruqqus.helpers.media import attach, dev, registry, rules, safety
from ruqqus.helpers.media.base import MediaGone
from ruqqus.helpers.wrappers import auth_required, get_logged_in_user, validate_formkey

NO_STORE = "private, no-store"
FOREVER = "public, max-age=31536000, immutable"


def _error(message, code=400, **extra):
    return jsonify({"error": message, **extra}), code


def _accounts(v):
    return g.db.query(MediaAccount).filter_by(user_id=v.id).order_by(MediaAccount.id).all()


def _account_for(v, kind):
    """The member's linked account that takes this kind, with its provider."""
    for account in _accounts(v):
        if not account.is_active:
            continue
        name = rules.provider_for(account.provider, kind)
        provider = registry.get(name) if name else None
        if provider is not None:
            return account, provider
    return None, None


def _my_asset(v, raw_id):
    try:
        asset_id = int(raw_id, 36)
    except (TypeError, ValueError):
        return None
    return g.db.query(MediaAsset).filter_by(id=asset_id, user_id=v.id).first()


def _set_status(asset, status):
    if asset.status != status and rules.can_change(asset.status, status):
        asset.status = status
        asset.updated_utc = int(time.time())
        g.db.add(asset)


# --- settings ----------------------------------------------------------------------

@app.get("/settings/media")
@auth_required
def settings_media(v):
    accounts = {a.provider: a for a in _accounts(v) if a.is_active}
    return render_template("settings_media.html", v=v, accounts=accounts,
                           offered=registry.account_kinds(),
                           error=request.args.get("error"), msg=request.args.get("msg"))


@app.post("/settings/media/dev/enable")
@auth_required
@validate_formkey
def settings_media_dev_enable(v):
    """Link the stand-in storage (development only, see helpers/media/dev.py)."""
    if not dev.enabled():
        return redirect("/settings/media?error=That+storage+is+not+available+here.")
    _link(v, "dev", external_id=f"dev-{v.id}")
    g.db.commit()
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
    kinds = [k for k in rules.KINDS if _account_for(v, k)[1] is not None]
    return jsonify({"kinds": kinds, "limits": {k: rules.SIZE_MAX[k] for k in kinds if k in rules.SIZE_MAX},
                    "settings": "/settings/media"})


@app.post("/api/media/uploads")
@limiter.limit("60/minute")
@auth_required
@validate_formkey
def media_upload_begin(v):
    """Start an upload into the member's linked account.

Form data: `kind` (image, audio or video), `filename`, `size` (bytes).
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

    account, provider = _account_for(v, kind)
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
    try:
        upload = provider.begin_upload(account, asset, request.host_url.rstrip("/"))
    except rules.MediaError as e:
        g.db.rollback()
        return _error(e.message, e.code)
    g.db.add(asset)
    g.db.commit()
    return jsonify({"id": rules.b36(asset.id), "upload": upload})


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
    try:
        info = provider.finish_upload(account, asset)
        asset.provider_ref = info["ref"]
        asset.size = int(info["size"])
        asset.checksum = info["checksum"]
        if provider.served:
            head = b"".join(provider.open(account, asset, (0, min(asset.size, 64) - 1)).chunks)
            asset.ext = rules.check_uploaded(asset.kind, head, asset.size)
            if asset.kind == rules.IMAGE:
                temp = safety.fetch_to_temp(provider, account, asset, rules.SIZE_MAX[rules.IMAGE])
                asset.width, asset.height = safety.image_size(temp)
    except rules.MediaError as e:
        _set_status(asset, rules.REMOVED)
        g.db.commit()
        return _error(e.message, e.code)
    except MediaGone:
        _set_status(asset, rules.REMOVED)
        g.db.commit()
        return _error("The upload did not arrive. Try again.", 400)
    finally:
        if temp:
            safety.discard(temp)

    _set_status(asset, rules.READY)
    g.db.commit()
    return jsonify(asset.json)


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
    is_owner = False
    if not live:
        # only now does it matter who is asking; a public file never looks at the session
        v, _ = get_logged_in_user()
        is_owner = bool(v) and v.id == asset.user_id
    mode = rules.access(asset.status, live, is_owner)
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
    except MediaGone:
        _set_status(asset, rules.GONE)
        g.db.commit()
        return _refuse(410)

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
