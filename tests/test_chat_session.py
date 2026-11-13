"""What one account's chat leaves in a browser is never another's (CLAUDE.md "Chat encryption", the chat plan's phase 2): each account's
devices are kept per device in the index (token.js `uqqus_chat_devices`), locked, and none is ever used for another account. `ensureSession`
is run under node against stand-ins for the browser (tests/chat_node_harness.py), so every case is the real function; tests/test_chat_index.py
holds the index's own rules, and scripts/ui/chat_session.spec.ts does the same with two accounts in one real browser."""
import json
import re
import subprocess

from chat_node_harness import A, B, NEW, OLD, ROOT, node, prefix, run, stores

TOKEN = ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js"
ASSETS = ROOT / "ruqqus" / "assets"
TEMPLATES = ROOT / "ruqqus" / "templates"

CHAT_STORES = ["matrix-js-sdk::matrix-sdk-crypto", "matrix-js-sdk::matrix-sdk-crypto-meta"]
CALL_STORES = ["uqqus-call::matrix-sdk-crypto", "uqqus-call::matrix-sdk-crypto-meta"]


def session(owner, device="OLD"):
    """What the client from before the index (build step 4) kept, which chat and Log out clear."""
    return json.dumps({"homeserver_url": "https://m", "user_id": owner, "access_token": "t-" + owner, "device_id": device})


START = 'const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat"));\n'


# --- one browser, two accounts: each keeps its own devices, locked; neither is ever given the other's (tests/test_chat_index.py has the rules)

@node
def test_another_accounts_devices_and_stores_are_kept_locked_and_never_used():
    got = run("""
setIndex("@ruqqus_2:s", [
  entry("@ruqqus_2:s", "UQBBB0000000", "chat", { x: 1, sealed: await boxFor("@ruqqus_2:s", "UQBBB0000000", "chat", "b-token") }),
  entry("@ruqqus_2:s", "UQBBC0000000", "call", { sealed: await boxFor("@ruqqus_2:s", "UQBBC0000000", "call", "b-call") }),
]);
store.set("uqqus_chat_waiting", JSON.stringify({ v: 2, u: "@ruqqus_2:s", iv: "x", ct: "y" }));
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + """
const waiting = await T.readWaiting(got.value);
report({ got, waiting });
""", dbs=stores(prefix(B, "UQBBB0000000")) + stores(prefix(B, "UQBBC0000000", "call")))
    assert "device_id" not in got["sent"][0]["body"]                          # never another account's device
    assert got["got"]["value"]["device_id"] == NEW and got["got"]["value"]["accessToken"] == "tok-" + NEW
    assert not any(name.startswith("uqqus-") for name in got["deleted"])      # another account's store is KEPT
    theirs = [e for e in got["index"]["entries"] if e["u"] == B]
    assert [e["s"] for e in theirs] == ["out", "out"] and not any("sealed" in e for e in theirs)
    assert got["waiting"] == {} and "uqqus_chat_waiting" not in got["stored"]  # their waiting messages are theirs: they go


@node
def test_signing_back_in_takes_up_the_same_device_and_its_store():
    got = run("""
setIndex(null, [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out", x: 1 }), entry("@ruqqus_2:s", "UQBBB0000000", "chat", { s: "out" })], 2);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000") });
""" + START + "report({ got });", dbs=stores(prefix(A, OLD)))
    assert got["sent"][0]["body"]["device_id"] == OLD and got["got"]["value"]["device_id"] == OLD
    assert not any(name.startswith("uqqus-") for name in got["deleted"])
    mine = [e for e in got["index"]["entries"] if e["u"] == A]
    assert [(e["s"], e["x"]) for e in mine] == [("in", 1)] and got["index"]["owner"] == A


@node
def test_a_qr_sign_in_that_needs_its_password_writes_nothing():
    stored = {"uqqus_chat_devices": json.dumps({"v": 2, "owner": B, "epoch": 0, "entries": []}), "ruqqus_chat_session": session(B)}
    got = run("""
serve = async () => ({ status: 403, body: { need_password: true, error: "Enter your password first." } });
""" + START + "report({ got });", local=stored, dbs=CHAT_STORES)
    assert got["got"]["thrown"] == "PasswordNeeded" and got["got"]["message"] == "Enter your password first."
    assert got["stored"] == stored and got["deleted"] == []


@node
def test_a_new_device_for_the_same_account_keeps_the_old_store():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out" })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", { extra: { previous: { device_id: "UQOLD0000000", reason: "others" } } }) });
""" + START + "report({ got });", dbs=stores(prefix(A, OLD)))
    assert [(e["d"], e["s"]) for e in got["index"]["entries"]] == [(OLD, "removed"), (NEW, "in")]
    assert not any(name.startswith("uqqus-") for name in got["deleted"])


@node
def test_a_changed_key_deletes_exactly_that_store():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { kid: "kid0", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }),
                          entry("@ruqqus_2:s", "UQBBB0000000", "chat", { s: "out" })]);
serve = async (r) => r.body.mode === "replace" ? { body: { replaced: true } }
  : { body: answer("@ruqqus_1:s", r.body.device_id || "UQNEW0000000", { access_token: r.body.device_id ? null : "t" }) };
