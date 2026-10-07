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
        assert schema_columns(migration, f"CREATE TABLE IF NOT EXISTS {table}") == model, table
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
    # a cached answer is shared by everyone: who is asking is only looked at for a file nobody attached
    serve = ROUTES[ROUTES.index("def media_file("):]
    assert "@auth_" not in ROUTES[ROUTES.index("# --- showing"):ROUTES.index("def media_file(")]
    assert serve.index("if not live:") < serve.index("get_logged_in_user()")


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
