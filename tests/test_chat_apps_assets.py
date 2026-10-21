"""Chat in outside Matrix apps (Settings > Chat apps, "Sign in with <site>", emoji verification, chat names and signing
out): the wiring that ties the pieces together, and helpers/chat_devices.py with the chat server faked. The rules are
tests/test_oidc_provider.py and tests/test_synapse_rules.py; the flow on a real app is tests/pg/test_oidc.py."""
import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ruqqus" / "assets" / "chat_src" / "src"


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_routes_are_loaded_and_the_settings_menu_links_the_page():
    routes = read("ruqqus", "routes", "__init__.py")
    assert "from .chat_apps import *" in routes and "from .oidc_pages import *" in routes
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/chat_apps"') == 2, "desktop and phone menus"
    assert 'aria-label="Chat apps"' in nav


def test_the_pages_never_write_html_from_data():
    for name in ("chat_apps.js", "verification.js", "signed_out.js"):
        source = (SRC / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source, name
    for name in ("settings_chat_apps.html", "oidc_consent.html"):
        page = read("ruqqus", "templates", name)
        assert "| safe" not in page and "|safe" not in page, name
        assert "Laba" not in page, f"{name}: the site's name comes from SITE_NAME"


def test_the_security_key_is_opened_only_in_the_browser():
    source = (SRC / "chat_apps.js").read_text(encoding="utf-8")
    assert re.search(r'import \{[^}]*prekeyFromPassword[^}]*\} from "\./keylock\.js"', source)
    assert "fetch(" not in source.split("// ---- devices ----")[0].replace("fetchKeyInfo", ""), \
        "the key part asks the site only through keylock.js (check the password, the locked copy)"


def test_the_bundle_is_built_shipped_and_untracked():
    build = json.loads(read("ruqqus", "assets", "chat_src", "package.json"))["scripts"]["build"]
    assert "esbuild src/chat_apps.js --bundle" in build and "--outfile=../js/chat_apps_bundle.js" in build
    assert "/ruqqus/assets/js/chat_apps_bundle.js ./ruqqus/assets/js/chat_apps_bundle.js" in read("Dockerfile")
    ignored = read(".gitignore")
    assert "ruqqus/assets/js/chat_apps_bundle.js" in ignored and "/synapse/oidc_client_secret" in ignored
    assert "asset_url('js/chat_apps_bundle.js')" in read("ruqqus", "templates", "settings_chat_apps.html")


def test_a_signed_out_page_starts_again_on_every_matrix_page():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert "startAgainWhenSignedOut(client, STORAGE_KEY);" in index
    for name in ("call.js", "space.js"):
        assert 'startAgainWhenSignedOut(client, CALL_STORAGE_KEY, "uqqus-call");' in (SRC / name).read_text(encoding="utf-8"), name
    assert "export const STORAGE_KEY" in (SRC / "token.js").read_text(encoding="utf-8")


def test_the_chat_page_verifies_only_its_own_devices_by_emoji():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert 'verificationMethods: ["m.sas.v1"],' in index and "answerOwnVerifications(client);" in index
    source = (SRC / "verification.js").read_text(encoding="utf-8")
    assert "request.isSelfVerification" in source and "VerificationPhase.Requested" in source
    assert "sas.confirm()" in source and "sas.mismatch()" in source


def test_both_stylesheets_draw_the_dialogs():
    for name in ("main.scss", "main_dark.scss"):
        sheet = read("ruqqus", "assets", "style", name)
        for rule in (".chat-key-dialog {", ".chat-key-box {", ".chat-key-code {", ".chat-sas {", ".chat-sas-emoji {"):
            assert rule in sheet, (name, rule)


def test_devices_are_named_and_accounts_made_without_a_spare_device():
    client = read("ruqqus", "helpers", "matrix_client.py")
    assert '"inhibit_login": True' in client
    assert '"initial_device_display_name": site_device_name()' in client
    assert "/_synapse/client/uqqus/display_name" in client


def test_chat_apps_sign_out_wherever_the_site_ends_other_sign_ins():
    settings = read("ruqqus", "routes", "settings.py")
    login = read("ruqqus", "routes", "login.py")
    assert settings.count('chat_devices.sign_out(g.db, v.id, app.config["SITE_NAME"])') == 2, "password change, log out others"
    assert 'chat_devices.sign_out(g.db, v.id, app.config["SITE_NAME"], apps_only=False)' in settings, "deleting the account"
    assert 'chat_devices.sign_out(g.db, user.id, app.config["SITE_NAME"])' in login, "a password reset"
    assert settings.count("chat_devices.sync_name(g.db, v)") == 2, "a username change, deleting the account"
    assert "chat_devices.sync_name(g.db, v, mxid)" in read("ruqqus", "routes", "chat.py"), "a new chat account"


def test_the_chat_server_signs_in_only_through_the_site():
    config = read("synapse", "homeserver.yaml")
    block = config[config.index("oidc_providers:"):]
    for line in ("allow_existing_users: true", "enable_registration: false", "pkce_method: always", "discover: false",
                 "client_secret_path: /data/oidc_client_secret", 'localpart_template: "ruqqus_{{ user.sub }}"',
                 'scopes: ["openid"]'):
        assert line in block, line
    assert re.search(r"^password_config:\n  enabled: false$", config, re.M)
    compose = read("docker-compose.yml")
    assert "OIDC_CLIENT_SECRET=${OIDC_CLIENT_SECRET:-}" in compose and "OIDC_PRIVATE_KEY=${OIDC_PRIVATE_KEY:-}" in compose
    assert '"./synapse/oidc_client_secret:/data/oidc_client_secret:ro"' in compose
    env = read(".env.example")
    assert "OIDC_CLIENT_SECRET=" in env and "OIDC_PRIVATE_KEY=" in env
    gen = read("scripts", "gen_dev_env.py")
    assert '"OIDC_CLIENT_SECRET"' in gen and '"OIDC_PRIVATE_KEY"' in gen and "OIDC_SECRET_FILE" in gen


# ---- helpers/chat_devices.py with the chat server faked ----

@pytest.fixture
def devices(monkeypatch):
    calls = {"deleted": [], "named": []}
    fake = types.ModuleType("ruqqus.helpers.matrix_client")
    fake.list_devices = lambda mxid: [{"device_id": "SITE1", "name": "Laba chat"}, {"device_id": "OLD", "name": ""},
                                      {"device_id": "APP1", "name": "Element Web"}, {"device_id": "APP2", "name": "FluffyChat"}]
    fake.delete_device = lambda mxid, device_id: calls["deleted"].append((mxid, device_id))
    fake.set_display_name = lambda mxid, name: calls["named"].append((mxid, name))
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.matrix_client", fake)
    import ruqqus.helpers
    monkeypatch.setattr(ruqqus.helpers, "matrix_client", fake, raising=False)
    from ruqqus.helpers import chat_devices
    return chat_devices, calls


class FakeDb:
    def __init__(self, mxid):
        self.mxid = mxid

    def execute(self, statement, params):
        assert "chat_identities" in str(statement)
        return SimpleNamespace(first=lambda: (self.mxid,) if self.mxid else None)


def test_a_password_change_signs_out_apps_and_keeps_the_sites_browsers(devices):
    chat_devices, calls = devices
    assert chat_devices.sign_out(FakeDb("@ruqqus_3:x"), 3, "Laba") == 2
    assert calls["deleted"] == [("@ruqqus_3:x", "APP1"), ("@ruqqus_3:x", "APP2")]


def test_deleting_the_account_signs_out_every_device(devices):
    chat_devices, calls = devices
    assert chat_devices.sign_out(FakeDb("@ruqqus_3:x"), 3, "Laba", apps_only=False) == 4


def test_no_chat_account_means_nothing_to_do(devices):
    chat_devices, calls = devices
    assert chat_devices.sign_out(FakeDb(None), 3, "Laba") == 0 and calls["deleted"] == []
    chat_devices.sync_name(FakeDb(None), SimpleNamespace(id=3, username="bob", is_deleted=False))
    assert calls["named"] == []


def test_the_chat_name_is_the_handle_or_deleted(devices):
    chat_devices, calls = devices
    chat_devices.sync_name(FakeDb("@ruqqus_3:x"), SimpleNamespace(id=3, username="bob", is_deleted=False))
    chat_devices.sync_name(FakeDb("@ruqqus_3:x"), SimpleNamespace(id=3, username="bob", is_deleted=True))
    assert calls["named"] == [("@ruqqus_3:x", "bob"), ("@ruqqus_3:x", "Deleted account")]


def test_a_chat_server_failure_never_stops_the_site(devices, capsys):
    chat_devices, calls = devices
    broken = types.ModuleType("ruqqus.helpers.matrix_client")

    def boom(*a):
        raise RuntimeError("down")
    broken.list_devices = broken.delete_device = broken.set_display_name = boom
    sys.modules["ruqqus.helpers.matrix_client"] = broken
    import ruqqus.helpers
    ruqqus.helpers.matrix_client = broken
    assert chat_devices.sign_out(FakeDb("@ruqqus_3:x"), 3, "Laba") == 0
    chat_devices.sync_name(FakeDb("@ruqqus_3:x"), SimpleNamespace(id=3, username="bob", is_deleted=False))
    assert "not done" in capsys.readouterr().out


def test_the_sites_own_devices_are_told_from_apps():
    from ruqqus.helpers.chat_devices import is_site_device
    assert is_site_device("Laba chat", "Laba") and is_site_device("", "Laba") and is_site_device(None, "Laba")
    assert not is_site_device("Element Web", "Laba") and not is_site_device("Labasomething", "Laba")