""" + START + "report({ got });", dbs=stores(prefix(A, OLD)) + stores(prefix(B, "UQBBB0000000")))
    assert [r["body"]["mode"] for r in got["sent"]] == ["boot", "replace", "boot"]
    assert sorted(name for name in got["deleted"] if name.startswith("uqqus-")) == sorted(stores(prefix(A, OLD)))


def test_the_three_pages_tell_the_bundle_who_is_signed_in():
    assert "mxid: {{ chat_mxid | tojson }}" in (TEMPLATES / "chat.html").read_text(encoding="utf-8")
    assert "chat_mxid=mxid" in (ROOT / "ruqqus" / "routes" / "chat.py").read_text(encoding="utf-8")
    for route in ("calls.py", "spaces.py"):
        assert '"mxid": chat_devices.mxid_of(g.db, v.id)' in (ROOT / "ruqqus" / "routes" / route).read_text(encoding="utf-8")
    for bundle, key in (("index.js", 'ensureSession(boot, "chat", {'), ("call.js", 'ensureSession(boot, "call")'), ("space.js", 'ensureSession(boot, "call")')):
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
    body = script[script.index("function leaveChatSessions()"):script.index("function forgetChatPrekey()")]
    assert "localStorage.removeItem(CHAT_WAITING_KEY)" in body and "forgetChatPrekey();" in body
    assert "delete e.sealed;" in body and 'if (e.s === "in") e.s = "out";' in body and "idx.owner = null;" in body
    assert "idx.epoch = (Number.isInteger(idx.epoch) ? idx.epoch : 0) + 1;" in body
    assert "deleteDatabase" not in body and "access_token" not in body
    # Log out leaves the sessions under the lock, at most 3 s, and only then draws the page again
    log_out = script[script.index("function logOut()"):script.index("function deleteChatDatabase(")]
    assert log_out.index("underChatSessionLock(leaveChatSessions, 3000).then(") < log_out.index("window.location.reload(true)")
    assert "deleteChatStore" not in log_out and "forgetChat" not in log_out.replace("forgetChatPrekey();", "")

    token = TOKEN.read_text(encoding="utf-8")
    # all_js.js writes the index chat keeps, by the same names
    for name, value in (("CHAT_INDEX_KEY", "INDEX_KEY"), ("CHAT_WAITING_KEY", "WAITING_KEY"), ("CHAT_STORE_LOCK", "STORE_LOCK"),
                        ("CHAT_LEGACY_KEYS", "LEGACY_KEYS")):
        mine = re.search(rf"var {name} = (.*?);", script).group(1)
        theirs = re.search(rf"(?:export )?const {value} = (.*?);", token).group(1)
        assert json.loads(mine) == json.loads(theirs), name
    assert re.search(r'const SESSION_LOCK = "(.*?)";', token).group(1) == re.search(r'var CHAT_SESSION_LOCK = "(.*?)";', script).group(1)
    assert json.loads(re.search(r"var CHAT_LEGACY_STORES = (\[.*?\]);", script).group(1)) == \
        json.loads(re.search(r"const LEGACY_STORES = (\[.*?\]);", token).group(1))
    assert 'WAITING_KEY = "uqqus_chat_waiting"' in token
    # every store is opened under its own name, made from the account and the device
    assert "cryptoDatabasePrefix: session.prefix" in token and "return `uqqus-${kind}-${local[1]}-${device}`;" in token
    assert "CHAT_SESSION_KEYS" not in script and "CHAT_CRYPTO_STORES" not in script


def test_the_site_never_hands_a_signed_out_device_back():
    """The route's work is helpers/chat_tokens.py (tests/test_chat_signouts.py runs it): a named device is taken up again only after
    the list check, and anything else is a new device the site names."""
    route = (ROOT / "ruqqus" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "chat_tokens.issue(g.db, matrix, v.id, mxid, request.values" in route[route.index("def chat_token(v):"):route.index('_BLOB = re.compile')]
    source = (ROOT / "ruqqus" / "helpers" / "chat_tokens.py").read_text(encoding="utf-8")
    body = source[source.index("    def token_for("):source.index("    def served(")]
    assert body.index("self.matrix.list_devices(self.mxid)") < body.index("self.matrix.login_as(self.mxid, device_id)")
    run = source[source.index("    def run("):source.index("    def retried(")]
    assert "return self.mint(req, previous)" in run


def test_a_tab_told_its_device_is_gone_deletes_nothing():
    """A store is kept, locked, for its account (the plan's 2.4): nothing on SessionLoggedOut or an index change deletes one."""
    source = (ASSETS / "chat_src" / "src" / "signed_out.js").read_text(encoding="utf-8")
    assert "clearStores" not in source and "removeItem" not in source and "deleteDatabase" not in source
    assert "client.stopClient()" in source and "HttpApiEvent.SessionLoggedOut" in source
    for name in ("index.js", "call.js", "space.js", "token.js", "signed_out.js"):
        text = (ASSETS / "chat_src" / "src" / name).read_text(encoding="utf-8")
        assert "clearStores" not in text and "forgetDevice" not in text, name


# --- pressing Log out, whatever the answer. The real functions from all_js.js, with stand-ins for the request, storage, IndexedDB and Web Locks

ALL_JS = (ASSETS / "js" / "all_js.js").read_text(encoding="utf-8")
CHAT_PART = ALL_JS[ALL_JS.index("var CHAT_INDEX_KEY"):ALL_JS.index("function post_with_reason")]
PRESSED = """
const store = new Map(Object.entries(%(stored)s));
const tabStore = new Map(Object.entries({ uqqus_chat_prekey: "TAB PREKEY" }));
const said = [], reloads = [], order = [], deleted = [], lockLog = [];
const dbs = new Set(%(dbs)s), failing = new Set(%(failing)s);
globalThis.sessionStorage = { removeItem: (k) => { order.push("remove from this tab " + k); tabStore.delete(k); } };
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => { order.push("write " + k); store.set(k, String(v)); },
  removeItem: (k) => { order.push("remove " + k); store.delete(k); },
  key: (i) => [...store.keys()][i] ?? null,
  get length() { return store.size; },
};
globalThis.indexedDB = {
  deleteDatabase(name) {
    const request = {};
    setTimeout(() => {
      if (failing.has(name)) { request.error = new Error("no"); request.onerror && request.onerror(); return; }
      deleted.push(name); order.push("delete " + name); dbs.delete(name); request.onsuccess && request.onsuccess();
    }, 0);
    return request;
  },
  databases: async () => [...dbs].map((name) => ({ name })),
};
// Web Locks: one holder per name; `ifAvailable` answers null at once when held; a `signal` that fires while waiting rejects
const busy = new Set(), queues = new Map(), outside = new Set(%(held)s);
const LOCKS = {
  request(name, options, work) {
    if (typeof options === "function") { work = options; options = {}; }
    if (options.ifAvailable && (busy.has(name) || outside.has(name))) return Promise.resolve().then(() => work(null));
    return new Promise((resolve, reject) => {
      const take = async () => {
        busy.add(name); lockLog.push("take " + name);
        try { resolve(await work({ name })); } catch (e) { reject(e); } finally { busy.delete(name); lockLog.push("drop " + name); }
      };
      if (outside.has(name)) {
        if (options.signal) options.signal.addEventListener("abort", () => { lockLog.push("gave up " + name); reject(new Error("AbortError")); });
        return;                                             // another page holds it and never lets go
      }
      const before = queues.get(name) || Promise.resolve();
      const mine = before.then(take);
      queues.set(name, mine.catch(() => {}));
    });
  },
};
Object.defineProperty(globalThis, "navigator", { value: %(locks)s ? { locks: LOCKS } : {}, configurable: true });
globalThis.tr = (text) => text;
globalThis.formkey = () => "f";
globalThis.alert = (text) => said.push(text);
const keepAlive = setInterval(() => {}, 50);          // node lets an AbortSignal.timeout's timer go when nothing else is waiting
function report(extra) {
  clearInterval(keepAlive);
  let index = null;
  try { index = JSON.parse(store.get("uqqus_chat_devices") || "null"); } catch (e) { index = "unreadable"; }
  console.log(JSON.stringify({ said, reloads: reloads.length, order, deleted, lockLog, dbs: [...dbs], index,
                               stored: Object.fromEntries(store), tab: Object.fromEntries(tabStore), ...(extra || {}) }));
}
const listeners = { pagehide: [] };
globalThis.window = {
  location: { reload: () => { reloads.push(true); report({ pagehide: listeners.pagehide.length }); } },
  addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
  removeEventListener: (name, fn) => { listeners[name] = (listeners[name] || []).filter((f) => f !== fn); },
  fire: (name) => { for (const fn of [...(listeners[name] || [])]) fn({}); },
};
globalThis.FormData = class { append() {} };
globalThis.XMLHttpRequest = class {
  open(method, url) { order.push(method + " " + url); }
  send() {
    order.push("send");
    Object.assign(this, %(answer)s);
    if (this.hold) return;                            // the answer never comes (the tab is left first)
    setTimeout(() => {
      if (this.status) { if (this.onload) this.onload(); } else if (this.onerror) { this.onerror(); }
      if (this.onloadend) this.onloadend();
    }, 0);
  }
};
%(script)s
%(call)s
"""


def node_run(call, stored=None, answer=None, dbs=(), failing=(), held=(), locks=True, timeout=60):
    script = ALL_JS[ALL_JS.index("function xhrData("):ALL_JS.index("function post(")] + CHAT_PART
    code = PRESSED % {"stored": json.dumps(stored or {}), "answer": json.dumps(answer or {}), "script": script, "call": call,
                      "dbs": json.dumps(list(dbs)), "failing": json.dumps(list(failing)), "held": json.dumps(list(held)),
                      "locks": "true" if locks else "false"}
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=timeout)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def press_log_out(stored, answer, **kw):
    return node_run("logOut();", stored, answer, **kw)


