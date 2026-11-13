"""A chat page that stops while its unlock box is open (another tab took chat over, its keeper stopped) changes nothing more on the server
(crypto.js `stopped`, keylock.js closeDialogs, keeper.js `onStopped`, index.js holdChatLock). Its client is stopped but its access token may
still work, and "I don't have it: start over" would delete the account's key backup and secret storage while the site's locked copies still
name the old key. Runs the real crypto.js and keylock.js under node, with a stand-in DOM, site and SDK crypto backend."""
from chat_node_harness import IN_CI, ROOT, SRC, node

import json
import subprocess

import pytest

CHAT_SRC = ROOT / "ruqqus" / "assets" / "chat_src"
SDK = CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "crypto-api" / "recovery-key.js"
installed = pytest.mark.skipif(not SDK.exists() and not IN_CI,
                               reason="the chat bundle's node_modules are not installed (npm ci in chat_src)")

PRELUDE = r"""
const SRC = __SRC__;
const posted = [], done = [], body = [];
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.style = {}; this.attrs = {}; this.textContent = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  append(...kids) { for (const k of kids) { k.parent = this; this.children.push(k); } }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  focus() {}
  click() { for (const fn of this.listeners.click || []) fn({}); }
  find(text) {
    if (this.tag === "button" && this.textContent === text) return this;
    for (const c of this.children) { const f = c.find(text); if (f) return f; }
    return null;
  }
  all(tag) { return (this.tag === tag ? [this] : []).concat(...this.children.map((c) => c.all(tag))); }
}
const docBody = new El("body");
globalThis.document = { createElement: (tag) => new El(tag), body: docBody };
globalThis.window = { tr: (t) => t, location: { origin: "https://site" } };
const tab = new Map();
globalThis.sessionStorage = { getItem: (k) => (tab.has(k) ? tab.get(k) : null), setItem: (k, v) => { tab.set(k, String(v)); },
                              removeItem: (k) => { tab.delete(k); } };
globalThis.localStorage = { getItem: () => null, removeItem() {} };
let keyInfo = { salt: "salt", password_blob: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", recovery_blob: null, legacy: false };
let hasKeys = true;
const saved = [];                                  // what each POST /api/chat/key saved (the locked copies)
globalThis.fetch = async (url, init = {}) => {
  if (init.method === "POST") posted.push(String(url));
  if (init.method === "POST" && String(url) === "/api/chat/key") saved.push(Object.fromEntries([...init.body].filter(([k]) => k !== "formkey")));
  if (init.method === "POST" && String(url) === "/api/chat/key_reset/grant") {
    const sent = Object.fromEntries([...init.body]);
    grantAsks.push(Object.keys(sent).filter((k) => k !== "formkey").sort());
    const answer = grantAnswer(sent);
    return { ok: answer.status === 200, status: answer.status, type: "basic", json: async () => answer.body };
  }
  const json = String(url) === "/api/chat/key" && init.method !== "POST" ? keyInfo : { ok: true };
  return { ok: true, status: 200, type: "basic", json: async () => json };
};
// Start over's grant (routes/chat_key_reset_pages.py): the right password buys "G1", anything else is refused beside its field
const grantAsks = [];
let grantAnswer = (sent) => (sent.password === "bob-password" && (!keyInfo.mfa || sent.otp === "123456")
  ? { status: 200, body: { grant: "G1" } }
  : { status: 403, body: { error: sent.password === "bob-password" ? "That code isn't right." : "That isn't your password.",
                           field: sent.password === "bob-password" ? "otp" : "password" } });
const armed = [];                                  // what the page's keeper was armed with (keeper.js armKeyReset), null for a disarm
const backend = {
  userHasCrossSigningKeys: async () => hasKeys,
  getCrossSigningStatus: async () => ({ privateKeysCachedLocally: {} }),
  resetEncryption: async () => { done.push("resetEncryption"); },
  bootstrapCrossSigning: async () => { done.push("bootstrapCrossSigning"); },
  bootstrapSecretStorage: async (o = {}) => { done.push("bootstrapSecretStorage"); if (o.createSecretStorageKey) await o.createSecretStorageKey(); },
  createRecoveryKeyFromPassphrase: async () => ({ encodedPrivateKey: "KEY" }),
  loadSessionBackupPrivateKeyFromSecretStorage: async () => {},
  checkKeyBackupAndEnable: async () => {},
  restoreKeyBackup: async () => {},
  importSecretsBundle: async (b) => { done.push("importSecretsBundle"); },
  crossSignDevice: async (d) => { done.push("crossSignDevice " + d); },
  getCrossSigningKeyId: async () => "MASTER",
  getKeyBackupInfo: async () => ({ version: "7" }),
  isKeyBackupTrusted: async (info) => ({ trusted: true, matchesDecryptionKey: info.version === localBackup }),
};
let localBackup = "7";                              // the backup this device's key opens (after an import, the bundle's)
let serverBackup = { version: "7" };                 // what /room_keys/version answers (null: none; "throw": can't be asked)
let serverMaster = "MASTER";
const rescueHooks = {
  find: async () => { rescueAsked += 1; return rescueFound; },
  master: async () => serverMaster,
  backup: async () => { if (serverBackup === "throw") throw new Error("offline"); return serverBackup; },
};
// rescue.js findRescue stand-in: what this browser's kept stores give (null: nothing to rescue)
let rescueFound = null;
let rescueAsked = 0;
const secretStorageAsked = [];
const plainBootstrap = backend.bootstrapSecretStorage;
backend.bootstrapSecretStorage = async (o = {}) => { secretStorageAsked.push({ newStorage: !!o.setupNewSecretStorage, newBackup: !!o.setupNewKeyBackup }); return plainBootstrap(o); };
const client = { getCrypto: () => backend, getDeviceId: () => "UQNEW0000000", getUserId: () => "@ruqqus_1:s", secretStorage: { getDefaultKeyId: async () => null } };
const K = await import(SRC + "/keylock.js");
const holds = [];                                  // what the page's keeper was asked to wait for (keeper.js holdFor)
let holdHook = (work) => { holds.push(work); return work; };
__SETUP__
const C = await import(SRC + "/crypto.js");
const tick = () => new Promise((r) => setTimeout(r, 0));
async function boxOpen() { for (let i = 0; i < 50 && !docBody.children.length; i += 1) await tick(); }
const titles = () => docBody.all("h2").map((h) => h.textContent);
const seen = [];
async function boxTitled(title) {
  for (let i = 0; i < 1500; i += 1) {
    const now = titles();
    for (const t of now) if (!seen.includes(t)) seen.push(t);
    if (now.includes(title)) return true;
    await new Promise((r) => setTimeout(r, 10));
  }
  return false;
}
function typePassword(value) { docBody.all("input").find((i) => i.type === "password").value = value; }
async function opens(blob, prekey) {
  try { return await K.unlock(await K.passwordKey(prekey, "salt"), blob); } catch (e) { return null; }
}
let stopped = false;
const notices = [];
const ready = C.ensureEncryptionReady(client, { formkey: "f", mxid: "@ruqqus_1:s", username: "Bob", prekeyRef: __REF__ },
  { why: "password", notify: (t) => notices.push(t), stopped: () => stopped,
    hold: (work) => holdHook(work),
    keyReset: { arm: (g) => armed.push(g), disarm: () => armed.push(null) },
    rescue: rescueHooks });
await boxOpen();
const asked = docBody.children.length;
"""


