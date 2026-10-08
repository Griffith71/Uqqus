"""Linked-account media crosses the models, the schema, the routes and the providers.
These checks keep the layers agreeing and guard the properties that must not drift."""
import re
from pathlib import Path

from ruqqus.helpers.media import rules
from ruqqus.helpers.media.base import Provider

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = read("ruqqus", "routes", "media.py")
MODEL = read("ruqqus", "classes", "media.py")
MAIN = read("ruqqus", "__main__.py")


def schema_columns(sql, header):
    m = re.search(rf"{re.escape(header)} \((.*?)\n\);", sql, re.S)
    assert m, header
    return {line.split()[0] for line in (x.strip() for x in m.group(1).splitlines())
            if line and not line.startswith(("--", "CONSTRAINT"))}


def model_columns(class_name):
    body = MODEL[MODEL.index(f"class {class_name}("):]
    body = body[:body.index("\nclass ", 1)] if "\nclass " in body[1:] else body
    return set(re.findall(r"^    (\w+) = Column\(", body, re.M))


def test_the_models_schema_and_migration_have_the_same_columns():
    schema, migration = read("schema.sql"), read("scripts", "migrations", "2026-10-07_media.sql")
    for class_name, table in (("MediaAccount", "media_accounts"), ("MediaAsset", "media_assets")):
        model = model_columns(class_name)
        assert "status" in model
        assert schema_columns(schema, f"CREATE TABLE public.{table}") == model, table
        # story_id was added later, by the stories migration (2026-10-10_stories.sql), not by the media one
        assert schema_columns(migration, f"CREATE TABLE IF NOT EXISTS {table}") == model - {"story_id"}, table
    assert "ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS story_id integer;" in read("scripts", "migrations", "2026-10-10_stories.sql")
    assert "story_id" in model_columns("MediaAsset")
    for index in ("media_accounts_user_provider_index", "media_assets_submission_id_index", "media_assets_comment_id_index"):
        assert index in schema and index in migration


def test_the_models_are_registered_and_the_routes_loaded():
    assert "from .media import *" in read("ruqqus", "classes", "__init__.py")
    assert "from .media import *" in read("ruqqus", "routes", "__init__.py")


def test_every_provider_named_in_the_rules_fills_in_the_interface():
    from ruqqus.helpers.media import registry
    needed = {name for kinds in rules.PROVIDERS.values() for name in kinds.values()}
    for name in needed & set(registry.PROVIDERS):
        provider = registry.PROVIDERS[name]
        assert isinstance(provider, Provider) and provider.name == name
        assert provider.served == (name in rules.SERVED), name
        for method in ("begin_upload", "finish_upload"):
            assert getattr(type(provider), method) is not getattr(Provider, method), f"{name}.{method}"
        if provider.served:
            for method in ("open", "checksum"):
                assert getattr(type(provider), method) is not getattr(Provider, method), f"{name}.{method}"


def test_the_stand_in_storage_only_exists_when_asked_for(monkeypatch):
    from ruqqus.helpers.media import registry
    monkeypatch.delenv("MEDIA_DEV_PROVIDER", raising=False)
    assert registry.get("dev") is None and "dev" not in registry.account_kinds()
    monkeypatch.setenv("MEDIA_DEV_PROVIDER", "1")
    assert registry.get("dev") is not None and "dev" in registry.account_kinds()
    # and its routes check the same switch
    assert ROUTES.count("if not dev.enabled():") >= 2


def test_a_served_file_is_never_run_and_never_typed_by_the_provider():
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert 'response.headers["Content-Type"] = rules.content_type(asset.ext)' in serve
    assert '"X-Content-Type-Options"] = "nosniff"' in serve
    assert "default-src 'none'; sandbox" in serve