def entry(u, d, k="chat", **fields):
    local = u[1:].split(":")[0]
    return {"u": u, "d": d, "k": k, "p": f"uqqus-{k}-{local}-{d}", "kid": "kid1", "hs": "https://m", "s": "in", "x": 0, "ed": None,
            "made": 1, "used": 2, "gen": 1, "exp": None, "life": None, "why": None, "fails": 0, "bad": 0, "empty": 0, **fields}


SEALED = {"iv": "aXY", "ct": "Y3Q"}


def index(owner, entries, epoch=3):
    return json.dumps({"v": 2, "owner": owner, "epoch": epoch, "entries": entries})


LEFT_BY_A = {
    "uqqus_chat_devices": index(A, [entry(A, OLD, x=1, sealed=SEALED, exp=99), entry(A, "UQCAL0000000", "call", sealed=SEALED),
                                    entry(A, "UQRMA0000000", s="removed"), entry(B, "UQBBB0000000", s="out")]),
    "ruqqus_chat_session": session(A), "ruqqus_call_session": session(A), "uqqus_chat_prekey": "PREKEY",
    "uqqus_chat_waiting": json.dumps({"v": 2, "u": A, "iv": "x", "ct": "y"}),
    # the sync filter ids the chat library kept before build step 4, this account's and another's: every sign-in here goes, so all of them
    f"mxjssdk_memory_filter_FILTER_SYNC_{A}": "1", f"mxjssdk_memory_filter_FILTER_SYNC_{B}_x": "2",
}
A_DBS = stores(prefix(A, OLD)) + stores(prefix(A, "UQCAL0000000", "call")) + stores(prefix(A, "UQRMA0000000"))
B_DBS = stores(prefix(B, "UQBBB0000000"))
SITE = "http://site"


def assert_nothing_left_to_use(got):
    """Every sealed sign-in gone, every device in use `out`, nobody the owner, `epoch` moved: and every store kept. The chat sessions are
    left twice when the answer comes: at once, before the request is made, and again when it ends, under the lock (so `epoch` moved by 2)."""
    assert "uqqus_chat_prekey" not in got["stored"] and "uqqus_chat_waiting" not in got["stored"] and got["tab"] == {}
    assert "ruqqus_chat_session" not in got["stored"] and "ruqqus_call_session" not in got["stored"]
    assert not any(k.startswith("mxjssdk_memory_filter_") for k in got["stored"])
    idx = got["index"]
    assert idx["owner"] is None and idx["epoch"] == 5
    assert [(e["d"], e["s"]) for e in idx["entries"]] == [(OLD, "out"), ("UQCAL0000000", "out"), ("UQRMA0000000", "removed"), ("UQBBB0000000", "out")]
    assert not any("sealed" in e or e["exp"] is not None for e in idx["entries"])
    assert [e["gen"] for e in idx["entries"]] == [3, 3, 3, 3]                    # other tabs act on it
    assert got["deleted"] == [] and got["reloads"] == 1                           # nothing is deleted from IndexedDB
    assert got["pagehide"] == 0                                                   # answered: leaving the page runs nothing more


@node
def test_log_out_that_works_clears_all_of_it_and_says_nothing():
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""}, dbs=A_DBS + B_DBS)
    assert_nothing_left_to_use(got)
    assert got["said"] == [] and sorted(got["dbs"]) == sorted(A_DBS + B_DBS)