def run(scenario, setup=""):
    code = (PRELUDE.replace("__SRC__", json.dumps(SRC.as_uri())).replace("__REF__", json.dumps(REF)).replace("__SETUP__", setup)
            + scenario)
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60, cwd=CHAT_SRC)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# the id of this sign-in, which the page's boot names (chat.html prekeyRef, helpers/session_cookie.py PREKEY_REF_KEY)
REF = "0123456789abcdef0123456789abcdef"
EARLIER = "fedcba9876543210fedcba9876543210"

REPORT = 'console.log(JSON.stringify({ asked, answer: await ready, done, posted, left: docBody.children.length, notices }));\n'


@node
@installed
def test_a_page_that_loses_chat_closes_its_unlock_box_and_changes_nothing():
    # another tab took chat over (index.js holdChatLock lost(), keeper.js shutdown -> onStopped): the box goes, and the unlock answers false
    got = run("stopped = true;\nC.stopUnlocking();\n" + REPORT)
    assert got["asked"] == 1 and got["left"] == 0
    assert got["answer"] is False and got["done"] == [] and got["posted"] == [] and got["notices"] == []


@node
@installed
def test_start_over_pressed_on_a_page_that_stopped_resets_nothing():
    # even a box left on screen (it wasn't closed) can't act: "start over", then "Start over" on the question, reset and save nothing
    got = run("""
stopped = true;
docBody.find("I don't have it: start over").click();
await tick();
const confirm = docBody.find("Start over");
if (confirm) confirm.click();
await tick();
""" + REPORT)
    assert got["answer"] is False and got["done"] == [] and got["posted"] == [] and got["left"] == 0


@node
@installed
def test_start_over_on_a_live_page_still_resets():
    # the guard is only for a stopped page: a live one starts over as before
    got = run("""
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Protect your messages");
ready.catch(() => {});
""" + 'console.log(JSON.stringify({ done, asked, armed, grantAsks }));\n')
    # the password bought the grant BEFORE anything was reset, the reset's one upload went with it, and nothing uploaded new keys twice
    assert got["grantAsks"] == [["password"]] and got["armed"][:2] == ["G1", None]
    assert got["done"][:2] == ["resetEncryption", "bootstrapSecretStorage"] and "bootstrapCrossSigning" not in got["done"]


def test_the_page_wires_the_stop_into_the_unlock():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    keeper = (SRC / "keeper.js").read_text(encoding="utf-8")
    assert "stopUnlocking();" in index[index.index("lost: () => {"):index.index("const unsent = sendGate.pending();")]
    assert "keeper.onStopped = stopUnlocking;" in index
    assert "{ notify: keeper.ui.notice, ...asked, stopped: chatGone, hold: (work) => keeper.holdFor(work),\n        wait: keysWaitLine, inSight,\n" in index
    assert "keyReset: { arm: (grant) => keeper.armKeyReset(grant), disarm: () => keeper.disarmKeyReset() }," in index
    # the rescue is offered on the chat page and its panel only (rescue.js)
    assert 'rescue: ["top", "panel"].includes(frameRole()) ? {' in index
    # after the sweep, which opens the same stores under the same locks (a store it held would be skipped)
    assert "find: () => swept.then(() => findRescue(session, { serverMaster: () => masterOnServer(client), stopped: chatGone }))," in index
    assert "backup: () => backupOnServer(client)," in index
    # a page that ends while new keys' copies are pending waits for them before it reloads (keeper.js end, crypto.js keysPending)
    assert "keeper.keysPending = keysPending;" in index
    shutdown = keeper[keeper.index("  shutdown() {"):keeper.index("  async end(")]
    assert "this.onStopped()" in shutdown


# --- the sign-in's pre-key is used only for the account it was typed for (keylock.js takePrekey, chat_key.js remember)

NEW_ACCOUNT = 'keyInfo = { salt: "salt", password_blob: null, recovery_blob: null, legacy: false };\nhasKeys = false;\n'


def held(name, prekey="ALICE-PREKEY", ref=REF):
    return 'tab.set("uqqus_chat_prekey", JSON.stringify({ v: 1, name: %s, k: %s, ref: %s }));\n' % (json.dumps(name), prekey, json.dumps(ref))


@node
@installed
def test_another_accounts_pre_key_left_in_the_tab_never_locks_a_new_accounts_key():
    """Alice signed in in this tab, never opened chat, logged out in another tab where Bob then signed in: this tab's page is Bob's now.
    Bob's first set-up must not lock his recovery key with Alice's password: the pre-key goes, and his own password is asked for."""
    got = run("""
const protect = await boxTitled("Protect your messages");
const leftAfter = tab.has("uqqus_chat_prekey");
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ protect, leftAfter, answer,
  alice: await opens(blob, alicePrekey), bob: await opens(blob, await K.prekeyFromPassword("bob-password")) }));
""", setup=NEW_ACCOUNT + 'const alicePrekey = await K.prekeyFromPassword("alice-password");\n' + held("alice", "alicePrekey"))
    assert got["protect"] is True and got["leftAfter"] is False and got["answer"] is True
    assert got["alice"] is None and got["bob"] == "KEY"


@node
@installed
def test_this_accounts_own_pre_key_locks_its_new_key_without_asking():
    got = run("""
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ seen, answer, bob: await opens(blob, bobPrekey) }));
""", setup=NEW_ACCOUNT + 'const bobPrekey = await K.prekeyFromPassword("bob-password");\n' + held("bob", "bobPrekey"))
    assert got["seen"] == ["Save your recovery code"] and got["answer"] is True and got["bob"] == "KEY"


@node
@installed
@pytest.mark.parametrize("ref", [EARLIER, "", None])
def test_a_pre_key_an_earlier_sign_in_of_this_account_left_never_locks_its_new_key(ref):
    """Bob signed in with P1 in this tab and never opened chat; his password was then changed to P2 in another browser (or by a reset, or
    by his parent), which ended this browser's sign-in, and nothing here noted it (no uqqus_chat_prekey_before). He signed in again with P2
    in another tab, a new sign-in with a new id. Back in this tab, his first set-up must not lock his recovery key with P1: the pre-key
    names the earlier sign-in (or none), so it goes, and the password is asked for."""
    # None: kept before pre-keys named their sign-in, with no id at all
    stored = (held("bob", "p1", ref=ref) if ref is not None
              else 'tab.set("uqqus_chat_prekey", JSON.stringify({ v: 1, name: "bob", k: p1 }));\n')
    got = run("""
const protect = await boxTitled("Protect your messages");
const leftAfter = tab.has("uqqus_chat_prekey");
typePassword("p2-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ protect, leftAfter, answer,
  p1: await opens(blob, p1), p2: await opens(blob, await K.prekeyFromPassword("p2-password")) }));
""", setup=NEW_ACCOUNT + 'const p1 = await K.prekeyFromPassword("p1-password");\n' + stored)
    assert got["protect"] is True and got["leftAfter"] is False and got["answer"] is True
    assert got["p1"] is None and got["p2"] == "KEY"


@node
@installed
def test_a_pre_key_an_earlier_sign_in_left_never_opens_a_migration_either():
    """The account has only the older server copy (legacy): the migration locks its password copy with a pre-key too, and that must also be
    this sign-in's own."""
    got = run("""
const protect = await boxTitled("Protect your messages");
console.log(JSON.stringify({ protect, saved, left: tab.has("uqqus_chat_prekey") }));
process.exit(0);
""", setup=('keyInfo = { salt: "salt", password_blob: null, recovery_blob: null, legacy: true };\n'
            'const realFetch = globalThis.fetch;\n'
            'globalThis.fetch = async (url, init = {}) => (String(url) === "/api/chat/recovery_key"'
            ' ? { ok: true, status: 200, json: async () => ({ recovery_key: "KEY" }) } : realFetch(url, init));\n'
            'const p1 = await K.prekeyFromPassword("p1-password");\n' + held("bob", "p1", ref=EARLIER)))
    assert got["protect"] is True and got["saved"] == [] and got["left"] is False