def test_a_public_file_is_cached_for_good_and_a_private_one_never():
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert 'FOREVER if mode == rules.PUBLIC else NO_STORE' in serve
    assert 'FOREVER = "public, max-age=31536000, immutable"' in ROUTES and 'NO_STORE = "private, no-store"' in ROUTES
    # refusals are never cached either, so a 404 cannot stick after the post goes up
    assert 'response.headers["Cache-Control"] = NO_STORE' in ROUTES[ROUTES.index("def _refuse("):ROUTES.index("def media_file(")]
    # and the app's blanket header leaves these answers alone
    assert 'if not request.path.startswith("/media/"):' in MAIN


def test_a_public_file_never_reads_the_session():
    # a cached answer is shared by everyone: who is asking is only looked at for a file nobody attached, or one that
    # is part of a post made for a Circle (and that answer is never cached: see the Circle tests)
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert "@auth_" not in ROUTES[ROUTES.index("# --- showing"):ROUTES.index("def media_file(")]
    assert serve.count("get_logged_in_user()") == 1
    assert serve.index("if not live or audience:") < serve.index("get_logged_in_user()")


def test_a_public_file_carries_nobodys_session_cookie():
    # a CDN will not keep (or worse, will share) an answer that sets a cookie
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert re.search(r"if mode == rules\.PUBLIC:\s+g\.skip_session_cookie = True", serve)
    assert re.search(r'if g\.get\("skip_session_cookie"\):\s+return', MAIN)
    assert "app.session_interface = _SessionInterface()" in MAIN


def test_a_swapped_file_is_not_served():
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert "provider.checksum(account, asset), asset.checksum" in serve
    assert serve.index("provider.checksum(") < serve.index("provider.open(")


def test_uploads_need_a_login_a_formkey_and_respect_the_hourly_cap():
    for name in ("media_upload_begin", "media_upload_complete"):
        head = ROUTES[:ROUTES.index(f"def {name}(")].rsplit("@app.", 1)[1]
        assert "@auth_required" in head and "@validate_formkey" in head and "@limiter.limit" in head, name
    assert "rules.UPLOADS_PER_HOUR" in ROUTES and "rules.may_upload(v)" in ROUTES


def test_no_credential_is_ever_sent_to_a_page_or_an_api_answer():
    assert "refresh_token" not in read("ruqqus", "templates", "settings_media.html")
    json_property = MODEL[MODEL.index("    def json(self):"):]
    assert "token" not in json_property.replace("self.path", "")
    assert "decrypt_secret" not in ROUTES


def test_the_settings_tab_is_reachable():
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/media"') == 2      # desktop and phone tab rows
    assert '@app.get("/settings/media")' in ROUTES


# --- the browser side ----------------------------------------------------------------

UPLOADER = read("ruqqus", "assets", "js", "media_upload.js")
SUBMIT = read("ruqqus", "templates", "submit.html")
COMPOSER = read("ruqqus", "templates", "partials", "inline_composer.html")
POSTS = read("ruqqus", "routes", "posts.py")


def test_the_uploader_talks_to_the_routes_that_exist():
    for address in ("/api/media/status", "/api/media/uploads"):
        assert f"'{address}'" in UPLOADER
        assert f'"{address}"' in ROUTES
    assert "'/api/media/uploads/' + begun.id + '/complete'" in UPLOADER
    assert '"/api/media/uploads/<aid>/complete"' in ROUTES
    # the status answer carries what the script reads
    for key in ('"offered"', '"kinds"'):
        assert key in ROUTES[ROUTES.index("def media_status("):ROUTES.index("def media_upload_begin(")]


def test_the_uploader_is_loaded_wherever_someone_can_write():
    assert "/assets/js/media_upload.js" in read("ruqqus", "templates", "default.html")
    assert SUBMIT.index("post_editor.js") < SUBMIT.index("media_upload.js")     # Create a post is a page of its own


def test_a_pictures_camera_data_never_leaves_the_browser():
    # the server used to strip EXIF (location); now the bytes go straight to the member's storage,
    # so the browser re-draws the picture first and never falls back to the original
    prepare = UPLOADER[UPLOADER.index("function prepare("):UPLOADER.index("function post(")]
    assert "createImageBitmap(file" in prepare and "canvas.toBlob(" in prepare
    assert "REDRAW.test(file.type)" in prepare and "jpeg|png|webp" in UPLOADER
    assert "resolve(file)" not in prepare[prepare.index("createImageBitmap(file"):]
    assert "could not be prepared" in prepare