@node
def test_the_pre_key_goes_before_the_request_is_made_and_the_index_is_written_at_once_and_again_under_the_lock_before_the_reload():
    """At once, with no lock (a tab closed right after the press never gets one, and the sign-in ends on the server anyway), then again
    when the request ends, under the lock: a chat start in another tab during the request wrote its sign-in after the first run."""
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""})
    order = got["order"]
    writes = [i for i, step in enumerate(order) if step == "write uqqus_chat_devices"]
    assert order.index("remove uqqus_chat_prekey") < order.index("send")
    assert order.index("remove from this tab uqqus_chat_prekey") < order.index("send")
    assert len(writes) == 2 and writes[0] < order.index("send") < writes[1]
    assert got["lockLog"] == ["take uqqus-chat-session", "drop uqqus-chat-session"]
    assert got["tab"] == {}


SIGNED_IN_MEANWHILE = """
logOut();
// a chat start in another tab while /logout was answering: the sign-in still counted, so it wrote its sealed sign-in after the first run
const idx = JSON.parse(store.get("uqqus_chat_devices"));
idx.owner = "%(a)s";
idx.entries[0] = { ...idx.entries[0], s: "in", sealed: { iv: "aXY", ct: "Y3Q" }, gen: idx.entries[0].gen + 1 };
store.set("uqqus_chat_devices", JSON.stringify(idx));
const before = idx.epoch;
"""


@node
def test_a_tab_left_before_the_log_out_answers_leaves_chat_again_at_pagehide():
    got = node_run(SIGNED_IN_MEANWHILE % {"a": A} + 'window.fire("pagehide");\nreport({ before });', LEFT_BY_A, {"hold": True})
    idx = got["index"]
    assert got["before"] == 4 and idx["epoch"] == 5 and idx["owner"] is None and got["reloads"] == 0
    assert not any("sealed" in e for e in idx["entries"]) and idx["entries"][0]["s"] == "out"


@node
def test_a_log_out_that_answered_leaves_chat_under_the_lock_and_stops_listening_for_pagehide():
    got = node_run(SIGNED_IN_MEANWHILE % {"a": A}, LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""})
    idx = got["index"]
    assert idx["epoch"] == 5 and idx["owner"] is None and not any("sealed" in e for e in idx["entries"])
    assert got["pagehide"] == 0 and got["reloads"] == 1


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
    # the member asked to leave: the server may still hold the sign-in, so they are told, and what they left behind is locked either way
    got = press_log_out(LEFT_BY_A, {"status": 500, "responseURL": SITE + "/logout", "response": "<html>"})
    assert_nothing_left_to_use(got)
    assert got["said"] == ["Something went wrong. Please try again."]


@node
def test_a_log_out_that_never_arrived_still_clears_this_browser_and_says_so():
    got = press_log_out(LEFT_BY_A, {"status": 0, "responseURL": "", "response": ""})
    assert_nothing_left_to_use(got)
    assert len(got["said"]) == 1


@node
def test_a_lock_a_stuck_page_holds_doesnt_stop_a_log_out():
    """Only a stuck page could hold the session lock for 3 s: the Log out writes without it, and its `epoch` still drops what that page
    prepared before it."""
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""}, held=["uqqus-chat-session"])
    assert_nothing_left_to_use(got)
    assert got["lockLog"] == ["gave up uqqus-chat-session"]


@node
def test_without_web_locks_log_out_still_clears():
    got = press_log_out(LEFT_BY_A, {"status": 200, "responseURL": SITE + "/", "response": ""}, locks=False)
    assert_nothing_left_to_use(got)


@node
def test_a_browser_that_never_used_chat_gets_only_the_counter():
    """So a chat page starting in another tab right now drops its answer (its `epoch` was 0)."""
    got = press_log_out({"uqqus_chat_prekey": "P", "density": "compact"}, {"status": 200, "responseURL": SITE + "/", "response": ""})
    assert got["index"] == {"v": 2, "owner": None, "epoch": 2, "entries": []}             # left at once, then again under the lock
    assert got["stored"] == {"density": "compact", "uqqus_chat_devices": json.dumps(got["index"], separators=(",", ":"))}


@node
def test_a_log_out_replaces_an_index_that_cant_be_read_with_an_empty_one_whose_epoch_moved():
    for unreadable in ("not json", json.dumps({"v": 3, "owner": A, "epoch": 7, "entries": []})):
        got = press_log_out({"uqqus_chat_devices": unreadable}, {"status": 200, "responseURL": SITE + "/", "response": ""})
        # `unlisted`: the stores it named are still here and no entry names them, so the ad hold stays until a chat start lists them
        # the first run replaces it (epoch 1), the second moves that one on
        assert json.loads(got["stored"]["uqqus_chat_devices"]) == {"v": 2, "owner": None, "epoch": 2, "entries": [], "unlisted": True}
        assert got["reloads"] == 1


@node
def test_a_chat_answer_on_its_way_during_a_log_out_over_an_unreadable_index_is_dropped():
    """An unreadable index reads as empty at epoch 0 for a starting page (token.js); a Log out meanwhile must still move what that page
    compares, or its answer would write a sealed sign-in and an owner back after the Log out."""
    script = json.dumps(CHAT_PART)
    got = run("""
(0, eval)(%s);
store.set("uqqus_chat_devices", "not json");
serve = async () => { leaveChatSessions(); return { body: answer("@ruqqus_1:s", "UQNEW0000000") }; };
""" % script + START + "report({ got });")
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"] == {"v": 2, "owner": None, "epoch": 1, "entries": [], "unlisted": True}


@node
def test_a_chat_answer_on_its_way_during_a_log_out_is_dropped():
    """The real ensureSession (token.js) and the real leaveChatSessions (all_js.js): a page that asked before the Log out never writes its
    sign-in after it."""
    script = json.dumps(CHAT_PART)
    got = run("""
(0, eval)(%s);
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "in", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })], 2);
serve = async () => { leaveChatSessions(); return { body: answer("@ruqqus_1:s", "UQOLD0000000") }; };
""" % script + START + "report({ got });")
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"]["epoch"] == 3 and got["index"]["owner"] is None
    assert [(e["s"], "sealed" in e) for e in got["index"]["entries"]] == [("out", False)]