@node
@installed
def test_a_pre_key_that_names_no_account_is_never_used():
    for stored in ('"QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE"', 'JSON.stringify({ v: 1, k: "QUFB" })', '"not json {"'):
        got = run("""
const protect = await boxTitled("Protect your messages");
console.log(JSON.stringify({ protect, left: tab.has("uqqus_chat_prekey") }));
process.exit(0);
""", setup=NEW_ACCOUNT + 'tab.set("uqqus_chat_prekey", %s);\n' % stored)
        assert got == {"protect": True, "left": False}, stored


@node
@installed
def test_a_pre_key_that_didnt_open_the_copy_never_locks_the_key_made_by_start_over():
    """The account has a copy the sign-in's pre-key doesn't open (the password changed since): after Start over, the new key is locked
    with a password asked for now, never with that pre-key."""
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
const protect = await boxTitled("Protect your messages");
console.log(JSON.stringify({ protect, seen, saved }));
process.exit(0);
""", setup='const stale = await K.prekeyFromPassword("old-password");\n' + held("bob", "stale"))
    assert got["protect"] is True and got["saved"] == []


@node
@installed
def test_the_start_over_question_says_what_comes_back_and_asks_the_password():
    """5.5: Start over finishes here now (the site makes its key upload), so the question says what this browser's older stores bring
    back, that it ends a rescue, and asks the password."""
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
const lines = docBody.all("p").map((p) => p.textContent);
console.log(JSON.stringify({ lines }));
process.exit(0);
""")
    assert "Messages this browser could already read will come back here." in got["lines"]
    assert "This also ends any rescue from a browser where you used chat." in got["lines"]
    assert any(text.startswith("Your chats will work again on this device") for text in got["lines"])
    assert "Enter your password to confirm." in got["lines"]


# --- once new keys are on the server, their locked copies and the code are finished whatever happens to the page (crypto.js keysMade)

@node
@installed
def test_a_page_that_stops_after_its_new_keys_are_made_still_saves_both_copies_and_shows_the_code():
    got = run("""
await boxTitled("Protect your messages");
stopped = true;
C.stopUnlocking();                                  // another tab took chat over
await tick();
const protectKept = titles().includes("Protect your messages");
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
C.stopUnlocking();
await tick();
const codeKept = titles().includes("Save your recovery code");
const shown = docBody.all("input").some((i) => i.readOnly && /^[A-Z2-9]{5}(-[A-Z2-9]{5}){4}$/.test(i.value));
docBody.find("I've saved it").click();
const answer = await ready;
console.log(JSON.stringify({ protectKept, codeKept, shown, answer, saved: saved.map((s) => Object.keys(s)), done, left: docBody.children.length }));
""", setup=NEW_ACCOUNT)
    assert got["protectKept"] is True and got["codeKept"] is True and got["shown"] is True
    assert got["saved"] == [["password_blob"], ["recovery_blob"]]
    assert got["answer"] is False and got["left"] == 0                # then it stops: nothing more is done with the stopped client
    assert "loadSessionBackupPrivateKeyFromSecretStorage" not in got["done"]


@node
@installed
def test_a_page_that_stops_after_start_over_made_new_keys_still_saves_their_copies():
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Protect your messages");
stopped = true;
C.stopUnlocking();
await tick();
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
console.log(JSON.stringify({ answer, saved: saved.map((s) => Object.keys(s)), done }));
""")
    assert got["done"][:1] == ["resetEncryption"]
    assert got["saved"] == [["password_blob"], ["recovery_blob"]] and got["answer"] is False


def test_the_sign_in_forms_keep_the_pre_key_with_the_account_it_was_typed_for():
    chat_key = (ROOT / "ruqqus" / "assets" / "js" / "chat_key.js").read_text(encoding="utf-8")
    keylock = (SRC / "keylock.js").read_text(encoding="utf-8")
    assert "sessionStorage.setItem(STORE, JSON.stringify({ v: 1, name: accountName(account), k: text, at: at, ref: ref || '' }));" in chat_key
    assert "sendRef(form, ref);\n      prekey(field.value).then(function (text) { remember(text, account, ref); })" in chat_key
    assert "remember(pre[1], accountOf(form), form.getAttribute('data-chat-prekey-ref'));" in chat_key
    assert "remember(pre[1]);" not in chat_key
    # one rule for the name, in both copies
    assert "return String(name || '').trim().replace(/^@+/, '').toLowerCase();" in chat_key
    assert 'return String(name || "").trim().replace(/^@+/, "").toLowerCase();' in keylock
    templates = ROOT / "ruqqus" / "templates"
    assert 'id="qr-upgrade-form" data-chat-account="{{ v.username }}" data-chat-prekey>' in (templates / "login_qr_upgrade.html").read_text(encoding="utf-8")
    assert ('data-chat-account="{{ v.username }}" data-chat-prekey-ref="{{ session.get(\'chat_prekey_ref\') or \'\' }}" data-chat-rekey>'
            in (templates / "settings_security.html").read_text(encoding="utf-8"))
    for name in ("login.html", "sign_up.html"):
        assert 'name="username"' in (templates / name).read_text(encoding="utf-8"), name
    chat_page = (templates / "chat.html").read_text(encoding="utf-8")
    assert "username: \"{{ v.username }}\"," in chat_page
    assert 'prekeyRef: {{ (session.get("chat_prekey_ref") or "") | tojson }},' in chat_page
    crypto = (SRC / "crypto.js").read_text(encoding="utf-8")
    assert crypto.count("takePrekey(site && site.username, site && site.prekeyRef)") == 2 and "takePrekey()" not in crypto
    # the sign-ins keep the id their form sent: the password form, its two-factor step, sign-up and the QR sign-in's password
    login = (ROOT / "ruqqus" / "routes" / "login.py").read_text(encoding="utf-8")
    assert 'complete_sign_in(account, sign_in_history.METHOD_PASSWORD, prekey_ref=request.form.get("chat_prekey_ref"))' in login
    assert login.count('prekey_ref=session_cookie.prekey_ref(request.form.get("chat_prekey_ref"))') == 2
    assert "session_cookie.keep_prekey_ref(flask_session, prekey_ref)" in login
    assert 'session_cookie.keep_prekey_ref(session, request.form.get("chat_prekey_ref"))' in login
    assert '<input type="hidden" name="chat_prekey_ref" value="{{ prekey_ref or \'\' }}">' in (templates / "login_2fa.html").read_text(encoding="utf-8")
    qr = (ROOT / "ruqqus" / "routes" / "qr_login_pages.py").read_text(encoding="utf-8")
    assert 'session_cookie.keep_prekey_ref(flask_session, request.form.get("chat_prekey_ref"))' in qr


# --- a pre-key made before a password change is never used (chat_key.js markChanged / remember `at`, keylock.js takePrekey)

SHARED = '''const shared = new Map();
globalThis.localStorage = { getItem: (k) => (shared.has(k) ? shared.get(k) : null), setItem: (k, v) => { shared.set(k, String(v)); },
                            removeItem: (k) => { shared.delete(k); } };
'''


def held_at(name, prekey, at, ref=REF):
    return 'tab.set("uqqus_chat_prekey", JSON.stringify({ v: 1, name: %s, k: %s, at: %d, ref: %s }));\n' % (
        json.dumps(name), prekey, at, json.dumps(ref))


@node
@installed
def test_a_pre_key_made_before_a_password_change_in_another_tab_never_locks_a_new_accounts_key():
    """Bob signed in with P1 in this tab and never opened chat; in another tab he changed the password to P2 (chat never set up, so
    nothing could be locked again there). His first set-up here asks for the password, and the copy opens with P2, never P1."""
    got = run("""
