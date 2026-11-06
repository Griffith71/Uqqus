"""What one account's chat leaves in a browser is never another's (CLAUDE.md "Chat encryption"). `ensureSession` is run under
node against stand-ins for the browser's storage, so every case is the real function; scripts/ui/chat_session.spec.ts does the same
with two accounts in one real browser, through the Log out links and the chat, call and Space pages."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKEN = ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js"
ASSETS = ROOT / "ruqqus" / "assets"
TEMPLATES = ROOT / "ruqqus" / "templates"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

A, B = "@ruqqus_1:s", "@ruqqus_2:s"
CHAT_STORES = ["matrix-js-sdk::matrix-sdk-crypto", "matrix-js-sdk::matrix-sdk-crypto-meta"]
CALL_STORES = ["uqqus-call::matrix-sdk-crypto", "uqqus-call::matrix-sdk-crypto-meta"]


def session(owner, device="OLD"):
    return json.dumps({"homeserver_url": "https://m", "user_id": owner, "access_token": "t-" + owner, "device_id": device})


HARNESS = """
const store = new Map(Object.entries(%(stored)s));
const deleted = [], sent = [];
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, v),
  removeItem: (k) => store.delete(k),
};
globalThis.indexedDB = {
  deleteDatabase(name) {
    deleted.push(name);
    const request = {};
    setTimeout(() => (%(blocked)s ? request.onblocked() : request.onsuccess()), 0);
    return request;
  },
};
globalThis.window = { tr: (text) => text };
globalThis.fetch = async (url, options) => {
  sent.push(options.body.toString());
  return { ok: %(accepted)s, json: async () => ({ homeserver_url: "https://m", user_id: %(mint)s, access_token: "new", device_id: %(device)s }) };
};
const { ensureSession, STORAGE_KEY, CALL_STORAGE_KEY } = await import(%(module)s);
let answer = null, thrown = null;
try { answer = await ensureSession({ formkey: "f", mxid: %(mxid)s }, %(key)s); } catch (e) { thrown = e.message; }
console.log(JSON.stringify({ answer, thrown, deleted, sent, stored: Object.fromEntries(store) }));
"""


def run(stored, mxid=A, key="STORAGE_KEY", accepted=True, blocked=False, device="NEW"):
    code = HARNESS % {"stored": json.dumps(stored), "mxid": json.dumps(mxid), "mint": json.dumps(mxid), "key": key, "device": json.dumps(device),
                      "accepted": "true" if accepted else "false", "blocked": "true" if blocked else "false",
                      "module": json.dumps(TOKEN.as_uri())}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_the_same_accounts_session_is_used_and_nothing_is_touched():
    got = run({"ruqqus_chat_session": session(A), "uqqus_chat_waiting": "{}"})
    assert got["answer"]["device_id"] == "OLD" and got["sent"] == [] and got["deleted"] == []
    assert got["stored"]["uqqus_chat_waiting"] == "{}"


@node
def test_another_accounts_session_store_and_waiting_messages_go_before_anything_is_used():
    got = run({"ruqqus_chat_session": session(B), "ruqqus_call_session": session(B), "uqqus_chat_waiting": '{"!r":[]}'})
    assert got["answer"] == {"homeserver_url": "https://m", "user_id": A, "access_token": "new", "device_id": "NEW"}
    assert sorted(got["deleted"]) == sorted(CHAT_STORES + CALL_STORES)
    assert "uqqus_chat_waiting" not in got["stored"] and "ruqqus_call_session" not in got["stored"]
    assert json.loads(got["stored"]["ruqqus_chat_session"])["user_id"] == A


@node
def test_a_call_window_on_another_accounts_session_clears_as_much():
    got = run({"ruqqus_chat_session": session(B), "ruqqus_call_session": session(B)}, key="CALL_STORAGE_KEY")
    assert sorted(got["deleted"]) == sorted(CHAT_STORES + CALL_STORES)
    assert json.loads(got["stored"]["ruqqus_call_session"])["user_id"] == A
    assert "ruqqus_chat_session" not in got["stored"]


@node
def test_no_session_at_all_still_clears_the_store_that_goes_with_it():
    # this is a new device, and the SDK refuses a store made for another one
    got = run({})
    assert got["deleted"] == CHAT_STORES and got["answer"]["device_id"] == "NEW"


def logged_out(owner, device="OLD"):
    return json.dumps({"user_id": owner, "device_id": device})                # what logOut() leaves


@node
def test_the_same_account_back_after_a_log_out_takes_up_its_device_and_keeps_its_store():
    got = run({"ruqqus_chat_session": logged_out(A), "ruqqus_call_session": logged_out(A), "uqqus_chat_waiting": "{}"}, device="OLD")
    assert "device_id=OLD" in got["sent"][0]
    assert got["deleted"] == [] and got["answer"]["access_token"] == "new" and got["answer"]["device_id"] == "OLD"
    assert json.loads(got["stored"]["ruqqus_call_session"]) == {"user_id": A, "device_id": "OLD"}


@node
def test_the_same_account_back_on_a_device_signed_out_meanwhile_starts_afresh():
    got = run({"ruqqus_chat_session": logged_out(A)})                         # the server answered another device
    assert got["deleted"] == CHAT_STORES and got["answer"]["device_id"] == "NEW"


@node
def test_another_account_after_a_log_out_clears_what_the_link_left():
    got = run({"ruqqus_chat_session": logged_out(B), "ruqqus_call_session": logged_out(B)})
    assert "device_id" not in got["sent"][0]
    assert sorted(got["deleted"]) == sorted(CHAT_STORES + CALL_STORES)
    assert "ruqqus_call_session" not in got["stored"]


@node
def test_this_accounts_other_window_and_waiting_messages_are_left_alone():
    got = run({"ruqqus_call_session": session(A), "uqqus_chat_waiting": '{"!r":[]}'})
    assert got["deleted"] == CHAT_STORES
    assert json.loads(got["stored"]["ruqqus_call_session"])["device_id"] == "OLD"
    assert got["stored"]["uqqus_chat_waiting"] == '{"!r":[]}'


@node
def test_a_page_the_site_refuses_wipes_nothing():
    # a page left open from before a sign-out has a form key that no longer counts: it can't take the next account's session with it
    got = run({"ruqqus_chat_session": session(B)}, accepted=False)
    assert got["thrown"] and got["deleted"] == []
    assert json.loads(got["stored"]["ruqqus_chat_session"])["user_id"] == B


@node
def test_a_store_another_tab_holds_open_doesnt_hang_the_page():
    got = run({"ruqqus_chat_session": session(B)}, blocked=True)
    assert got["answer"]["device_id"] == "NEW"


@node
def test_a_new_device_never_asks_for_another_accounts_id():
    assert "device_id" not in run({"ruqqus_chat_session": session(B, "THEIRS")})["sent"][0]


def test_the_three_pages_tell_the_bundle_who_is_signed_in():
    assert "mxid: {{ chat_mxid | tojson }}" in (TEMPLATES / "chat.html").read_text(encoding="utf-8")
    assert "chat_mxid=mxid" in (ROOT / "ruqqus" / "routes" / "chat.py").read_text(encoding="utf-8")
    for route in ("calls.py", "spaces.py"):
        assert '"mxid": chat_devices.mxid_of(g.db, v.id)' in (ROOT / "ruqqus" / "routes" / route).read_text(encoding="utf-8")
    for bundle, key in (("index.js", "ensureSession(boot)"), ("call.js", "ensureSession(boot, CALL_STORAGE_KEY)"), ("space.js", "ensureSession(boot, CALL_STORAGE_KEY)")):
        assert key in (ASSETS / "chat_src" / "src" / bundle).read_text(encoding="utf-8")


def test_a_call_spaces_page_data_can_be_written_out():
    # KIND_LABELS[CALL] is lazy, and space.html's `boot | tojson` raises on it: every call's page answered 500
    assert '"kindLabel": str(space_rules.KIND_LABELS[space.kind])' in (ROOT / "ruqqus" / "routes" / "spaces.py").read_text(encoding="utf-8")


def test_every_log_out_link_goes_through_the_one_function():
    links = 0
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for link in re.findall(r"<a [^>]*data-logout[^>]*>", text):
            assert 'onclick="logOut()"' in link, path
            links += 1
        assert "post('/logout'" not in text, path                             # never a way round it
    assert links == 3


def test_log_out_takes_the_token_and_keeps_only_whose_device_this_was():
    script = (ASSETS / "js" / "all_js.js").read_text(encoding="utf-8")
    body = script[script.index("function leaveChatSessions()"):script.index("function post_with_reason")]
    for key in ("uqqus_chat_prekey", "uqqus_chat_waiting"):
        assert f"localStorage.removeItem('{key}')" in body
    assert 'JSON.stringify({"user_id": session.user_id, "device_id": session.device_id})' in body and "access_token" not in body

    token = TOKEN.read_text(encoding="utf-8")
    keys = json.loads(re.search(r"var CHAT_SESSION_KEYS = (\[.*?\]);", script).group(1))
    assert keys == [re.search(r'export const STORAGE_KEY = "(\w+)"', token).group(1), re.search(r'export const CALL_STORAGE_KEY = "(\w+)"', token).group(1)]
    assert 'WAITING_KEY = "uqqus_chat_waiting"' in token
    # the stores are the ones the bundles open
    assert 'cryptoDatabasePrefix: "uqqus-call"' in (ASSETS / "chat_src" / "src" / "call.js").read_text(encoding="utf-8")
    assert 'cryptoDatabasePrefix: "uqqus-call"' in (ASSETS / "chat_src" / "src" / "space.js").read_text(encoding="utf-8")


def test_the_site_never_hands_a_signed_out_device_back():
    """The route's work is helpers/chat_tokens.py (tests/test_chat_signouts.py runs it): a named device is taken up again only after
    the list check, and anything else is a new device the site names."""
    route = (ROOT / "ruqqus" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "chat_tokens.issue(g.db, matrix, v.id, mxid, request.values" in route[route.index("def chat_token(v):"):route.index('_BLOB = re.compile')]
    source = (ROOT / "ruqqus" / "helpers" / "chat_tokens.py").read_text(encoding="utf-8")
    body = source[source.index("def issue("):source.index("def _mint(")]
    assert body.index("matrix.list_devices(mxid)") < body.index("matrix.login_as(mxid, device_id)")
    assert "return _mint(" in body


def test_a_tab_told_its_device_is_gone_clears_only_its_own_device():
    source = (ASSETS / "chat_src" / "src" / "signed_out.js").read_text(encoding="utf-8")
    guard = source.index("stored.device_id === client.getDeviceId()")
    assert guard < source.index("client.clearStores") and guard < source.index("localStorage.removeItem(storageKey)")
    # and a tab whose token is no longer the stored one stops at once, leaving the stores to whoever's they are
    listener = source[source.index('addEventListener("storage"'):]
    assert "client.stopClient()" in listener and "clearStores" not in listener and "stored.access_token === client.getAccessToken()" in listener


# --- pressing Log out, whatever the answer. The real functions from all_js.js, with a stand-in for the request

ALL_JS = (ASSETS / "js" / "all_js.js").read_text(encoding="utf-8")
PRESSED = """
const store = new Map(Object.entries(%(stored)s));
const tabStore = new Map(Object.entries({ uqqus_chat_prekey: "TAB PREKEY" }));
const said = [], reloads = [], order = [], deleted = [];
globalThis.sessionStorage = { removeItem: (k) => { order.push("remove from this tab " + k); tabStore.delete(k); } };
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, v),
  removeItem: (k) => { order.push("remove " + k); store.delete(k); },
};
globalThis.indexedDB = { deleteDatabase: (name) => { deleted.push(name); return {}; } };
globalThis.tr = (text) => text;
globalThis.formkey = () => "f";
globalThis.alert = (text) => said.push(text);
globalThis.window = { location: { reload: () => reloads.push(true) } };
globalThis.FormData = class { append() {} };
globalThis.XMLHttpRequest = class {
  open(method, url) { order.push(method + " " + url); }
  send() {
    order.push("send");
    Object.assign(this, %(answer)s);
    setTimeout(() => {
      if (this.status) { if (this.onload) this.onload(); } else if (this.onerror) { this.onerror(); }
      if (this.onloadend) this.onloadend();
      console.log(JSON.stringify({ said, reloads: reloads.length, order, deleted, stored: Object.fromEntries(store), tab: Object.fromEntries(tabStore) }));
    }, 0);
  }
};
%(script)s
%(call)s
"""


def press_log_out(stored, answer):
    script = ALL_JS[ALL_JS.index("function xhrData("):ALL_JS.index("function post(")] + ALL_JS[ALL_JS.index("var CHAT_SESSION_KEYS"):ALL_JS.index("function post_with_reason")]
    code = PRESSED % {"stored": json.dumps(stored), "answer": json.dumps(answer), "script": script, "call": "logOut();"}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


LEFT_BY_A = {"ruqqus_chat_session": session(A), "ruqqus_call_session": session(A), "uqqus_chat_prekey": "PREKEY", "uqqus_chat_waiting": '{"!r":[]}'}
SITE = "http://site"


def assert_nothing_left_to_use(got):
    assert "uqqus_chat_prekey" not in got["stored"] and "uqqus_chat_waiting" not in got["stored"]
    for key in ("ruqqus_chat_session", "ruqqus_call_session"):
        assert json.loads(got["stored"][key]) == {"user_id": A, "device_id": "OLD"}
    assert got["reloads"] == 1


@node
def test_log_out_that_works_clears_all_of_it_and_says_nothing():
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""})
    assert_nothing_left_to_use(got)
    assert got["said"] == []


@node
def test_the_pre_key_goes_before_the_request_is_made():
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""})
    assert got["order"].index("remove uqqus_chat_prekey") < got["order"].index("send")
    assert got["order"].index("remove from this tab uqqus_chat_prekey") < got["order"].index("send")
    assert got["tab"] == {}


@node
def test_a_sign_in_already_gone_still_clears_and_is_not_called_a_failure():
    # a password change or "log out everywhere" elsewhere: /logout answers 401, which lands on /login, and there is no link to press once the page is drawn again
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/login?redirect=/logout", "response": "<html>"})
    assert_nothing_left_to_use(got)
    assert got["said"] == []


@node
def test_a_home_page_that_fails_after_the_site_has_logged_out_still_clears():
    # the POST's redirect was followed, so the sign-in is over: the page it led to failing changes nothing about that
    got = press_log_out(LEFT_BY_A, {"status": 500, "responseURL": SITE + "/", "response": "<html>"})
    assert_nothing_left_to_use(got)
    assert got["said"] == []


@node
def test_a_log_out_the_site_refused_still_clears_this_browser_and_says_so():
    # the member asked to leave: the server may still hold the sign-in, so they are told, and what they left behind is gone either way
    got = press_log_out(LEFT_BY_A, {"status": 500, "responseURL": SITE + "/logout", "response": "<html>"})
    assert_nothing_left_to_use(got)
    assert got["said"] == ["Something went wrong. Please try again."]


@node
def test_a_log_out_that_never_arrived_still_clears_this_browser_and_says_so():
    got = press_log_out(LEFT_BY_A, {"status": 0, "responseURL": "", "response": ""})
    assert_nothing_left_to_use(got)
    assert len(got["said"]) == 1


# --- a deleted account's device, on the page its deletion answers with

def forget_all(stored):
    script = ALL_JS[ALL_JS.index("var CHAT_SESSION_KEYS"):ALL_JS.index("function post_with_reason")]
    code = PRESSED % {"stored": json.dumps(stored), "answer": "{}", "script": script,
                      "call": "forgetChatDevices(); console.log(JSON.stringify({ deleted, stored: Object.fromEntries(store) }));"}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_a_deleted_accounts_device_goes_altogether():
    got = forget_all({**LEFT_BY_A, "density": "compact"})
    assert got["stored"] == {"density": "compact"}                            # what isn't chat's is left alone
    assert sorted(got["deleted"]) == sorted(CHAT_STORES + CALL_STORES)


def test_only_a_deletion_that_went_through_answers_with_the_page_that_clears():
    settings = (ROOT / "ruqqus" / "routes" / "settings.py").read_text(encoding="utf-8")
    body = settings[settings.index("def close_own_account("):settings.index('@app.route("/settings/blocks"')]
    assert body.index("v.verifyPass(") < body.index('return render_template("settings_security.html"') < body.index("g.db.commit()") < body.index('"account_deleted.html"')
    assert "forgetChatDevices" in (TEMPLATES / "account_deleted.html").read_text(encoding="utf-8")
    assert "forgetChatDevices" not in (TEMPLATES / "settings_security.html").read_text(encoding="utf-8")


# --- two tabs at once

RACE = """
const store = new Map(Object.entries(%(stored)s));
const deleted = [], sent = [];
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, v),
  removeItem: (k) => store.delete(k),
};
globalThis.indexedDB = {
  deleteDatabase(name) {
    deleted.push(name);
    const request = {};
    setTimeout(() => request.onsuccess(), 0);
    return request;
  },
};
globalThis.window = { tr: (text) => text };
// one holder at a time, as the browser's Web Locks: every other request waits its turn
let queue = Promise.resolve();
Object.defineProperty(globalThis, "navigator", { value: %(locks)s, configurable: true });
let made = 0;
globalThis.fetch = async (url, options) => {
  sent.push(options.body.toString());
  made += 1;
  const device = "DEVICE" + made;
  await new Promise((resolve) => setTimeout(resolve, 5));
  return { ok: true, json: async () => ({ homeserver_url: "https://m", user_id: %(mxid)s, access_token: "t" + made, device_id: device }) };
};
const { ensureSession } = await import(%(module)s);
const answers = await Promise.all([ensureSession({ formkey: "f", mxid: %(mxid)s }), ensureSession({ formkey: "f", mxid: %(mxid)s })]);
console.log(JSON.stringify({ answers, sent, stored: Object.fromEntries(store) }));
"""
WITH_LOCKS = """{ locks: { request(name, work) { const run = queue.then(() => work()); queue = run.catch(() => {}); return run; } } }"""


def race(stored, locks=WITH_LOCKS):
    code = RACE % {"stored": json.dumps(stored), "mxid": json.dumps(A), "locks": locks, "module": json.dumps(TOKEN.as_uri())}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_two_tabs_opening_at_once_make_one_device():
    # two mints left a session of one device beside a store of the other, which nothing could open: "Loading..." for good
    got = race({})
    assert len(got["sent"]) == 1
    assert got["answers"][0]["device_id"] == got["answers"][1]["device_id"] == json.loads(got["stored"]["ruqqus_chat_session"])["device_id"]


@node
def test_a_browser_with_no_locks_still_gets_a_session():
    got = race({}, locks="{}")
    assert json.loads(got["stored"]["ruqqus_chat_session"])["user_id"] == A


OPENING = """
const store = new Map(Object.entries(%(stored)s));
const held = new Map(Object.entries(%(held)s));
const deleted = [], sent = [];
const shelf = (map) => ({
  getItem: (k) => (map.has(k) ? map.get(k) : null),
  setItem: (k, v) => map.set(k, v),
  removeItem: (k) => map.delete(k),
});
globalThis.localStorage = shelf(store);
globalThis.sessionStorage = shelf(held);
globalThis.indexedDB = {
  deleteDatabase(name) {
    deleted.push(name);
    const request = {};
    setTimeout(() => request.onsuccess(), 0);
    return request;
  },
};
let reloads = 0;
globalThis.window = { location: { reload: () => { reloads += 1; console.log(JSON.stringify(report("reloaded"))); process.exit(0); } } };
const client = {
  getDeviceId: () => %(device)s,
  initRustCrypto: async (options) => { client.options = options; %(init)s },
};
function report(how, message) {
  return { how, message, reloads, deleted, options: client.options, stored: Object.fromEntries(store), held: Object.fromEntries(held) };
}
const { openCryptoStore, STORAGE_KEY, CALL_STORAGE_KEY } = await import(%(module)s);
try {
  await openCryptoStore(client, %(key)s, %(options)s);
  console.log(JSON.stringify(report("opened")));
} catch (e) {
  console.log(JSON.stringify(report("thrown", e.message)));
}
"""
REFUSED = "throw new Error(\"the account in the store doesn't match the account in the constructor: expected @a:s:ONE, got @a:s:TWO\");"


def open_store(stored, init=REFUSED, held=None, device="OLD", key="STORAGE_KEY", options="undefined"):
    code = OPENING % {"stored": json.dumps(stored), "held": json.dumps(held or {}), "init": init, "device": json.dumps(device), "key": key,
                      "options": options, "module": json.dumps(TOKEN.as_uri())}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@node
def test_a_store_made_for_another_device_is_cleared_and_the_page_starts_again():
    got = open_store({"ruqqus_chat_session": session(A)})
    assert got["how"] == "reloaded" and got["deleted"] == CHAT_STORES and "ruqqus_chat_session" not in got["stored"]


@node
def test_the_call_windows_store_is_the_one_cleared_for_a_call_window():
    got = open_store({"ruqqus_chat_session": session(A), "ruqqus_call_session": session(A)}, key="CALL_STORAGE_KEY", options='{ cryptoDatabasePrefix: "uqqus-call" }')
    assert got["how"] == "reloaded" and got["deleted"] == CALL_STORES
    assert "ruqqus_call_session" not in got["stored"] and "ruqqus_chat_session" in got["stored"]


@node
def test_it_starts_again_once_and_never_loops():
    first = open_store({"ruqqus_chat_session": session(A)})
    second = open_store({"ruqqus_chat_session": session(A)}, held=first["held"])
    assert second["how"] == "thrown" and "doesn't match" in second["message"] and second["reloads"] == 0 and second["deleted"] == []


@node
def test_a_store_that_opens_forgets_the_try_before():
    first = open_store({"ruqqus_chat_session": session(A)})
    got = open_store({"ruqqus_chat_session": session(A)}, init="", held=first["held"])
    assert got["how"] == "opened" and got["held"] == {} and got["deleted"] == []


@node
def test_another_tabs_new_device_is_not_cleared_by_a_page_on_the_old_one():
    # another tab has made a new device since this client was made: this page only starts again and takes that one up
    got = open_store({"ruqqus_chat_session": session(A, "NEWER")}, device="OLD")
    assert got["how"] == "reloaded" and got["deleted"] == [] and "ruqqus_chat_session" in got["stored"]


@node
def test_any_other_failure_opening_the_store_is_left_to_say_so():
    got = open_store({"ruqqus_chat_session": session(A)}, init='throw new Error("quota");')
    assert got["how"] == "thrown" and got["message"] == "quota" and got["deleted"] == [] and "ruqqus_chat_session" in got["stored"]


def test_all_three_bundles_open_the_store_through_the_one_function():
    for bundle in ("index.js", "call.js", "space.js"):
        source = (ASSETS / "chat_src" / "src" / bundle).read_text(encoding="utf-8")
        assert "openCryptoStore(client" in source and "client.initRustCrypto(" not in source, bundle


def test_a_tab_that_lost_the_chat_lock_makes_no_client():
    # the newest tab took over while this one was still getting its session: there was no client yet for the lost lock to stop
    source = (ASSETS / "chat_src" / "src" / "index.js").read_text(encoding="utf-8")
    assert "chatLockLost = true" in source[source.index("function holdChatLock()"):source.index("async function main()")]
    main = source[source.index("async function main()"):]
    assert main.index("ensureSession(boot)") < main.index("if (chatLockLost) return") < main.index("sdk.createClient(")
    after_store = main[main.index("openCryptoStore(client"):]
    assert after_store.index("if (chatLockLost) return") < after_store.index("client.startClient(")