# A page that holds the session lock while it awaits an unseal (a reload with a sealed token: `settle`) can be stalled there for longer than
# a Log out waits (a suspended background tab), and a browser with no Web Locks has no lock at all: the Log out, or a deletion's mark, then
# runs while the page's write is prepared. The stand-in for WebCrypto's decrypt runs it exactly there.
DURING_UNSEAL = """
(0, eval)(%s);
const realDecrypt = crypto.subtle.decrypt.bind(crypto.subtle);
let meanwhile = null;
crypto.subtle.decrypt = async (...args) => { const run = meanwhile; meanwhile = null; if (run) run(); return realDecrypt(...args); };
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "in", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })], 2);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" % json.dumps(CHAT_PART)


@node
def test_a_log_out_that_ran_without_the_lock_while_a_start_awaited_its_unseal_is_never_undone():
    got = run(DURING_UNSEAL + """
meanwhile = () => leaveChatSessions();             // the 3 s ran out: it runs while the stalled page holds the lock
""" + START + "report({ got });")
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"]["epoch"] == 3 and got["index"]["owner"] is None
    assert [(e["s"], "sealed" in e) for e in got["index"]["entries"]] == [("out", False)]


@node
def test_a_deletion_that_ran_while_a_start_awaited_its_unseal_still_deletes_the_accounts_store():
    got = run(DURING_UNSEAL + """
let deletion = null;
meanwhile = () => { deletion = forgetChatDevices("@ruqqus_1:s"); };
""" + START + "await deletion;\nreport({ got });", locks=False, dbs=stores(prefix(A, OLD)))
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"]["owner"] is None and got["index"]["entries"] == []
    assert set(stores(prefix(A, OLD))) <= set(got["deleted"]) and got["dbs"] == []


# --- a deleted account's devices, on the page its deletion answers with

def forget_all(mxid, stored, **kw):
    return node_run(f"await forgetChatDevices({json.dumps(mxid)}); report();", stored, **kw)


DELETED = {
    **{k: v for k, v in LEFT_BY_A.items() if not k.startswith("mxjssdk_memory_filter_")}, "density": "compact",
    "mxjssdk_memory_filter_FILTER_SYNC_" + A: "1", "mxjssdk_memory_filter_FILTER_SYNC_" + B: "2",
}
LEGACY = CHAT_STORES + CALL_STORES
LOST = stores(prefix(A, "UQLOST000000"))                            # a store of A's that the index no longer names
NOT_A = stores("uqqus-chat-ruqqus_10-UQTEN0000000") + stores(prefix(B, "UQBBB0000000"))


@node
def test_a_deleted_accounts_devices_go_and_nobody_elses():
    got = forget_all(A, DELETED, dbs=A_DBS + LOST + NOT_A + CHAT_STORES + CALL_STORES)
    assert sorted(got["deleted"]) == sorted(A_DBS + LOST + CHAT_STORES + CALL_STORES)   # the account's, a lost one of its, the legacy ones
    assert sorted(got["dbs"]) == sorted(NOT_A)                                         # ruqqus_10 isn't ruqqus_1, and B's stay
    assert [(e["u"], e["s"]) for e in got["index"]["entries"]] == [(B, "out")]
    assert got["index"]["owner"] is None and got["index"]["epoch"] == 4
    left = dict(got["stored"])
    left.pop("uqqus_chat_devices")
    assert left == {"density": "compact", "mxjssdk_memory_filter_FILTER_SYNC_" + B: "2"}


@node
def test_other_tabs_are_told_before_any_store_goes():
    got = forget_all(A, DELETED, dbs=A_DBS)
    order = got["order"]
    first_delete = min(i for i, step in enumerate(order) if step.startswith("delete "))
    assert order.index("write uqqus_chat_devices") < first_delete             # the `forget` mark is written first: keeper.js stops them
    # each store is deleted under its own lock
    assert got["lockLog"][:2] == ["take uqqus-chat-session", "drop uqqus-chat-session"]
    assert "take uqqus-store:" + prefix(A, OLD) in got["lockLog"]


@node
def test_another_accounts_waiting_messages_stay_theirs():
    got = forget_all(A, {**DELETED, "uqqus_chat_waiting": json.dumps({"v": 2, "u": B, "iv": "x", "ct": "y"})}, dbs=A_DBS)
    assert "uqqus_chat_waiting" in got["stored"]


@node
def test_a_store_still_in_use_stays_forget_for_the_next_chat_start():
    """A page of the account that didn't let go in 5 s: its store and its entry stay, marked `forget`, never lost (token.js deletes it at
    the next chat start on this browser, when its lock is free)."""
    busy = prefix(A, OLD)
    got = forget_all(A, DELETED, dbs=A_DBS, held=["uqqus-store:" + busy])
    assert not any(name.startswith(busy) for name in got["deleted"]) and set(stores(busy)) <= set(got["dbs"])
    left = [e for e in got["index"]["entries"] if e["u"] == A]
    assert [(e["p"], e["s"], "sealed" in e) for e in left] == [(busy, "forget", False)]
    assert "gave up uqqus-store:" + busy in got["lockLog"]


@node
def test_a_delete_that_fails_keeps_its_entry():
    got = forget_all(A, DELETED, dbs=A_DBS, failing=[prefix(A, "UQRMA0000000") + "::matrix-sdk-crypto-meta"])
    assert [(e["d"], e["s"]) for e in got["index"]["entries"] if e["u"] == A] == [("UQRMA0000000", "forget")]


@node
def test_a_deleted_accounts_stores_go_even_when_the_index_cant_be_read():
    """An unreadable index (bad JSON, a newer client's before a roll-back) names nothing: a database carrying the account's own name is
    the account's whatever it said, so it goes; the index is replaced with an empty one marked `unlisted`, since another account's stores
    it may have named are still here (the ad hold stays, token.js gives them their entries back at the next chat start)."""
    for unreadable in ("not json", json.dumps({"v": 3, "owner": A, "epoch": 7, "entries": []})):
        got = forget_all(A, {**DELETED, "uqqus_chat_devices": unreadable}, dbs=A_DBS + LOST + NOT_A)
        assert sorted(n for n in got["deleted"] if n not in LEGACY) == sorted(A_DBS + LOST)
        assert sorted(got["dbs"]) == sorted(NOT_A)
        assert got["index"] == {"v": 2, "owner": None, "epoch": 1, "entries": [], "unlisted": True}
    # the sweep on its own, over an index nobody has replaced yet: it never stops for an index it can't read
    got = node_run(f"await sweepChatStores({json.dumps(A)}); report();", {"uqqus_chat_devices": "not json"}, dbs=A_DBS + NOT_A)
    assert sorted(got["deleted"]) == sorted(A_DBS) and sorted(got["dbs"]) == sorted(NOT_A)


@node
def test_a_deleted_accounts_unnamed_store_still_in_use_is_written_down_as_forget():
    """The sweep finds a store the index doesn't name (or can't, being unreadable) that a page of the account still has open: it is never
    lost track of, but marked `forget` for the next chat start, which deletes it when it is free."""
    busy = prefix(A, "UQLOST000000")
    for start in (DELETED, {**DELETED, "uqqus_chat_devices": "not json"}):
        got = forget_all(A, start, dbs=A_DBS + LOST + NOT_A, held=["uqqus-store:" + busy])
        assert set(LOST) <= set(got["dbs"])
        left = [(e["u"], e["d"], e["k"], e["p"], e["s"]) for e in got["index"]["entries"] if e["u"] == A]
        assert left == [(A, "UQLOST000000", "chat", busy, "forget")]


@node
def test_a_parents_deletion_also_forgets_the_children_closed_with_it():
    got = node_run(f"await forgetChatDevices({json.dumps(A)}, [{json.dumps(B)}]); report();", DELETED, dbs=A_DBS + B_DBS + NOT_A[:2])
    assert sorted(n for n in got["deleted"] if n not in LEGACY) == sorted(A_DBS + B_DBS)    # the child's stores go too
    assert got["index"]["entries"] == [] and got["index"]["owner"] is None and got["index"]["epoch"] == 4
    assert "mxjssdk_memory_filter_FILTER_SYNC_" + B not in got["stored"]


@node
def test_a_child_its_parent_deletes_is_forgotten_and_the_parents_sign_in_here_is_left_alone():
    """A family shares a browser: deleting a child from Settings > Children takes the child's devices, stores and leftovers, and nothing of
    the parent's, who stays signed in (their pre-key, sealed token and `epoch` stay, so their own open chat carries on)."""
    stored = {"uqqus_chat_devices": index(A, [entry(A, OLD, sealed=SEALED), entry(B, "UQBBB0000000", s="out")]),
              "uqqus_chat_waiting": json.dumps({"v": 2, "u": A, "iv": "x", "ct": "y"}),
              "mxjssdk_memory_filter_FILTER_SYNC_" + A: "1", "mxjssdk_memory_filter_FILTER_SYNC_" + B: "2"}
    lost = stores(prefix(B, "UQBLOST00000"))                       # a store of the child's the index no longer names
    got = node_run(f"await forgetChildChatDevices([{json.dumps(B)}]); report();", stored, dbs=stores(prefix(A, OLD)) + B_DBS + lost)
    assert sorted(n for n in got["deleted"] if n not in LEGACY) == sorted(B_DBS + lost)
    assert sorted(got["dbs"]) == sorted(stores(prefix(A, OLD)))
    idx = got["index"]
    assert idx["owner"] == A and idx["epoch"] == 3
    assert [(e["u"], e["s"], "sealed" in e) for e in idx["entries"]] == [(A, "in", True)]
    assert got["tab"] == {"uqqus_chat_prekey": "TAB PREKEY"} and "uqqus_chat_waiting" in got["stored"]
    assert "mxjssdk_memory_filter_FILTER_SYNC_" + A in got["stored"] and "mxjssdk_memory_filter_FILTER_SYNC_" + B not in got["stored"]