const protect = await boxTitled("Protect your messages");
const leftAfter = tab.has("uqqus_chat_prekey");
typePassword("p2-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ protect, leftAfter, answer,
  p1: await opens(blob, p1), p2: await opens(blob, await K.prekeyFromPassword("p2-password")) }));
""", setup=NEW_ACCOUNT + SHARED + 'const p1 = await K.prekeyFromPassword("p1-password");\n' + held_at("bob", "p1", 1000)
        + 'shared.set("uqqus_chat_prekey_before", "2000");\n')
    assert got["protect"] is True and got["leftAfter"] is False and got["answer"] is True
    assert got["p1"] is None and got["p2"] == "KEY"


@node
@installed
def test_a_pre_key_made_after_the_last_password_change_is_still_used():
    got = run("""
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ seen, answer, p2: await opens(blob, p2) }));
""", setup=NEW_ACCOUNT + SHARED + 'const p2 = await K.prekeyFromPassword("p2-password");\n' + held_at("bob", "p2", 3000)
        + 'shared.set("uqqus_chat_prekey_before", "2000");\n')
    assert got["seen"] == ["Save your recovery code"] and got["answer"] is True and got["p2"] == "KEY"


CHAT_KEY = ROOT / "ruqqus" / "assets" / "js" / "chat_key.js"

REKEY = r"""
const shared = new Map(), tab = new Map();
const shelf = (map) => ({ getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => { map.set(k, String(v)); },
                          removeItem: (k) => { map.delete(k); } });
globalThis.localStorage = shelf(shared);
globalThis.sessionStorage = shelf(tab);
globalThis.window = globalThis;
const fields = { "[name=old_password]": { value: "p1-password" }, "[name=new_password]": { value: "p2-password" } };
let submitted = 0;
const listeners = [];
const form = {
  dataset: {}, appended: [],
  getAttribute: (k) => (k === "data-chat-account" ? "Bob" : k === "data-chat-prekey-ref" ? __REF__ : null),
  querySelector: (sel) => fields[sel] || null,
  addEventListener: (name, fn) => listeners.push(fn),
  appendChild(el) { this.appended.push(el); },
  requestSubmit() { submitted += 1; },
};
globalThis.document = {
  querySelectorAll: (sel) => (sel === "form[data-chat-rekey]" ? [form] : []),
  createElement: () => ({}),
};
globalThis.fetch = async () => ({ ok: true, json: async () => (__INFO__) });
(0, eval)(__CODE__);
const ChatKey = window.ChatKey;
const p1 = await ChatKey.prekey("p1-password");
tab.set("uqqus_chat_prekey", JSON.stringify({ v: 1, name: "bob", k: p1, at: Date.now() - 5, ref: __REF__ }));  // the sign-in's, from P1
const K = await import(__SRC__ + "/keylock.js");
for (const fn of listeners) fn({ preventDefault() {} });
for (let i = 0; i < 400 && !submitted; i += 1) await new Promise((r) => setTimeout(r, 10));
const afterTab = K.takePrekey("bob", __REF__);
// the same tab signs in again with the new password: that pre-key is used
ChatKey.remember(await ChatKey.prekey("p2-password"), "bob", __REF__);
const fresh = K.takePrekey("bob", __REF__);
console.log(JSON.stringify({ submitted, afterTab, marked: shared.has("uqqus_chat_prekey_before"),
  fresh: fresh === await ChatKey.prekey("p2-password"), appended: form.appended.length }));
"""


def run_rekey(info):
    code = (REKEY.replace("__INFO__", json.dumps(info)).replace("__CODE__", json.dumps(CHAT_KEY.read_text(encoding="utf-8")))
            .replace("__SRC__", json.dumps(SRC.as_uri())).replace("__REF__", json.dumps(REF)))
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60, cwd=CHAT_SRC)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@node
@installed
def test_a_password_change_with_no_chat_copy_leaves_no_old_pre_key_and_marks_the_change_for_every_tab():
    # chat never set up: /api/chat/key answers no password copy, so nothing is locked again; the old pre-key goes, and the time is noted
    for info in ({"salt": "s", "password_blob": None, "recovery_blob": None, "legacy": False}, None):
        got = run_rekey(info)
        assert got["submitted"] == 1 and got["appended"] == 0, info
        assert got["afterTab"] is None and got["marked"] is True and got["fresh"] is True, info



# --- the keys being changed on the server are one piece: never cut short by the page stopping (crypto.js keysMade, `critical`)

SLOW_RESET = """let letReset;
const resetGate = new Promise((resolve) => { letReset = resolve; });
backend.resetEncryption = async () => { done.push("resetEncryption:begun"); await resetGate; done.push("resetEncryption"); };
"""


@node
@installed
def test_a_page_taken_over_in_the_middle_of_start_over_finishes_the_reset_and_its_new_keys_and_the_keeper_waits_for_it():
    """Another tab took chat over while Start over was deleting the key backup and the secret storage: the reset and the new keys are
    finished (the keeper waits for them before it stops the client), and both copies are saved with the new key, never left naming the
    old one."""
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
for (let i = 0; i < 300 && !done.includes("resetEncryption:begun"); i += 1) await tick();
stopped = true;                                     // another tab took chat over: the keeper is shutting down
C.stopUnlocking();
const heldWork = holds.length;
let settledAt = null;
if (holds[0]) holds[0].then(() => { settledAt = [...done]; });
letReset();
await boxTitled("Protect your messages");
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ heldWork, settledAt, done, answer, saved: saved.map((s) => Object.keys(s)),
  opens: await opens(blob, await K.prekeyFromPassword("bob-password")) }));
""", setup=SLOW_RESET)
    assert got["heldWork"] == 1
    assert got["done"][:3] == ["resetEncryption:begun", "resetEncryption", "bootstrapSecretStorage"]
    assert got["settledAt"][-1] == "bootstrapSecretStorage"          # the keeper's wait ends only once the new keys are made
    assert got["saved"] == [["password_blob"], ["recovery_blob"]] and got["opens"] == "KEY"
    assert got["answer"] is False                                      # then it stops: nothing more is done with the stopped client


@node
@installed
def test_a_first_set_up_is_held_for_the_keeper_too():
    got = run("""
await boxTitled("Protect your messages");
console.log(JSON.stringify({ holds: holds.length, done }));
process.exit(0);
""", setup=NEW_ACCOUNT)
    assert got["holds"] == 1 and got["done"] == ["bootstrapCrossSigning", "bootstrapSecretStorage"]


def test_the_chat_page_hands_its_keepers_wait_to_the_unlock():
    keeper = (SRC / "keeper.js").read_text(encoding="utf-8")
    closing = keeper.split("  shutdown() {", 1)[1].split("\n  }\n", 1)[0]
    assert closing.index("await this.storeOpening;") < closing.index("await this.keyWork;") < closing.index("this.client.stopClient()")


