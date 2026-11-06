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
    assert 'aria-label="{{ _(\'Chat apps\') }}"' in nav


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
    """Every credential event goes through account_sessions.revoke, which queues the chat sign-out (helpers/chat_signouts.py;
    tests/test_revocation_assets.py has the tripwires, tests/pg/test_chat_signout_routes.py runs them)."""
    settings = read("ruqqus", "routes", "settings.py")
    login = read("ruqqus", "routes", "login.py")
    assert 'account_sessions.revoke(g.db, v, "password", session=flask_session, spare_this_sign_in=True)' in settings
    assert 'account_sessions.revoke(g.db, v, "others", session=flask_session, spare_this_sign_in=True)' in settings
    assert settings.count('account_sessions.revoke(g.db, v, "mfa", session=flask_session, spare_this_sign_in=True)') == 2, "two-factor on, off"
    assert 'account_sessions.revoke(g.db, v, "deleted")' in settings, "deleting the account"
    assert 'account_sessions.revoke(g.db, user, "reset")' in login, "a password reset"
    assert 'account_sessions.revoke(g.db, account, "mfa_recovery")' in login, "the recovery code"
    assert 'account_sessions.revoke(g.db, row, "child_password")' in read("ruqqus", "routes", "children_pages.py")
    assert "chat_devices.sign_out" not in settings + login
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


# ---- helpers/chat_devices.py with the chat server faked (signing out: tests/test_chat_signouts.py) ----

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


def test_no_chat_account_means_no_name_to_set(devices):
    chat_devices, calls = devices
    chat_devices.sync_name(FakeDb(None), SimpleNamespace(id=3, username="bob", is_deleted=False))
    assert calls["named"] == []
    assert not hasattr(chat_devices, "sign_out"), "signing out is the durable queue's (helpers/chat_signouts.py)"


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
    chat_devices.sync_name(FakeDb("@ruqqus_3:x"), SimpleNamespace(id=3, username="bob", is_deleted=False))
    assert "not done" in capsys.readouterr().out


def test_the_sites_own_devices_are_told_from_apps():
    from ruqqus.helpers.chat_devices import is_site_device
    assert is_site_device("Laba chat", "Laba") and is_site_device("", "Laba") and is_site_device(None, "Laba")
    assert not is_site_device("Element Web", "Laba") and not is_site_device("Labasomething", "Laba")


def test_this_sign_in_is_what_the_server_says_and_every_other_device_can_be_signed_out():
    """`here` is read from chat_site_devices (the row's sign_in is this cookie's), and it is a label, never a reason to hide: a copy
    of the cookie is the same sign-in (chat plan 3.6)."""
    source = (SRC / "chat_apps.js").read_text(encoding="utf-8")
    devices = source.split("// ---- devices ----")[1]
    assert 'device.here ? window.tr("This sign-in")' in devices
    assert "if (device.device_id !== here)" not in devices and "li.append(button);" in devices
    assert 'window.tr("This signs out chat in this browser too, if it is this browser\'s. It will start again as a new device.")' in devices
    assert "resp.status === 202" in devices and 'window.tr("This device will be signed out shortly.")' in devices
    route = read("ruqqus", "routes", "chat_apps.py")
    assert '"here": bool(row and not row[0] and sign_in and row[1] == sign_in)' in route
    assert '"site": row is not None or is_site_device(d["name"], site_name)' in route
    assert "chat_signouts.sign_in_hash(flask_session.get(\"session_id\"))" in route
    body = route[route.index("def chat_device_sign_out(v):"):route.index("def _not_now(")]
    # the site's row is revoked and committed BEFORE Synapse deletes the device (a token request in flight can't re-create it), and only
    # with the migration in (no 500 without it); what can't be done now is queued, and said to be only when something was kept
    revoke = body.index('chat_signouts.revoke_device(g.db, v.id, device_id, "signed_out_here")')
    assert body.rindex("if ready and", 0, revoke) < revoke < body.index("g.db.commit()", revoke) < body.index("matrix.delete_device(")
    assert "ready = chat_signouts.tables_ready(g.db)" in body and "revoke_rows" not in body
    later = route[route.index("def _sign_out_later("):]
    assert 'if chat_signouts.queue(g.db, v.id, "signed_out_here", "listed", device_ids=[device_id], mxid=mxid) is None:' in later


def test_in_the_interim_this_browsers_own_devices_are_labelled_from_its_storage_and_never_offered_sign_out():
    """Build steps 2 and 3 (helpers/chat_signouts.py KEEP_SITE_BROWSERS): today's chat page and call window delete their crypto store when
    their device is signed out, and today's client asks the token route only when it has no token, so the server's `here` is often an
    older sign-in's. This browser's own chat and call devices are read from its storage, as before, and get no Sign out; step 4c drops it."""
    source = (SRC / "chat_apps.js").read_text(encoding="utf-8")
    devices = source.split("// ---- devices ----")[1]
    assert 'import { CALL_STORAGE_KEY, loadSession } from "./token.js";' in source
    assert "new Set([(loadSession() || {}).device_id, (loadSession(CALL_STORAGE_KEY) || {}).device_id].filter(Boolean))" in devices
    assert 'const own = ownDevices.has(device.device_id);' in devices
    assert 'own ? window.tr("This browser") : device.here ? window.tr("This sign-in")' in devices
    assert devices.index("if (own) return li;") < devices.index('document.createElement("button")') < devices.index("li.append(button);")
    assert "THE INTERIM" in devices and "Build step 4c removes" in devices