@node
def test_a_childs_store_still_in_use_stays_forget_and_nothing_is_written_for_no_child():
    busy = prefix(B, "UQBBB0000000")
    got = node_run(f"await forgetChildChatDevices([{json.dumps(B)}]); report();", LEFT_BY_A, dbs=A_DBS + B_DBS, held=["uqqus-store:" + busy])
    assert [(e["u"], e["s"]) for e in got["index"]["entries"] if e["u"] == B] == [(B, "forget")]
    untouched = node_run("await forgetChildChatDevices([]); report();", LEFT_BY_A, dbs=A_DBS + B_DBS)
    assert json.loads(untouched["stored"]["uqqus_chat_devices"]) == json.loads(LEFT_BY_A["uqqus_chat_devices"])


@node
def test_without_web_locks_the_deletion_page_still_deletes():
    got = forget_all(A, DELETED, dbs=A_DBS, locks=False)
    assert sorted(name for name in got["deleted"] if name not in LEGACY) == sorted(A_DBS)
    assert [e["u"] for e in got["index"]["entries"]] == [B]


@node
def test_an_account_that_never_had_chat_deletes_nothing_of_anyones():
    got = forget_all(None, DELETED, dbs=A_DBS + NOT_A)
    assert [name for name in got["deleted"] if name not in LEGACY] == []
    assert json.loads(got["stored"]["uqqus_chat_devices"]) == json.loads(DELETED["uqqus_chat_devices"])


def test_only_a_deletion_that_went_through_answers_with_the_page_that_clears():
    settings = (ROOT / "ruqqus" / "routes" / "settings.py").read_text(encoding="utf-8")
    body = settings[settings.index("def close_own_account("):settings.index('@app.route("/settings/blocks"')]
    assert body.index("v.verifyPass(") < body.index('return render_template("settings_security.html"') < body.index("g.db.commit()") < body.index('"account_deleted.html"')
    assert "forgetChatDevices" in (TEMPLATES / "account_deleted.html").read_text(encoding="utf-8")
    assert "forgetChatDevices" not in (TEMPLATES / "settings_security.html").read_text(encoding="utf-8")