def test_the_browser_specs_read_the_pre_key_as_it_is_kept():
    # chat_key.js keeps {v, name, k, at}: a spec that still looked for the old bare key failed at the real sign-in and checked nothing after
    ui = ROOT / "scripts" / "ui"
    session = (ui / "chat_session.spec.ts").read_text(encoding="utf-8")
    assert ("expect(JSON.parse(key || \"null\")).toEqual({ v: 1, name: A.name.toLowerCase(), k: expect.stringMatching(/^[\\w-]{40,}$/), "
            "at: expect.any(Number), ref: expect.stringMatching(/^[0-9a-f]{32}$/) });") in session
    assert "expect(key).toMatch(" not in session
    ads = (ui / "ads.spec.ts").read_text(encoding="utf-8")
    assert ('.toEqual({ v: 1, name: "ui_ads_fresh", k: expect.stringMatching(/^[\\w-]{40,}$/), at: expect.any(Number), '
            'ref: expect.stringMatching(/^[0-9a-f]{32}$/) });') in ads


# --- another tab of the account, stopped with its new keys' copies still pending, is waited for (crypto.js otherTabFinishing, KEYS_LOCK)

LOCKS = """const busy = new Map(), lockLog = [];
Object.defineProperty(globalThis, "navigator", { configurable: true, value: { locks: {
  async request(name, options, work) {
    if (typeof options === "function") { work = options; options = {}; }
    if (options.ifAvailable && busy.has(name)) return work(null);
    while (busy.has(name)) await busy.get(name).gone;
    let gone;
    const entry = { gone: new Promise((r) => { gone = r; }) };
    busy.set(name, entry);
    lockLog.push("take " + name);
    try { return await work({ name }); } finally { busy.delete(name); lockLog.push("drop " + name); gone(); }
  },
} } });
client.getUserId = () => "@ruqqus_1:s";
const KEYS = "uqqus-chat-keys:@ruqqus_1:s";
let gets = 0;
const plainFetch = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => { if (String(url) === "/api/chat/key" && init.method !== "POST") gets += 1; return plainFetch(url, init); };
const lines = [];
"""


def run_waiting(scenario, setup=""):
    """As run(), with Web Locks and the page's `wait` line recorded."""
    code = (PRELUDE.replace("__SRC__", json.dumps(SRC.as_uri())).replace("__REF__", json.dumps(REF)).replace("__SETUP__", LOCKS + setup)
            .replace("hold: (work) => holdHook(work),\n", "hold: (work) => holdHook(work), wait: (t) => lines.push(t),\n") + scenario)
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60, cwd=CHAT_SRC)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@node
@installed
def test_a_tab_that_takes_chat_over_waits_for_the_other_tabs_new_keys_copies_and_never_offers_start_over():
    """Tab A made the account's keys and stopped at "Protect your messages" when this tab took chat over and opened the same store: keys
    there, no copies on the site yet. Offered Start over and pressed, A's late save of the deleted key's copies overwrote these, and no
    device could unlock again. This page says another tab is finishing, asks nothing and reads nothing until A lets the lock go, then
    reads the copies A saved."""
    got = run_waiting("""
for (let i = 0; i < 50 && !lines.length; i += 1) await tick();
const whileHeld = { lines: lines.slice(), titles: titles(), gets, posted: posted.slice() };
keyInfo = { salt: "salt", password_blob: "PW", recovery_blob: "RC", legacy: false };
backend.getCrossSigningStatus = async () => ({ privateKeysCachedLocally: { selfSigningKey: true } });
letOtherGo();
const answer = await ready;
console.log(JSON.stringify({ whileHeld, answer, lines, seen, gets, done }));
""", setup="""let letOtherGo;
navigator.locks.request(KEYS, () => new Promise((r) => { letOtherGo = r; }));   // tab A, its copies still pending
await new Promise((r) => setTimeout(r, 0));
keyInfo = { salt: "salt", password_blob: null, recovery_blob: null, legacy: false };   // A's keys are on the server, no copies yet
""")
    assert got["whileHeld"] == {"lines": ["Finishing chat set-up in another tab…"], "titles": [], "gets": 0, "posted": []}
    assert got["answer"] is True and got["seen"] == [] and got["gets"] == 1
    assert got["lines"] == ["Finishing chat set-up in another tab…", ""]
    assert "resetEncryption" not in got["done"]


@node
@installed
def test_a_page_holds_the_lock_while_its_new_keys_copies_are_pending_and_lets_it_go_once_they_are_saved():
    got = run_waiting("""
await boxTitled("Protect your messages");
const heldAtPassword = lockLog.filter((l) => l.endsWith(KEYS));
const pendingAtPassword = C.keysPending() !== null;
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
const stillHeld = lockLog.filter((l) => l.endsWith(KEYS)).slice(-1)[0];
let settled = false;
C.keysPending().then(() => { settled = true; });
docBody.find("I've saved it").click();
const answer = await ready;
await tick();
console.log(JSON.stringify({ heldAtPassword, pendingAtPassword, stillHeld, settled, answer, after: C.keysPending(),
  log: lockLog.filter((l) => l.endsWith(KEYS)) }));
""", setup=NEW_ACCOUNT)
    # the first "take/drop" is the check at the start (free); then it is taken as the keys begin, and held to the end
    assert got["heldAtPassword"] == ["take " + "uqqus-chat-keys:@ruqqus_1:s", "drop uqqus-chat-keys:@ruqqus_1:s",
                                     "take uqqus-chat-keys:@ruqqus_1:s"]
    assert got["pendingAtPassword"] is True and got["stillHeld"] == "take uqqus-chat-keys:@ruqqus_1:s"
    assert got["settled"] is True and got["answer"] is True and got["after"] is None
    assert got["log"][-1] == "drop uqqus-chat-keys:@ruqqus_1:s"


@node
@installed
def test_a_page_whose_copies_failed_to_save_lets_the_lock_go_too():
    got = run_waiting("""
await boxTitled("Protect your messages");
typePassword("bob-password");
docBody.find("Continue").click();
let error = null;
try { await ready; } catch (e) { error = String(e && e.message); }
console.log(JSON.stringify({ error, after: C.keysPending(), last: lockLog.filter((l) => l.endsWith(KEYS)).slice(-1)[0] }));
""", setup=NEW_ACCOUNT + """const okFetch = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => (init.method === "POST" && String(url) === "/api/chat/key"
  ? { ok: false, status: 503, type: "basic", json: async () => ({}) } : okFetch(url, init));
""")
    assert got["error"] and got["after"] is None and got["last"] == "drop uqqus-chat-keys:@ruqqus_1:s"


def run_seen(scenario, setup="", visible="false", wait_ms=120000):
    """As run_waiting(), on a page the member can or can't see (index.js inSight), waiting `wait_ms` at most for another tab's lock."""
    code = (PRELUDE.replace("__SRC__", json.dumps(SRC.as_uri())).replace("__REF__", json.dumps(REF)).replace("__SETUP__", LOCKS + setup)
            .replace("hold: (work) => holdHook(work),\n",
                     "hold: (work) => holdHook(work), wait: (t) => lines.push(t), inSight: () => visible, waitMs: %d,\n" % wait_ms)
            .replace("let stopped = false;", "let stopped = false;\nlet visible = %s;" % visible) + scenario)
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60, cwd=CHAT_SRC)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


SEEN_REPORT = """const answer = await ready;
await tick();
console.log(JSON.stringify({ asked, answer, done, saved: saved.map((s) => Object.keys(s)), left: docBody.children.length, notices,
  keys: lockLog.filter((l) => l.endsWith(KEYS)), after: C.keysPending() }));
"""


