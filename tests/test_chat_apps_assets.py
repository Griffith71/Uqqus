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
    for name in ("index.js", "call.js", "space.js"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "watchSignedOut(client, keeper);" in text and "startAgainWhenSignedOut" not in text, name
    assert "export async function ensureSession(boot, kind" in (SRC / "token.js").read_text(encoding="utf-8")


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


def test_this_browsers_devices_are_the_servers_word_and_nothing_is_read_from_storage():
    """Build step 4c dropped the interim of steps 2 and 3: the page no longer reads this browser's devices from its storage and hides
    their Sign out. A browser's chat keeps its store when its device is signed out (token.js), so every device, this sign-in's
    included, is listed with its Sign out, labelled from the server's `here` and `site` (chat plan 3.6)."""
    source = (SRC / "chat_apps.js").read_text(encoding="utf-8")
    devices = source.split("// ---- devices ----")[1]
    assert "from \"./token.js\"" not in source and "readIndex" not in source and "localStorage" not in source
    assert "ownDevices" not in source and "THE INTERIM" not in source and 'window.tr("This browser")' not in source
    assert 'const kind = device.here ? window.tr("This sign-in") : device.site ? window.tr("A browser on this site") : window.tr("An app");' \
        in devices
    loop = devices[devices.index("data.devices.map("):]
    assert "return li;" in loop and loop.index('document.createElement("button")') < loop.index("li.append(button);") < loop.index("return li;")
    assert loop.count("return li;") == 1                          # no early return: every device gets its button


def test_signing_out_this_sign_in_asks_in_the_sites_own_sheet():
    """3.6: pressing Sign out asks first in the site's confirm sheet. The settings shell doesn't carry the sheet (default.html does),
    so the page includes it itself; without it confirmSheet() isn't there and the browser's own confirm() was always what asked."""
    page = (ROOT / "ruqqus" / "templates" / "settings_chat_apps.html").read_text(encoding="utf-8")
    assert '{% include "partials/confirm_sheet.html" %}' in page
    assert page.index("js/confirm_sheet.js") < page.index("js/chat_apps_bundle.js")
    source = (SRC / "chat_apps.js").read_text(encoding="utf-8")
    assert "window.confirmSheet({" in source and "if (!(await ask(question))) return;" in source


def test_what_the_route_and_the_settings_say_about_the_browser_are_true_at_build_step_4():
    """The route's own description, the token flag's comments and the specs' README say what the code does now."""
    route = read("ruqqus", "routes", "chat_apps.py")
    doc = route[:route.index('"""', 3)]
    assert "until build step 4c" not in doc and "deletes its store when its device is signed out" not in doc
    assert "this\nsign-in's included" in doc and "its store kept" in doc and "reads nothing from this browser's storage" in doc
    # build step 5 made every token expire: the switch that kept them from it is gone from all three places
    for where in (read(".env.example"), read("docker-compose.yml"), read("ruqqus", "__main__.py")):
        assert "CHAT_EXPIRING_TOKENS" not in where
    readme = read("scripts", "ui", "README.md")
    assert "two tabs opening together make one device" not in readme
    assert ("two tabs opening together end with one current device (a second may be made: either kept `removed`, or, when its page lost "
            "chat to the other while it asked, signed out with the site and never written to the index, `token.js` dropMinted)") in readme
    # a dropped device's replace is answered {replaced: true}, which names no device: counted, it made three devices of two
    spec = read("scripts", "ui", "chat_session.spec.ts")
    assert "new Set(tokens.map((t) => t.device_id))" not in spec
    assert "new Set(tokens.filter((t) => t.device_id).map((t) => t.device_id)).size" in spec
    assert 'expect(gone.why, `run ${run}: only a device a page dropped is replaced`).toBe("taken_over");' in spec