def test_all_three_bundles_open_the_store_through_the_one_function():
    for bundle in ("index.js", "call.js", "space.js"):
        source = (ASSETS / "chat_src" / "src" / bundle).read_text(encoding="utf-8")
        assert "openCryptoStore(client" in source and "client.initRustCrypto(" not in source, bundle
        # its opening is tracked by the keeper, after the store's own lock (6.4), and the client is the keeper's (makeClient)
        # (keeper.js openStore: the store lock, the wasm, then the opening, never once the page has stopped)
        assert "keeper.openStore(() => openCryptoStore(client, session), {" in source, bundle
        assert "initCryptoWasm(" in source[source.index("keeper.openStore("):], bundle
        assert "keeper.holdStore()" not in source and "keeper.opening(" not in source, bundle
        assert "makeClient(sdk, session" in source and "sdk.createClient(" not in source, bundle
        assert "watchSignedOut(client, keeper);" in source, bundle
    # a store that can't be opened (StoreFailed from openStore) shuts the keeper down: the call window in main()'s catch, which every
    # throw of startMatrix reaches; the Space page around its own openStore
    call = (ASSETS / "chat_src" / "src" / "call.js").read_text(encoding="utf-8")
    caught = call[call.index("    await run();\n  } catch (e) {"):]
    assert caught.index("if (state.keeper) state.keeper.shutdown();") < caught.index("main();")
    space = (ASSETS / "chat_src" / "src" / "space.js").read_text(encoding="utf-8")
    around = space[space.index("keeper.openStore("):]
    assert around.index("} catch (e) {") < around.index("keeper.shutdown();") < around.index("throw e;")


def test_a_tab_that_lost_the_chat_lock_makes_no_client():
    # the newest tab took over while this one was still getting its session: there was no client yet for the lost lock to stop
    source = (ASSETS / "chat_src" / "src" / "index.js").read_text(encoding="utf-8")
    lock = source[source.index("function holdChatLock()"):source.index("async function main()")]
    assert "chatLockLost = true" in lock
    # a page that lost the lock stops in the safe order: the opening settles, the client stops, the store lock goes (keeper.js
    # holdChatLock calls the keeper's shutdown; driven under node in tests/test_chat_keeper.py)
    assert "keeper: () => state.keeper," in lock
    main = source[source.index("async function main()"):]
    # a page that lost the lock while the site answered drops that answer (token.js TakenOver: never demotes the other tab's device),
    # and a session it never opens has its raw store key zeroed (1.2)
    assert main.index('ensureSession(boot, "chat", { stillMine: () => !chatLockLost })') \
        < main.index("if (!session || chatLockLost) return forgetStoreKey(session);") < main.index("makeClient(sdk, session")
    # a frame that loses chat tells its parent (share_chat.js drops it, side_panels.js marks it stale), and says what wasn't sent
    assert 'if (role === "panel" || role === "share") {' in lock and '{ why: "taken_over" }' in lock and "unsentLine(unsent)" in lock
    assert "keeper.unsent = () => sendGate.pending();" in main
    assert main.index("makeClient(sdk, session") < main.index("if (chatLockLost || keeper.stopped) return keeper.shutdown()") \
        < main.index("keeper.openStore(") < main.index("openCryptoStore(client")
    after_store = main[main.index("openCryptoStore(client"):]
    assert after_store.index("if (!opened || chatLockLost || keeper.stopped) return") < after_store.index("client.startClient(")
    # a failure to start is said through the keeper's role-aware ui, so a side-panel or share frame tells its parent (6.6)
    failure = after_store[:after_store.index("if (!opened")]
    assert "keeper.ui.stop(" in failure and "chatUi.stop(" not in failure
    # ...except by a page that lost chat to another tab meanwhile: its "Chat is open in another tab." and "Use it here" stay
    assert failure.index("keeper.shutdown();") < failure.index("if (chatLockLost) return undefined;") < failure.index("keeper.ui.stop(")
    assert "notify: keeper.ui.notice" in main and "notify: chatUi.notice" not in main


def test_the_guild_chat_tabs_frame_taken_over_says_so_where_it_can_be_seen_and_tells_its_page():
    """The guild Chat tab's frame (`single`) hides its conversation list (`.chat-single #chat-list-pane`, both stylesheets): its note
    goes where the thread was (noteArea), never into the hidden list with the thread emptied (a blank frame), and its page is told
    (`uqqus-chat:stopped`, taken_over), which makes the frame again when the member comes back to that tab."""
    source = (ASSETS / "chat_src" / "src" / "index.js").read_text(encoding="utf-8")
    lock = source[source.index("function holdChatLock()"):source.index("async function main()")]
    assert "STOPPED as FRAME_STOPPED" in source
    single = lock[lock.index('if (role === "single") {'):]
    single = single[:single.index("return;")]
    assert "const area = noteArea();" in single and "area.replaceChildren(note);" in single
    assert 'tellParent({ type: FRAME_STOPPED, why: "taken_over", text: "" });' in single
    assert "pane.replaceChildren()" not in single and "list.replaceChildren" not in single
    # the single branch comes after the note is built and before the top page's own list and thread
    assert lock.index("note.append(again);") < lock.index('if (role === "single") {') < lock.index("if (list) list.replaceChildren(note);")
    for sheet in ("main.scss", "main_dark.scss"):
        assert "#chat #chat-app.chat-single #chat-list-pane" in (ASSETS / "style" / sheet).read_text(encoding="utf-8")
    guild = (ROOT / "ruqqus" / "templates" / "guild" / "chat.html").read_text(encoding="utf-8")
    assert "event.data.type === 'uqqus-chat:stopped'" in guild and "frame.src = frame.src;" in guild