@node
@installed
def test_a_frame_nobody_sees_makes_no_new_keys_and_asks_nothing():
    """The hidden Send-in-chat frame (or the side panel's, closed) of an account with no keys yet: new keys begun there would hold the keys
    lock with "Save your recovery code" shown to nobody, and every other chat tab would wait on it. It makes none and says to open Chat."""
    got = run_seen(SEEN_REPORT, setup=NEW_ACCOUNT)
    assert got["answer"] is False and got["asked"] == 0 and got["left"] == 0
    assert got["done"] == [] and got["saved"] == [] and got["after"] is None
    assert got["keys"] == ["take uqqus-chat-keys:@ruqqus_1:s", "drop uqqus-chat-keys:@ruqqus_1:s"]      # only the check at the start
    assert got["notices"] == ["Your password was changed, so chat is starting again here. "
                              "Open Chat to finish setting up your messages on this device."]
    # seen, the same account's keys are made and the code shown, as on the chat page
    shown = run_seen('await boxTitled("Protect your messages");\nconsole.log(JSON.stringify({ done }));\n', setup=NEW_ACCOUNT, visible="true")
    assert "bootstrapCrossSigning" in shown["done"]


@node
@installed
def test_a_frame_nobody_sees_opens_no_box_to_unlock_either():
    """Keys on the server, none here and no pre-key: the password box would wait in a frame nobody can see."""
    got = run_seen(SEEN_REPORT)
    assert got["answer"] is False and got["asked"] == 0 and got["left"] == 0 and got["saved"] == []
    assert got["notices"][-1].endswith("Open Chat to finish setting up your messages on this device.")


@node
@installed
def test_a_tab_never_waits_for_ever_for_another_tabs_keys_lock():
    """A page holding the lock can be waiting on a person, or be a frame nobody sees: after `waitMs` this page reads the key info afresh."""
    got = run_seen("""
const answer = await ready;
console.log(JSON.stringify({ answer, lines, gets }));
""", setup="""navigator.locks.request(KEYS, () => new Promise(() => {}));     // another page that never lets it go
await new Promise((r) => setTimeout(r, 0));
backend.getCrossSigningStatus = async () => ({ privateKeysCachedLocally: { selfSigningKey: true } });
keyInfo = { salt: "salt", password_blob: "PW", recovery_blob: "RC", legacy: false };
""", visible="true", wait_ms=50)
    assert got["answer"] is True and got["gets"] == 1
    assert got["lines"] == ["Finishing chat set-up in another tab…", ""]


INDEX_DOM = r"""
class El {
  constructor(id) { this.id = id; this.children = []; this.attrs = {}; this.textContent = ""; this.className = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  prepend(k) { k.parent = this; this.children.unshift(k); }
  append(k) { k.parent = this; this.children.push(k); }
  replaceChildren(...kids) { this.children = []; for (const k of kids) this.append(k); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; }
}
const pane = new El("chat-list-pane"), list = new El("chat-conversation-list");
pane.append(list);
list.append(new El("a conversation"));
const all = (el) => [el].concat(...el.children.map(all));
globalThis.document = { createElement: () => new El(""), getElementById: (id) => all(pane).find((e) => e.id === id) || null };
const boot = { single: false };
let gone = false;
const chatGone = () => gone;
"""


def _index_function(name):
    index = (SRC / "index.js").read_text(encoding="utf-8")
    start = index.index("function %s(" % name)
    return index[start:index.index("\n}\n", start) + 3]


@node
def test_the_wait_line_never_takes_the_list_or_the_note_a_stopped_page_left():
    """keysWaitLine once drew into the list's own element: the line replaced the conversation list and taking it away emptied it, or
    erased "Chat is open in another tab." and its Use it here when the page lost chat while it waited."""
    code = INDEX_DOM + _index_function("noteArea") + _index_function("bannerHome") + _index_function("keysWaitLine") + r"""
const ids = () => all(pane).map((e) => e.id || e.textContent);
keysWaitLine("Finishing chat set-up in another tab…");
const waiting = ids();
keysWaitLine("");
const after = ids();
keysWaitLine("Finishing chat set-up in another tab…");
gone = true;                                                     // another tab took chat over: the page's note goes in the list
list.replaceChildren(new El("Chat is open in another tab."));
keysWaitLine("");
const stopped = ids();
keysWaitLine("Finishing chat set-up in another tab…");          // nothing is drawn once the page has stopped
console.log(JSON.stringify({ waiting, after, stopped, late: ids() }));
"""
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["waiting"] == ["chat-list-pane", "chat-keys-wait", "chat-conversation-list", "a conversation"]
    assert got["after"] == ["chat-list-pane", "chat-conversation-list", "a conversation"]
    assert got["stopped"] == got["late"] == ["chat-list-pane", "chat-conversation-list", "Chat is open in another tab."]


@node
def test_only_a_frame_on_the_screen_counts_as_seen():
    code = _index_function("inSight") + r"""
let role = "top", rects = 1, cross = false;
const frameRole = () => role;
globalThis.window = { get frameElement() { if (cross) throw new Error("cross-origin"); return role === "top" ? null : { getClientRects: () => ({ length: rects }) }; } };
const out = {};
for (const [r, n, c] of [["top", 0, false], ["single", 0, false], ["share", 1, false], ["panel", 1, false], ["panel", 0, false], ["panel", 1, true]]) {
  role = r; rects = n; cross = c;
  out[r + n + c] = inSight();
}
console.log(JSON.stringify(out));
"""
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"top0false": True, "single0false": True, "share1false": False, "panel1false": True,
                                      "panel0false": False, "panel1true": False}


# --- Start over finishes here: the member's password (and two-factor code) buys a grant first (routes/chat_key_reset_pages.py)

@node
@installed
def test_a_wrong_password_stays_in_the_box_and_resets_nothing():
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("wrong");
docBody.find("Start over").click();
for (let i = 0; i < 20; i += 1) await tick();
const still = titles().includes("Start over?");
const said = docBody.all("p").map((p) => p.textContent).includes("That isn't your password.");
docBody.find("Cancel").click();
const answer = await ready;
console.log(JSON.stringify({ still, said, answer, done, armed, grantAsks }));
""")
    assert got["still"] is True and got["said"] is True and got["answer"] is False
    assert got["done"] == [] and got["armed"] == [] and got["grantAsks"] == [["password"]]


@node
@installed
def test_two_factor_asks_the_code_in_a_second_box_and_sends_both():
    got = run("""
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Enter your two-factor code");
docBody.all("input").find((i) => i.type === "text").value = "000000";
docBody.find("Start over").click();
for (let i = 0; i < 20; i += 1) await tick();
const wrongStays = titles().includes("Enter your two-factor code") && done.length === 0;
docBody.all("input").find((i) => i.type === "text").value = "123456";
docBody.find("Start over").click();
await boxTitled("Protect your messages");
console.log(JSON.stringify({ wrongStays, done, armed, grantAsks }));
process.exit(0);
""", setup="keyInfo = { ...keyInfo, mfa: true };\n")
    assert got["wrongStays"] is True
    assert got["grantAsks"] == [["otp", "password"], ["otp", "password"]]
    assert got["armed"][0] == "G1" and got["done"][:1] == ["resetEncryption"]


@node
@installed
def test_a_reset_whose_upload_failed_asks_once_more_and_makes_the_new_keys():
    """resetEncryption may have deleted the backup before its upload failed: the page asks the password once more (a box that stays
    whatever happens to the page) and makes the new keys, cross-signing and backup included, with a fresh grant."""
    got = run("""
let resets = 0;
backend.resetEncryption = async () => { resets += 1; if (resets === 1) { done.push("resetEncryption:failed"); throw new Error("upload refused"); }
                                        done.push("resetEncryption"); };