def test_a_providers_address_never_gets_the_session():
    assert "xhr.withCredentials = upload.url.charAt(0) === '/'" in UPLOADER


def test_the_post_picture_is_named_by_id_and_checked_as_the_authors_own():
    for template, field in ((SUBMIT, "post-media"), (COMPOSER, "ic-media")):
        assert f'data-media-main="{field}"' in template
        assert f'name="media" id="{field}"' in template
    assert 'media_attach.own_asset(g.db, v.id, request.form.get("media"), kinds=("image",))' in POSTS
    # nothing is stored for it, so no thumbnail is made
    assert "main_media is None and (v.is_activated" in POSTS


def test_a_form_is_not_sent_while_its_picture_is_still_uploading():
    assert "data-media-busy" in UPLOADER
    tail = UPLOADER[UPLOADER.index("document.addEventListener('submit'"):]
    assert "event.preventDefault();" in tail and "}, true);" in tail      # capture: before the form's own handler


def test_comment_forms_offer_the_picture_button_only_with_linked_storage():
    for name in ("comments.html", "submission.html"):
        html = read("ruqqus", "templates", name)
        assert 'data-media-upload="image" data-target="comment-form-body-' in html, name
        button = html[:html.index('data-media-upload="image"')]
        assert button.rstrip().endswith("<button type=\"button\" class=\"format btn btn-secondary m-0 ml-1 d-inline-block\"")
        assert "{% if media_offered() %}" in button[-400:], name


def test_removal_tells_the_cdn_to_forget_the_files():
    for path, marker in (("posts.py", "submission_id=post.id"), ("comments.py", "comment_id=c.id"),
                         ("admin_api.py", "submission_id=post.id"), ("admin_api.py", "comment_id=comment.id")):
        src = read("ruqqus", "routes", path)
        assert f"media_cdn.purge(media_attach.attached_paths(g.db, {marker}))" in src, (path, marker)


def test_every_save_point_attaches_and_then_scans():
    for path, count in (("posts.py", 2), ("comments.py", 2)):
        src = read("ruqqus", "routes", path)
        assert src.count("attached_media = media_attach.sync(") == count, path
        assert src.count("media_safety.scan_later(attached_media)") == count, path
        for chunk in src.split("attached_media = media_attach.sync(")[1:]:
            assert chunk.index("g.db.commit()") < chunk.index("media_safety.scan_later(attached_media)"), path


def test_a_banned_picture_is_refused_at_upload_with_the_old_consequence():
    complete = ROUTES[ROUTES.index("def media_upload_complete("):ROUTES.index("def media_dev_upload(")]
    assert "safety.banned_match(g.db, temp)" in complete
    assert "safety.ban_uploader(g.db, v, match.ban_reason, match.ban_time or 0)" in complete
    assert complete.index("safety.banned_match(") < complete.index("_set_status(asset, rules.READY)")


def test_the_preview_only_shows_a_picture_that_is_really_stored():
    # the page's own instant preview would show a picture that was never added (no storage, refused file)
    assert 'data-media-preview="image-preview" data-media-label="filename-show"' in SUBMIT
    handler = SUBMIT[SUBMIT.index("$('#file-upload').on('change'"):]
    assert handler.index("f.hasAttribute('data-media-main')") < handler.index("new FileReader()")
    assert "image.src = asset.path" in UPLOADER and "image.removeAttribute('src')" in UPLOADER


def test_a_picture_that_is_gone_hides_cleanly_however_early_it_fails():
    # a file its owner removed answers 410; the listing's onerror handler must already exist then
    shell = read("ruqqus", "templates", "default.html")
    assert shell.index("function hideBrokenMedia(imgEl)") < shell.index("</head>")
    assert 'onerror="hideBrokenMedia(this)"' in read("ruqqus", "templates", "submission_listing.html")