def test_the_spec_and_its_readme_say_another_accounts_waiting_messages_go_when_the_next_account_opens_chat():
    """token.js readWaiting removes a box sealed for another account (tests/test_chat_index.py holds it): what the spec's title and the
    README say must be that, not that they are kept."""
    readme = (ROOT / "scripts" / "ui" / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "scripts" / "ui" / "chat_session.spec.ts").read_text(encoding="utf-8")
    assert "device, store and waiting messages are never used" not in readme
    assert "its waiting messages, sealed for that account alone, are never used and go when another account opens chat here" in readme
    assert "waiting messages are never used, and are kept" not in spec
    assert "are never used and are kept for it, and its waiting messages go" in spec
    token = (ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js").read_text(encoding="utf-8")
    read = token[token.index("export async function readWaiting("):token.index("let waitingWrites")]
    assert "box.u === session.user_id" in read and "removeItem(WAITING_KEY);" in read


def test_the_chat_client_never_speaks_of_a_later_steps_work_as_built():
    """The harvest (build step 6, harvest.js) and stale-device pruning (build step 7, chat_signouts.prune_stale) are built: nothing says
    either isn't, and a row left on the chat server is said to be left to the stale-device step, by its name."""
    src = ROOT / "ruqqus" / "assets" / "chat_src" / "src"
    for path in sorted(src.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for gone in ("build step 6, not built yet", "build step 7", "stale-device pruning"):
            assert gone not in re.sub(r"\s*\n\s*(//)?\s*", " ", text), (path.name, gone)
    token = (src / "token.js").read_text(encoding="utf-8")
    assert token.count("until the stale-device step signs it") == 3 and "chat_signouts.prune_stale" in token
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "stale-device pruning is build step 7, not built yet" not in claude
    assert "| **A stale site device**" in claude and "`chat_signouts.prune_stale`, each in its own `try`" in claude


def test_what_is_left_to_the_stale_step_is_said_with_its_one_exception():
    """prune_stale never signs out an account's most recently used non-revoked CHAT row, and gives a call row no such keeping: every
    place that says a device is left to the stale-device step says so as a condition, never as a promise about that device."""
    token = (ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js").read_text(encoding="utf-8")
    flat = re.sub(r"\s*\n\s*(//)?\s*", " ", token)
    for fn in ("async function replaceDevice(", "async function dropMinted(", "throw new StoreReplaced(why);"):
        at = flat.index(fn)
        near = flat[max(0, at - 700):at + 700]
        assert "unless it is still the account's most recently used chat device" in near, fn
    assert flat.count("unless it is still the account's most recently used chat device") == 3
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "and it is never the account's most recently used chat device" not in claude
    assert "unless it is the account's most recently used chat device (never signed out this way)" in claude
    assert "as it does, under the same condition, a device whose `mode=replace` never reached the site" in claude
    ban_row = next(line for line in claude.splitlines() if line.startswith("| **A suspension, a permanent ban, an unban**"))
    assert "the stale-device step still reaches the account's site devices" in ban_row and "a call device has no such keeping" in ban_row
    privacy = (ROOT / "ruqqus" / "templates" / "help" / "privacy.html").read_text(encoding="utf-8")
    assert "never the one your account used most recently" not in privacy
    assert "except the chat device your account used most recently for chat (a device used for calls is never kept" in privacy


def test_claude_md_describes_the_browser_as_it_is_at_build_step_5():
    """Every site page's client sends `mode` since build step 4 (token.js), so the no-mode take-up of a device from before the rows serves
    no site browser; renewal through the site is built (keeper.js), and since build step 5 every token ends after an hour and is renewed
    before it does: no sentence may still say they don't expire."""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "today's `token.js`, which posts only" not in text and "renewal through the site comes in the next step" not in text
    assert "**none of the site's own pages takes that path now**" in text
    # the docs sweep: the docs describe what is built, never by the plan's phases, steps or section numbers a reader can't look up
    for doc in ("CLAUDE.md", "RESTORE.md", "ruqqus/templates/help/privacy.html"):
        body = (ROOT / doc).read_text(encoding="utf-8").lower()
        for pointer in ("build step", "chat plan", "phase 2", "phase 0", "plan's phase"):
            assert pointer not in body, (doc, pointer)
        assert not re.search(r"\bphase \d, (step )?\d", body), doc
    for stale in ("tokens don't expire yet", "come in build step 5", "until expiring tokens", "CHAT_EXPIRING_TOKENS"):
        assert stale not in text, stale
    assert "**Every chat token ends after an hour**" in text and "**a copy of an access token works until it ends, within the hour**" in text
    assert "**an app that asks for refresh tokens now refreshes hourly**" in text and "**Element Web 1.12.3 asks for none**" in text
    # What was checked about Element is what Synapse holds for its token, never an hour-long run nobody made.
    assert "Synapse's row for its token has no `valid_until_ms` and no refresh token" in text
    assert "**No run was kept going for the real hour**" in text
    restore = (ROOT / "RESTORE.md").read_text(encoding="utf-8")
    assert "keeps a token that never expires" not in restore and "its token has no expiry (checked on Synapse" in restore
    token =(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js").read_text(encoding="utf-8")
    assert 'mode: "boot"' in token and 'mode: "replace"' in token
    assert "Today's chat page deletes its store" not in (ROOT / "ruqqus" / "helpers" / "chat_tokens.py").read_text(encoding="utf-8")


def test_a_refused_password_change_and_its_new_pre_key_are_disclosed_as_they_are():
    """chat_key.js makes the new password's pre-key before the site has checked the change: it is kept whenever the typed current
    password opens the chat key's copy, which a refused change can still do (a new password the site refuses, or a current one that
    is wrong to the site but is the password the copy is still locked with, as after a reset). CLAUDE.md and the privacy page say so."""
    rekey = (ROOT / "ruqqus" / "assets" / "js" / "chat_key.js").read_text(encoding="utf-8")
    rekey = rekey[rekey.index("form[data-chat-rekey]"):]
    # the new pre-key is kept only after the typed current password unlocked the copy (so any other wrong one keeps none)
    assert rekey.index("unlock(oldKey, info.password_blob)") < rekey.index("remember(pre[1]")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "or the current password wrong, which the site checks only after the form has done all this, when what was typed is the " \
           "password the chat key's copy is still locked with, as after a reset" in claude
    assert "shorter than 8 or longer than 100 characters" in claude
    privacy = (ROOT / "ruqqus" / "templates" / "help" / "privacy.html").read_text(encoding="utf-8")
    assert "a key made from the refused new password can stay in that tab until you close it or log out in it" in privacy
    assert "the current password you typed was wrong but is the one your chat key is still locked with" in privacy


def test_claude_md_says_what_queue_chat_signouts_needs_and_what_it_costs():
    """The script refuses a live database without --confirm and empties chat_site_devices, which stops the harvest: CLAUDE.md's
    sentence about it says both, as RESTORE.md's runbooks do."""
    script = (ROOT / "scripts" / "queue_chat_signouts.py").read_text(encoding="utf-8")
    assert "if not scratch(name) and not confirm:" in script and 'DELETE FROM chat_site_devices' in script
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    sentence = claude[claude.index("`scripts/queue_chat_signouts.py --all"):]
    sentence = sentence[:sentence.index("Tests: ")]
    assert "[--confirm]" in sentence and "**every live run needs `--confirm`**" in sentence
    assert "**empties `chat_site_devices`**" in sentence and "**the harvest can't move what the browsers' kept stores held**" in sentence