grantAnswer = (sent) => (sent.password === "bob-password" ? { status: 200, body: { grant: "G" + (grantAsks.length) } }
                                                         : { status: 403, body: { error: "That isn't your password.", field: "password" } });
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Finish starting over");
typePassword("bob-password");
docBody.find("Try again").click();
await boxTitled("Protect your messages");
console.log(JSON.stringify({ done, armed }));
process.exit(0);
""")
    # the whole reset again (whichever step failed), then only the new secret storage: one upload per grant
    assert got["done"][:3] == ["resetEncryption:failed", "resetEncryption", "bootstrapSecretStorage"]
    assert "bootstrapCrossSigning" not in got["done"]
    assert got["armed"][:4] == ["G1", None, "G2", None]


@node
@installed
def test_a_second_failure_is_said_plainly_and_chat_stays_locked():
    got = run("""
backend.resetEncryption = async () => { throw new Error("upload refused"); };
backend.bootstrapCrossSigning = async () => { throw new Error("upload refused again"); };
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Finish starting over");
typePassword("bob-password");
docBody.find("Try again").click();
const answer = await ready;
console.log(JSON.stringify({ answer, notices, armed }));
""")
    assert got["answer"] is False
    assert any("Chat couldn't finish starting over. Reload to try again." in n for n in got["notices"])
    assert got["armed"][-1] is None                                    # never left armed


@node
@installed
def test_after_a_failed_start_over_the_next_unlock_goes_straight_back_to_start_over():
    """The keys this device holds may be half changed (new private keys here, the old public ones on the server): the next Unlock in the
    same page never trusts them (crypto.js resetUnfinished), and says why a failed one stopped whichever call it was."""
    got = run("""
backend.resetEncryption = async () => { throw new Error("upload refused"); };
await boxTitled("Unlock your messages");
docBody.find("I don't have it: start over").click();
await boxTitled("Start over?");
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Finish starting over");
typePassword("bob-password");
docBody.find("Try again").click();
const first = await ready;
// the device now says its keys are here: it must not be believed
backend.getCrossSigningStatus = async () => ({ privateKeysCachedLocally: { selfSigningKey: true } });
backend.resetEncryption = async () => { done.push("resetEncryption"); };
const later = [];
const again = C.ensureEncryptionReady(client, { formkey: "f", mxid: "@ruqqus_1:s", username: "Bob" },
  { notify: (t) => later.push(t), keyReset: { arm: (g) => armed.push(g), disarm: () => armed.push(null) } });
const straight = await boxTitled("Start over?");
const firstTitleAfter = titles()[0];
typePassword("bob-password");
docBody.find("Start over").click();
await boxTitled("Protect your messages");
console.log(JSON.stringify({ first, notices, straight, firstTitleAfter, done }));
process.exit(0);
""")
    assert got["first"] is False and any("couldn't finish starting over" in n for n in got["notices"])
    assert got["straight"] is True and got["firstTitleAfter"] == "Start over?"
    assert "resetEncryption" in got["done"]


# --- the rescue (rescue.js): offered only once the password is known not to open the copy, before the recovery code is asked for

BUNDLE = ('rescueFound = { d: "UQOLD0000000", master: "MASTER", bundle: { cross_signing: { master_key: "m" }, backup: { backup_version: "7" } } };\n'
          'globalThis.localStorage = { store: new Map(), getItem(k) { return this.store.has(k) ? this.store.get(k) : null; },'
          ' setItem(k, v) { this.store.set(k, String(v)); }, removeItem(k) { this.store.delete(k); } };\n')


@node
@installed
def test_a_sign_in_whose_pre_key_doesnt_open_the_copy_is_offered_the_rescue_with_the_password():
    got = run("""
await boxTitled("We found your chat on this browser");
const lines = docBody.all("p").map((p) => p.textContent);
typePassword("bob-password");
docBody.find("Use them").click();
await boxTitled("Save your recovery code");
const codeLines = docBody.all("p").map((p) => p.textContent);
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ answer, lines, codeLines, done, secretStorageAsked, seen, saved: saved.map((s) => Object.keys(s)),
  opens: await opens(blob, await K.prekeyFromPassword("bob-password")) }));
""", setup='const stale = await K.prekeyFromPassword("old-password");\n' + held("bob", "stale") + BUNDLE)
    assert got["answer"] is True
    assert "Unlock your messages" not in got["seen"] and "Enter your recovery code" not in got["seen"]
    assert any("from before your password changed" in t for t in got["lines"])
    assert "Your old recovery code no longer works." in got["codeLines"]
    # the old keys into this device, signed with them, then a new secret storage keeping the current backup (no new keys, no grant), and
    # never bootstrapCrossSigning, which would read the old secret storage, whose key is what was lost
    assert got["done"][:3] == ["importSecretsBundle", "crossSignDevice UQNEW0000000", "bootstrapSecretStorage"]
    assert "bootstrapCrossSigning" not in got["done"]
    assert got["secretStorageAsked"][0] == {"newStorage": True, "newBackup": False}
    assert got["saved"] == [["password_blob"], ["recovery_blob"]] and got["opens"] == "KEY"


@node
@installed
def test_a_password_typed_here_that_doesnt_open_the_copy_is_offered_the_rescue_without_asking_again():
    got = run("""
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
const field = docBody.all("input").some((i) => i.type === "password");
docBody.find("Use them").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const answer = await ready;
const blob = saved.find((s) => s.password_blob).password_blob;
console.log(JSON.stringify({ answer, field, opens: await opens(blob, await K.prekeyFromPassword("bob-password")) }));
""", setup=BUNDLE)
    assert got["answer"] is True and got["field"] is False and got["opens"] == "KEY"


@node
@installed
def test_not_now_goes_on_to_the_recovery_code_and_changes_nothing():
    got = run("""
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
docBody.find("Not now").click();
const code = await boxTitled("Enter your recovery code");
console.log(JSON.stringify({ code, done, saved }));
process.exit(0);
""", setup=BUNDLE)
    assert got["code"] is True and got["done"] == [] and got["saved"] == []


@node
@installed
def test_a_browser_with_nothing_to_rescue_asks_for_the_recovery_code_as_before():
    got = run("""
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
const code = await boxTitled("Enter your recovery code");
console.log(JSON.stringify({ code, rescueAsked, seen }));
process.exit(0);
""")
    assert got["code"] is True and got["rescueAsked"] == 1 and "We found your chat on this browser" not in got["seen"]


@node
@installed
def test_a_password_that_opens_the_copy_is_never_offered_a_rescue():
    got = run("""
const answer = await ready;
console.log(JSON.stringify({ answer, rescueAsked, seen: titles() }));
""", setup='const pre = await K.prekeyFromPassword("bob-password");\n'
           'keyInfo = { ...keyInfo, recovery_blob: "R", password_blob: await K.lock(await K.passwordKey(pre, "salt"), "KEY") };\n'
           + held("bob", "pre") + BUNDLE)
    assert got["answer"] is True and got["rescueAsked"] == 0


@node
@installed
def test_a_bundle_whose_backup_is_gone_gets_a_new_backup():
    got = run("""
serverBackup = { version: "9" }; localBackup = "7";             // another backup now: the bundle's key is for one that is gone
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
const lines = docBody.all("p").map((p) => p.textContent);
docBody.find("Use them").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
await ready;
console.log(JSON.stringify({ secretStorageAsked, lines }));
""", setup=BUNDLE)
    assert got["secretStorageAsked"][0] == {"newStorage": True, "newBackup": True}
    assert any("only in the backup it opens" in t for t in got["lines"])        # the member was told before


@node
@installed
def test_a_backup_that_cant_be_read_means_no_rescue_is_offered_and_nothing_changes():
    got = run("""
serverBackup = "throw";
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
const code = await boxTitled("Enter your recovery code");
console.log(JSON.stringify({ code, seen, done }));
process.exit(0);
""", setup=BUNDLE)
    assert got["code"] is True and "We found your chat on this browser" not in got["seen"] and got["done"] == []


@node
@installed
def test_keys_a_start_over_made_stale_while_the_box_was_open_are_never_imported():
    got = run("""
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
serverMaster = "SOMEONE-STARTED-OVER";
docBody.find("Use them").click();
const answer = await ready;
console.log(JSON.stringify({ answer, done, notices, saved }));
""", setup=BUNDLE)
    assert got["answer"] is False and got["done"] == [] and got["saved"] == []
    assert any("no longer your account's" in n for n in got["notices"])


@node
@installed
def test_a_password_that_opens_the_copy_in_the_rescue_box_goes_the_ordinary_way():
    got = run("""
await boxTitled("We found your chat on this browser");
typePassword("bob-password");
docBody.find("Use them").click();
const answer = await ready;
console.log(JSON.stringify({ answer, done, saved, seen }));
""", setup='const right = await K.prekeyFromPassword("bob-password");\n'
           'const stale = await K.prekeyFromPassword("old-password");\n'
           'keyInfo = { ...keyInfo, recovery_blob: "R", password_blob: await K.lock(await K.passwordKey(right, "salt"), "KEY") };\n'
           + held("bob", "stale") + BUNDLE)
    assert got["answer"] is True and "importSecretsBundle" not in got["done"]
    assert "Save your recovery code" not in got["seen"]                         # no code replaced


@node
@installed
def test_a_rescue_that_stopped_part_way_is_finished_by_the_next_unlock_never_trusted():
    got = run("""
let failOnce = true;
backend.crossSignDevice = async (d) => { if (failOnce) { failOnce = false; throw new Error("signature upload failed"); } done.push("crossSignDevice " + d); };
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
docBody.find("Use them").click();
const first = await ready;
const marked = [...localStorage.store.keys()];
// the keys are on the device now: a plain Unlock would believe them; the mark sends it to finish instead
backend.getCrossSigningStatus = async () => ({ privateKeysCachedLocally: { selfSigningKey: true } });
const again = C.ensureEncryptionReady(client, { formkey: "f", mxid: "@ruqqus_1:s", username: "Bob" },
  { notify: (t) => notices.push(t), rescue: rescueHooks });
await boxTitled("Finish bringing your chat over");
typePassword("bob-password");
docBody.find("Finish").click();
await boxTitled("Save your recovery code");
docBody.find("I've saved it").click();
const second = await again;
console.log(JSON.stringify({ first, marked, second, done, left: [...localStorage.store.keys()] }));
""", setup=BUNDLE)
    assert got["first"] is False and got["marked"] == ["uqqus_chat_rescue:@ruqqus_1:s:UQNEW0000000"]
    assert got["second"] is True and "crossSignDevice UQNEW0000000" in got["done"] and got["left"] == []


@node
@installed
def test_keys_that_dont_match_once_imported_stop_the_rescue_and_say_so():
    got = run("""
backend.getCrossSigningKeyId = async () => "ANOTHER";
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("We found your chat on this browser");
docBody.find("Use them").click();
const answer = await ready;
console.log(JSON.stringify({ answer, notices, done, saved }));
""", setup=BUNDLE)
    assert got["answer"] is False and got["saved"] == []
    assert not any(d.startswith("crossSignDevice") for d in got["done"])
    assert any("couldn't bring your keys over" in n for n in got["notices"])


@node
@installed
def test_the_recovery_code_box_points_to_another_browser_only_when_this_one_had_nothing():
    """The forgotten-password screen's order: the code, a browser where chat was used, Start over. A browser that just offered its own
    rescue (declined) doesn't send the member to another one."""
    script = """
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
__DECLINE__
await boxTitled("Enter your recovery code");
const lines = docBody.all("p").map((p) => p.textContent);
const buttons = docBody.all("button").map((b) => b.textContent);
console.log(JSON.stringify({ lines, buttons }));
process.exit(0);
"""
    other = "No code? Sign in with your new password on a browser where you've used chat: it can bring your chat back."
    nothing = run(script.replace("__DECLINE__", ""))
    assert other in nothing["lines"] and "I don't have it: start over" in nothing["buttons"]
    declined = run(script.replace("__DECLINE__", 'await boxTitled("We found your chat on this browser");\ndocBody.find("Not now").click();'),
                   setup=BUNDLE)
    assert other not in declined["lines"]


@node
@installed
def test_the_code_box_offers_copy_and_download_and_saying_it_was_kept_is_told_to_the_site():
    got = run("""
await boxTitled("Protect your messages");
typePassword("bob-password");
docBody.find("Continue").click();
await boxTitled("Save your recovery code");
const buttons = docBody.all("button").map((b) => b.textContent);
const before = posted.includes("/api/chat/code_saved");
docBody.find("I've saved it").click();
await ready;
console.log(JSON.stringify({ buttons, before, after: posted.includes("/api/chat/code_saved") }));
""", setup=NEW_ACCOUNT)
    assert "Copy" in got["buttons"] and "Download" in got["buttons"]
    assert got["before"] is False and got["after"] is True


def run_new_code(scenario, setup=""):
    code = (PRELUDE.replace("__SRC__", json.dumps(SRC.as_uri())).replace("__REF__", json.dumps(REF)).replace("__SETUP__", setup)
            .replace('{ why: "password", notify', '{ newCode: true, why: "password", notify') + scenario)
    out = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60, cwd=CHAT_SRC)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


UNLOCKED = ('const pre = await K.prekeyFromPassword("bob-password");\n'
            'keyInfo = { ...keyInfo, recovery_blob: "R", password_blob: await K.lock(await K.passwordKey(pre, "salt"), "KEY") };\n'
            'backend.getCrossSigningStatus = async () => ({ privateKeysCachedLocally: { selfSigningKey: true } });\n')


@node
@installed
def test_a_new_code_is_asked_for_first_and_cancel_keeps_the_old_one():
    got = run_new_code("""
await boxTitled("Make a new recovery code?");
docBody.find("Cancel").click();
const answer = await ready;
console.log(JSON.stringify({ answer, seen, saved }));
""", setup=UNLOCKED)
    assert got["answer"] is True and got["saved"] == [] and "Save your recovery code" not in got["seen"]


@node
@installed
def test_the_new_code_box_stays_when_the_page_stops_after_its_copy_was_saved():
    got = run_new_code("""
await boxTitled("Make a new recovery code?");
docBody.find("Make a new code").click();
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("Save your recovery code");
stopped = true;
C.stopUnlocking();                                  // another tab took chat over: this box is the only place the new code is
await tick();
const kept = titles().includes("Save your recovery code");
docBody.find("I've saved it").click();
await ready;
console.log(JSON.stringify({ kept, savedCode: posted.includes("/api/chat/code_saved") }));
""", setup=UNLOCKED)
    assert got["kept"] is True and got["savedCode"] is True


@node
@installed
def test_a_rescue_that_couldnt_read_the_backup_still_points_to_another_browser():
    got = run("""
serverBackup = "throw";
await boxTitled("Unlock your messages");
typePassword("bob-password");
docBody.find("Unlock").click();
await boxTitled("Enter your recovery code");
const lines = docBody.all("p").map((p) => p.textContent);
console.log(JSON.stringify({ lines }));
process.exit(0);
""", setup=BUNDLE)
    assert any(t.startswith("No code? Sign in with your new password") for t in got["lines"])


def test_the_page_reads_recovery_new_once():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    block = index[index.index('get("recovery") === "new";'):index.index("const why = session.why;")]
    assert 'here.searchParams.delete("recovery");' in block and "window.history.replaceState(" in block
