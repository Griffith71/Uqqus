"""The running page's side of chat sessions (keeper.js, signed_out.js, lines.js and token.js openCryptoStore; the chat plan's 3.3, 4.4,
6.2 to 6.6), run under node with stand-ins for the browser (tests/chat_node_harness.py): a token the chat server refuses is renewed
through the site exactly once and handed to the SDK's retry, a renewal never holds the index lock across the network, a Log out in
the middle drops the answer, other tabs' changes stop or update this page, reloads are budgeted per role, a frame tells its parent,
the store's lock is let go only after the client that opened it stopped, a store that won't open is given up once and never deleted,
and each way chat can't start says so."""
from chat_node_harness import A, NEW, node, prefix, run

KEEPER = r"""
const K = await import(SRC + "/keeper.js");
const SO = await import(SRC + "/signed_out.js");
const L = await import(SRC + "/lines.js");
const posted = [], shown = [], listeners = {}, log = [], sleeps = [];
let reloads = 0;
const realSetTimeout = setTimeout;
const tick = (ms = 15) => new Promise((resolve) => realSetTimeout(resolve, ms));
const win = {
  tr: (t) => t,
  location: { origin: "https://site", search: "", reload: () => { reloads += 1; } },
  addEventListener: (name, fn) => { (listeners[name] = listeners[name] || []).push(fn); },
};
win.parent = win;
globalThis.window = win;
function asFrame(search) {
  win.location.search = search;
  win.parent = { postMessage: (m, origin) => posted.push({ ...m, origin }) };
}
const ui = {
  status: (t) => shown.push(["status", t]),
  stop: (t, o = {}) => shown.push(["stop", t, o.action || null]),
  banner: (t, o = {}) => shown.push(["banner", t, o.action || null]),
  notice: (t) => shown.push(["notice", t]),
};
function fakeClient(token) {
  return {
    token,
    getAccessToken() { return this.token; },
    setAccessToken(t) { log.push("set " + t); this.token = t; },
    stopClient() { log.push("stop"); },
    on(name, fn) { (this.handlers = this.handlers || {})[name] = fn; },
  };
}
const unknown = () => new Response(JSON.stringify({ errcode: "M_UNKNOWN_TOKEN", error: "Unknown token" }),
                                   { status: 401, headers: { "Content-Type": "application/json" } });
const BOOT = { formkey: "f", mxid: "@ruqqus_1:s" };
async function started(fields = {}) {
  serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", fields) });
  const session = await T.ensureSession(BOOT, "chat");
  await tick();                                  // the first start's legacy clean-up (token.js) is not what these tests look at
  sent.length = 0;
  deleted.length = 0;
  return session;
}
let clock = Date.now();
function keeperFor(session, options = {}) {
  const k = new K.SessionKeeper(session, {
    ui, role: "top", now: () => clock, random: () => 0,
    sleep: async (ms) => { sleeps.push({ ms, locked: busy.has("uqqus-chat-session") }); clock += ms; },
    ...options,
  });
  const client = fakeClient(session.accessToken);
  k.attach(client);
  return { k, client };
}
const HS = "https://m/_matrix/client/v3/sync";
const call = (k, token) => k.fetch(new URL(HS), { headers: { Authorization: "Bearer " + token } });
const renewals = () => sent.filter((r) => r.url === "/api/chat/token");
const renewAnswer = (token, extra = {}) => ({ body: { ...answer("@ruqqus_1:s", "UQNEW0000000", { access_token: token }), ...extra } });
async function opened(e) {
  const tk = await seal.tokenKey(seal.b64urlDecode(keyOf(e.d)));
  const box = await seal.unseal(tk, seal.tokenAad(e), e.sealed);
  return box && box.t;
}
const mine = () => getIndex().entries.find((e) => e.d === "UQNEW0000000");
"""


def keeper(scenario, **options):
    return run(KEEPER + scenario, **options)


@node
def test_a_refused_token_is_renewed_once_and_the_same_401_handed_back_for_the_sdk_to_retry():
    got = keeper("""
const session = await started();
const first = unknown();
const { k, client } = keeperFor(session, { fetch: async () => first });
serve = async () => renewAnswer("tok-2");
const res = await call(k, "tok-UQNEW0000000");
report({ same: res === first, status: res.status, log, token: client.token, entry: mine(), plain: await opened(mine()), reason: k.reason });
""")
    [renewal] = [r for r in got["sent"] if r["url"] == "/api/chat/token"]
    assert renewal["body"]["mode"] == "renew" and renewal["body"]["device_id"] == NEW and renewal["body"]["kind"] == "chat"
    assert not renewal["locked"] and renewal["redirect"] == "manual"
    assert got["same"] and got["status"] == 401                  # the SDK's own check sees a new token and retries
    assert got["log"] == ["set tok-2"] and got["token"] == "tok-2" and got["reason"] is None
    assert got["plain"] == "tok-2" and got["entry"]["gen"] == 2   # sealed, and other pages see the rotation by `gen`
    assert all("tok-2" not in value for value in got["stored"].values())


@node
def test_five_refusals_at_once_make_one_renewal():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => { await tick(30); return renewAnswer("tok-2"); };
await Promise.all([1, 2, 3, 4, 5].map(() => call(k, "tok-UQNEW0000000")));
report({ log });
""")
    assert len([r for r in got["sent"] if r["url"] == "/api/chat/token"]) == 1 and got["log"] == ["set tok-2"]


@node
def test_a_refusal_for_an_old_token_and_any_other_401_ask_nothing():
    got = keeper("""
const session = await started();
let answerWith = unknown;
const { k } = keeperFor(session, { fetch: async () => answerWith() });
const stale = await call(k, "a-token-swapped-already");
answerWith = () => new Response(JSON.stringify({ errcode: "M_FORBIDDEN" }), { status: 401 });
const other = await call(k, "tok-UQNEW0000000");
answerWith = unknown;
const elsewhere = await k.fetch(new URL("https://other.example/_matrix/x"), { headers: { Authorization: "Bearer tok-UQNEW0000000" } });
report({ statuses: [stale.status, other.status, elsewhere.status], log });
""")
    assert got["statuses"] == [401, 401, 401] and got["sent"] == [] and got["log"] == []


@node
def test_a_newer_token_another_page_sealed_is_adopted_with_no_site_call():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
const idx = getIndex();
const e = idx.entries.find((x) => x.d === "UQNEW0000000");
e.gen += 3;
e.sealed = await boxFor("@ruqqus_1:s", "UQNEW0000000", "chat", "tok-other-tab");
store.set("uqqus_chat_devices", JSON.stringify(idx));
const res = await call(k, "tok-UQNEW0000000");
report({ log, status: res.status });
""")
    assert got["sent"] == [] and got["log"] == ["set tok-other-tab"]


@node
def test_a_device_gone_marks_the_entry_removed_with_its_reason():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => ({ status: 409, body: { device_gone: true, reason: "password" } });
const res = await call(k, "tok-UQNEW0000000");
report({ log, reason: k.reason, entry: mine() });
""")
    assert got["reason"] == "removed" and got["log"] == []
    assert got["entry"]["s"] == "removed" and got["entry"]["why"] == "password" and "sealed" not in got["entry"]
    assert got["deleted"] == []                                   # kept for the harvest


@node
def test_a_device_a_running_page_found_gone_says_why_to_the_next_device_once():
    """The chat plan's spec 5: a renewal answered 409 marks the entry `removed` with the site's reason; the page starts again naming no
    device, so the site's answer has no `previous` to give, and the reason kept on the removed entry is what the new device's line says
    (token.js settle). Once: the removed entry's reason goes with it."""
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => ({ status: 409, body: { device_gone: true, reason: "signed_out_here" } });
await call(k, "tok-UQNEW0000000");
sent.length = 0;
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEXT000000") });
const next = await T.ensureSession(BOOT, "chat");
report({ nextWhy: next.why, asked: sent[0].body, old: getIndex().entries.find((e) => e.d === "UQNEW0000000") });
""")
    assert "device_id" not in got["asked"] and got["nextWhy"] == "signed_out_here"
    assert got["old"]["s"] == "removed" and got["old"]["why"] is None                     # told once
    assert [e for e in got["index"]["entries"] if e["d"] == "UQNEXT000000"][0]["why"] == "signed_out_here"


@node
def test_a_stale_form_key_is_refreshed_and_the_renewal_goes_through_without_a_reload():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
let asked = 0;
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { body: { formkey: "f2", mxid: "@ruqqus_1:s" } };
  asked += 1;
  return asked === 1 ? { status: 0, type: "opaqueredirect" } : renewAnswer("tok-2");
};
await call(k, "tok-UQNEW0000000");
report({ log, reloads, formkey: session.boot.formkey });
""")
    asks = [(r["url"], (r["body"] or {}).get("formkey")) for r in got["sent"]]
    assert asks == [("/api/chat/token", "f"), ("/api/chat/formkey", None), ("/api/chat/token", "f2")]
    assert got["log"] == ["set tok-2"] and got["reloads"] == 0 and got["formkey"] == "f2"


@node
def test_a_site_sign_in_that_ended_signs_out_and_takes_the_sealed_token_away():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => ({ status: 0, type: "opaqueredirect" });
await call(k, "tok-UQNEW0000000");
report({ reason: k.reason, entry: mine() });
""")
    assert [r["url"] for r in got["sent"]] == ["/api/chat/token", "/api/chat/formkey"]
    assert got["reason"] == "signed_out" and "sealed" not in got["entry"] and got["entry"]["s"] == "in"
    assert got["deleted"] == []


@node
def test_a_form_key_that_couldnt_be_asked_for_now_is_retried_never_read_as_signed_out():
    """A credential event that spared this sign-in leaves the page's key stale, and the renewal is the POST that meets it; if the GET for
    the new key fails for now (its 30 a minute shared by every page refreshing after one event, a 5xx while the app restarts, a timeout or
    a network error), the sign-in isn't over: the sealed token stays, nothing reloads, and the backoff's next try goes through."""
    for failure in ('({ status: 429 })', '({ status: 503 })', '({ status: 502, body: "<html>" })', '({ body: { nothing: 1 } })',
                    '(() => { throw new Error("TimeoutError"); })()'):
        got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
let keys = 0;
serve = async (r) => {
  if (r.url === "/api/chat/formkey") { keys += 1; return keys === 1 ? __FAILURE__ : { body: { formkey: "f2", mxid: "@ruqqus_1:s" } }; }
  return r.body.formkey === "f2" ? renewAnswer("tok-2") : { status: 401 };
};
await call(k, "tok-UQNEW0000000");
report({ log, reloads, reason: k.reason, formkey: session.boot.formkey, plain: await opened(mine()), sleeps, shown });
""".replace("__FAILURE__", failure))
        urls = [r["url"] for r in got["sent"]]
        assert urls == ["/api/chat/token", "/api/chat/formkey", "/api/chat/token", "/api/chat/formkey", "/api/chat/token"], failure
        assert got["reason"] is None and got["reloads"] == 0 and got["shown"] == [], failure
        assert got["log"] == ["set tok-2"] and got["plain"] == "tok-2" and got["formkey"] == "f2", failure
        assert len(got["sleeps"]) == 1 and not got["sleeps"][0]["locked"], failure          # one backoff, holding no lock

    # never answering: given up as transient, the sealed token still there and nothing reloaded
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async (r) => (r.url === "/api/chat/formkey" ? { status: 429 } : { status: 401 });
const res = await attempt(call(k, "tok-UQNEW0000000"));
report({ res, reloads, reason: k.reason, entry: mine(), plain: await opened(mine()) });
""")
    assert got["res"]["message"] == "chat offline"            # the SDK's ConnectionError: sync backs off, no log-out
    assert got["reason"] is None and got["reloads"] == 0
    assert got["plain"] == "tok-UQNEW0000000" and got["entry"]["gen"] == 1 and got["entry"]["s"] == "in"


@node
def test_a_token_renewed_under_a_minute_ago_and_refused_again_is_never_renewed_again():
    got = keeper("""
const session = await started();
const first = unknown();
const { k, client } = keeperFor(session, { fetch: async () => first });
serve = async () => renewAnswer("tok-2");
await call(k, "tok-UQNEW0000000");
const before = sent.length;
const again = await call(k, "tok-2");
const direct = await k.renew("tok-2");
report({ more: sent.length - before, same: again === first, reason: k.reason, direct, log });
""")
    assert got["more"] == 0 and got["same"] and got["reason"] == "unusable" and got["direct"] == "unusable"
    assert got["log"] == ["set tok-2"]


@node
def test_the_timer_renews_a_token_it_renewed_under_a_minute_ago_and_a_refusal_of_that_token_still_isnt_renewed():
    # tokens of 90 s (the specs' CHAT_TOKEN_REPORT_SECONDS) are renewed 30 to 60 s after they came: the guard against the SDK's retry
    # loop (FRESH_MS) is for a token the chat server refused, never for the timer, which once stopped the page and reloaded it instead
    got = keeper("""
const session = await started({ extra: { expires_in_ms: 90000 } });
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
let n = 1;
serve = async () => { n += 1; return renewAnswer("tok-" + n, { expires_in_ms: 90000 }); };
await k.renewSoon(true);
clock += 30 * 1000;
await k.renewSoon(true);
const timed = { token: client.token, reason: k.reason, log: [...log] };
const asks = renewals().length;
const again = await call(k, client.token);
report({ timed, asks, after: renewals().length, reason: k.reason, refused: again.status });
""")
    assert got["timed"] == {"token": "tok-3", "reason": None, "log": ["set tok-2", "set tok-3"]}
    assert got["asks"] == 2 and got["after"] == 2 and got["reason"] == "unusable" and got["refused"] == 401


@node
def test_a_log_out_while_the_renewal_is_out_drops_its_token():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
const before = mine().sealed;
serve = async () => {
  const idx = getIndex();                      // all_js.js leaveChatSessions ran meanwhile (step 4d): epoch up, everything out
  idx.epoch += 1;
  idx.owner = null;
  for (const e of idx.entries) { e.s = "out"; delete e.sealed; e.gen += 1; }
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return renewAnswer("tok-2");
};
await call(k, "tok-UQNEW0000000");
report({ log, reason: k.reason, entry: mine(), token: client.token });
""")
    assert got["reason"] == "signed_out" and got["log"] == [] and got["token"] == "tok-" + NEW
    assert "sealed" not in got["entry"] and got["entry"]["s"] == "out" and got["index"]["owner"] is None
    assert all("tok-2" not in value for value in got["stored"].values())


@node
def test_any_epoch_move_while_the_renewal_is_out_drops_its_token_even_with_the_entry_untouched():
    # a Log out and a sign-in back, or another account forgotten here (5.4): whatever moved `epoch`, an answer prepared before it is
    # never written
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => {
  const idx = getIndex();
  idx.epoch += 1;
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return renewAnswer("tok-2");
};
await call(k, "tok-UQNEW0000000");
report({ log, reason: k.reason, plain: await opened(mine()) });
""")
    assert got["reason"] == "signed_out" and got["log"] == [] and got["plain"] == "tok-" + NEW


@node
def test_a_log_out_that_ran_without_the_lock_while_the_renewal_awaited_an_unseal_is_never_undone():
    # another page's newer token won't open here, so the renewal unseals under the lock and then writes its own; a Log out that couldn't
    # wait for the lock (its 3 s ran out, or a browser with no Web Locks) runs exactly while that unseal is awaited
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
const realDecrypt = crypto.subtle.decrypt.bind(crypto.subtle);
let meanwhile = null;
crypto.subtle.decrypt = async (...args) => { const run = meanwhile; meanwhile = null; if (run) run(); return realDecrypt(...args); };
serve = async () => {
  const idx = getIndex();
  const e = idx.entries.find((x) => x.d === "UQNEW0000000");
  e.gen += 1;
  e.sealed = await boxFor("@ruqqus_1:s", "UQOTHER00000", "chat", "not-for-this-device");
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  meanwhile = () => {                             // all_js.js leaveChatSessions
    const now = getIndex();
    now.epoch += 1;
    now.owner = null;
    for (const x of now.entries) { x.s = "out"; delete x.sealed; x.gen += 1; }
    store.set("uqqus_chat_devices", JSON.stringify(now));
  };
  return renewAnswer("tok-2");
};
await call(k, "tok-UQNEW0000000");
report({ log, reason: k.reason, entry: mine(), token: client.token });
""")
    assert got["reason"] == "signed_out" and got["log"] == [] and got["token"] == "tok-" + NEW
    assert got["index"]["owner"] is None and got["entry"]["s"] == "out" and "sealed" not in got["entry"]
    assert all("tok-2" not in value for value in got["stored"].values())


@node
def test_a_newer_token_written_while_ours_was_out_is_taken_and_ours_dropped():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => {
  const idx = getIndex();
  const e = idx.entries.find((x) => x.d === "UQNEW0000000");
  e.gen += 1;
  e.sealed = await boxFor("@ruqqus_1:s", "UQNEW0000000", "chat", "tok-theirs");
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return renewAnswer("tok-ours");
};
await call(k, "tok-UQNEW0000000");
report({ log, plain: await opened(mine()) });
""")
    assert got["log"] == ["set tok-theirs"] and got["plain"] == "tok-theirs"


@node
def test_no_index_lock_is_held_across_the_site_or_a_backoff_and_a_server_down_is_a_connection_error():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => ({ status: 503, body: { retry: true } });
let thrown = null;
try { await call(k, "tok-UQNEW0000000"); } catch (e) { thrown = e.constructor.name; }
report({ thrown, sleeps, entry: mine() });
""")
    asks = [r for r in got["sent"] if r["url"] == "/api/chat/token"]
    assert got["thrown"] == "TypeError"                           # the SDK's ConnectionError: sync backs off, nobody is logged out
    assert [s["ms"] for s in got["sleeps"]] == [2000, 5000, 15000, 30000, 60000] and len(asks) == 6   # about two minutes, then it gives up
    assert not any(r["locked"] or r["lockedAfter"] for r in asks) and not any(s["locked"] for s in got["sleeps"])
    assert got["entry"]["s"] == "in" and "sealed" in got["entry"]


@node
def test_a_refusal_keeps_everything():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async () => ({ status: 403, body: { need_password: true, error: "Your password first." } });
await call(k, "tok-UQNEW0000000");
report({ reason: k.reason, refusal: k.refusal, entry: mine() });
""")
    assert got["reason"] == "refused" and got["refusal"]["need_password"] and got["entry"]["s"] == "in" and "sealed" in got["entry"]
    assert got["deleted"] == []


@node
def test_the_renewal_timer_runs_only_when_the_token_has_an_end():
    got = keeper("""
const delays = [];
const never = await started();
globalThis.setTimeout = (fn, ms) => { delays.push(ms); return { unref() {} }; };
keeperFor(never);
const none = delays.length;
globalThis.setTimeout = realSetTimeout;
const ending = await started({ extra: { expires_in_ms: 3600000 } });
clock = Date.now();
globalThis.setTimeout = (fn, ms) => { delays.push(ms); return { unref() {} }; };
keeperFor(ending);
globalThis.setTimeout = realSetTimeout;
report({ none, delays, exp: ending.exp, clock });
""")
    assert got["none"] == 0
    [delay] = got["delays"]
    expected = got["exp"] - 10 * 60 * 1000 - got["clock"]           # ten minutes before the end (a third of the hour is longer)
    assert abs(delay - expected) < 2000 and delay > 30 * 1000


@node
def test_the_timer_waits_for_a_third_of_a_short_life_jitters_up_to_a_minute_and_never_fires_sooner_than_30_seconds():
    got = keeper("""
const delays = {};
async function delayFor(life, random) {
  const session = await started({ extra: { expires_in_ms: life } });
  clock = Date.now();
  const seen = [];
  globalThis.setTimeout = (fn, ms) => { seen.push(ms); return { unref() {} }; };
  const k = new K.SessionKeeper(session, { ui, role: "top", now: () => clock, random: () => random });
  k.attach(fakeClient(session.accessToken));
  globalThis.setTimeout = realSetTimeout;
  return { delay: seen[0], left: session.exp - clock };
}
delays.short = await delayFor(90000, 0);          // the specs' 90 s token: renewed 30 s before its end, a third of its life
delays.floor = await delayFor(40000, 0);          // a third is 13 s before the end, 27 s from now: held to 30 s
delays.jitter = await delayFor(3600000, 1);       // up to a minute earlier, so the tabs of one device don't all ask at once
report({ delays });
""")
    short, floor, jitter = got["delays"]["short"], got["delays"]["floor"], got["delays"]["jitter"]
    assert abs(short["delay"] - (short["left"] - 30 * 1000)) < 2000 and short["delay"] > 50 * 1000
    assert floor["delay"] == 30 * 1000
    assert abs(jitter["delay"] - (jitter["left"] - 10 * 60 * 1000 - 60 * 1000)) < 2000


@node
def test_a_page_coming_back_looks_again_but_only_renews_a_token_near_its_end():
    # timers are throttled in hidden tabs and frozen in sleep: the tab becoming visible, the network coming back and a page restored from
    # the back-forward cache each look; a hidden tab, an ordinary pageshow and a token with time left ask nothing
    got = keeper("""
const docListeners = {};
globalThis.document = { visibilityState: "hidden", addEventListener: (n, fn) => { (docListeners[n] = docListeners[n] || []).push(fn); } };
const session = await started({ extra: { expires_in_ms: 3600000 } });
clock = Date.now();
const { k, client } = keeperFor(session);
let n = 1;
serve = async () => { n += 1; return renewAnswer("tok-" + n, { expires_in_ms: 3600000 }); };
const fire = async (target, name, event = {}) => { for (const fn of target[name] || []) fn(event); await tick(30); };
const counts = {};
await fire(listeners, "online");
document.visibilityState = "visible";
await fire(docListeners, "visibilitychange");
await fire(listeners, "pageshow", { persisted: true });
counts.early = renewals().length;
clock = k.exp - 5 * 60 * 1000;
document.visibilityState = "hidden";
await fire(docListeners, "visibilitychange");
await fire(listeners, "pageshow", { persisted: false });
counts.ignored = renewals().length;
await fire(listeners, "online");
counts.online = renewals().length;
clock = k.exp - 5 * 60 * 1000;
document.visibilityState = "visible";
await fire(docListeners, "visibilitychange");
counts.visible = renewals().length;
clock = k.exp - 5 * 60 * 1000;
await fire(listeners, "pageshow", { persisted: true });
counts.pageshow = renewals().length;
report({ counts, token: client.token, reason: k.reason });
""")
    assert got["counts"] == {"early": 0, "ignored": 0, "online": 1, "visible": 2, "pageshow": 3}
    assert got["token"] == "tok-4" and got["reason"] is None


@node
def test_a_timed_renewal_nobody_answers_keeps_trying_until_the_token_ends_then_looks_again_every_minute():
    got = keeper("""
const session = await started({ extra: { expires_in_ms: 3600000 } });
clock = Date.now();
const { k, client } = keeperFor(session);
clock = k.exp - 10 * 60 * 1000;
serve = async () => ({ status: 503, body: {} });
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return { unref() {} }; };
await k.renewSoon(true);
globalThis.setTimeout = realSetTimeout;
const waited = sleeps.reduce((sum, s) => sum + s.ms, 0);
const tries = renewals().length;
const stopped = log.includes("stop");
serve = async () => renewAnswer("tok-back", { expires_in_ms: 3600000 });
const minute = timers.filter((t) => t.ms === 60 * 1000);
await minute[minute.length - 1].fn();
await tick(30);
report({ waited, tries, stopped, minutes: minute.length, reason: k.reason, token: client.token, locked: sleeps.some((s) => s.locked) });
""")
    # a renewal a refusal asked for gives up after about 2 minutes; the timer's keeps going while the token lasts (10 minutes here)
    assert got["waited"] > 2 * 60 * 1000 and got["waited"] <= 10 * 60 * 1000 and got["tries"] > 6
    assert not got["stopped"] and got["reason"] is None and not got["locked"]
    assert got["minutes"] == 1 and got["token"] == "tok-back"


@node
def test_a_timed_renewal_meeting_a_stale_key_whose_refresh_fails_for_now_keeps_the_token_and_the_page():
    """Step 5's timer is often an idle page's first POST after a credential event spared this sign-in, so it is the one that meets the
    stale key: a 429, a 5xx or a timeout on the form key's GET is the network's, never a Log out. The sealed token stays, nothing stops
    or reloads (a call window keeps its call), and the next try goes through with the new key."""
    for failure in ('({ status: 429 })', '({ status: 503 })', '(() => { throw new Error("TimeoutError"); })()'):
        got = keeper("""
const session = await started({ extra: { expires_in_ms: 3600000 } });
clock = Date.now();
const { k, client } = keeperFor(session);
const lost = [];
k.onLost = (why) => lost.push(why);
clock = k.exp - 10 * 60 * 1000;
let keys = 0;
serve = async (r) => {
  if (r.url === "/api/chat/formkey") { keys += 1; return keys === 1 ? __FAILURE__ : { body: { formkey: "f2", mxid: "@ruqqus_1:s" } }; }
  return r.body.formkey === "f2" ? renewAnswer("tok-2", { expires_in_ms: 3600000 }) : { status: 401 };
};
await k.renewSoon(true);
report({ lost, reloads, reason: k.reason, stopped: log.includes("stop"), token: client.token, plain: await opened(mine()), shown });
""".replace("__FAILURE__", failure))
        assert got["lost"] == [] and got["reloads"] == 0 and got["reason"] is None and not got["stopped"], failure
        assert got["token"] == "tok-2" and got["plain"] == "tok-2" and got["shown"] == [], failure


@node
def test_a_timed_renewal_that_finds_the_device_gone_or_the_sign_in_over_stops_the_client_and_says_so():
    got = keeper("""
const outcomes = {};
for (const how of ["removed", "signed_out"]) {
  const session = await started({ extra: { expires_in_ms: 3600000 } });
  const { k } = keeperFor(session);
  const lost = [];
  k.onLost = (why) => lost.push(why);
  log.length = 0;
  if (how === "removed") {
    serve = async () => ({ status: 409, body: { device_gone: true, reason: "signed_out_here" } });
  } else {
    const idx = getIndex();                          // another tab's Log out, whose storage event this page hasn't seen yet
    idx.owner = null;
    for (const e of idx.entries) { e.s = "out"; delete e.sealed; }
    store.set("uqqus_chat_devices", JSON.stringify(idx));
  }
  await k.renewSoon(true);
  outcomes[how] = { reason: k.reason, lost, stopped: log.includes("stop") };
  setIndex(null, [], getIndex().epoch + 1);
}
report({ outcomes });
""")
    assert got["outcomes"]["removed"] == {"reason": "removed", "lost": ["removed"], "stopped": True}
    assert got["outcomes"]["signed_out"] == {"reason": "signed_out", "lost": ["signed_out"], "stopped": True}


@node
def test_a_rotation_by_another_tab_is_taken_without_a_reload():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session);
const idx = getIndex();
const e = idx.entries.find((x) => x.d === "UQNEW0000000");
e.gen += 1;
e.sealed = await boxFor("@ruqqus_1:s", "UQNEW0000000", "chat", "tok-rotated");
store.set("uqqus_chat_devices", JSON.stringify(idx));
for (const fn of listeners.storage) fn({ key: "uqqus_chat_devices" });
await tick();
report({ log, reloads });
""")
    assert got["log"] == ["set tok-rotated"] and got["reloads"] == 0


@node
def test_a_log_out_a_deletion_another_account_or_a_replaced_device_stops_this_page():
    for change, why in (
        ('e.s = "out"; delete e.sealed; idx.owner = null;', "logged_out"),
        ('e.s = "forget"; delete e.sealed;', "forgotten"),
        ('idx.owner = "@ruqqus_2:s";', "other_account"),
        ('e.s = "removed"; delete e.sealed;', "removed"),
        ("idx.entries = [];", "forgotten"),
    ):
        got = keeper("""
const session = await started();
const { k, client } = keeperFor(session);
const idx = getIndex();
const e = idx.entries.find((x) => x.d === "UQNEW0000000");
e.gen += 1;
""" + change + """
store.set("uqqus_chat_devices", JSON.stringify(idx));
for (const fn of listeners.storage) fn({ key: "uqqus_chat_devices" });
await tick();
report({ log, reloads, budget: JSON.parse(tab.get("uqqus_chat_reloads:top") || "[]").length });
""")
        assert got["log"] == ["stop"] and got["reloads"] == 1 and got["budget"] == 1, why
        assert got["deleted"] == [], why


@node
def test_reloads_are_budgeted_per_role_and_quiet_frames_tell_their_parent():
    got = keeper("""
const top = [K.reloadOnce("x", "top"), K.reloadOnce("x", "top"), K.reloadOnce("x", "top"), K.reloadOnce("x", "single")];
const reloadsTop = reloads;
asFrame("?embed=1");
const panelRole = K.frameRole();
const panel = K.reloadOnce("removed", panelRole);
asFrame("?embed=1&share=1");
const shareRole = K.frameRole();
asFrame("?embed=1&single=1");
const singleRole = K.frameRole();
report({ top, reloadsTop, panelRole, panel, shareRole, singleRole, posted, reloads });
""")
    assert got["top"] == ["reload", "reload", "over", "reload"] and got["reloadsTop"] == 3
    assert got["panelRole"] == "panel" and got["shareRole"] == "share" and got["singleRole"] == "single"
    assert got["panel"] == "posted" and got["reloads"] == 3
    assert got["posted"] == [{"type": "uqqus-chat:stopped", "why": "removed", "text": "", "origin": "https://site"}]


@node
def test_over_the_budget_a_top_or_guild_tab_page_says_chat_stopped_and_a_panel_frame_tells_its_parent():
    got = keeper("""
const session = await started();
tab.set("uqqus_chat_reloads:single", JSON.stringify([Date.now(), Date.now()]));
asFrame("?embed=1&single=1");
const single = keeperFor(session, { role: "single" }).k;
await single.end("removed");
const singleShown = shown.splice(0);
const singlePosted = posted.splice(0);
asFrame("?embed=1");
const panel = keeperFor(session, { role: "panel" }).k;
await panel.end("removed", { notice: "a line" });
report({ singleShown, singlePosted, shown, posted, reloads });
""")
    assert got["singleShown"] == [["stop", "Chat stopped in this tab.", "Reload"]]
    assert got["singlePosted"][0]["type"] == "uqqus-chat:stopped"     # its page makes it again when the member comes back
    assert got["shown"] == [] and got["reloads"] == 0
    assert [p["type"] for p in got["posted"]] == ["uqqus-chat:notice", "uqqus-chat:stopped"] and got["posted"][0]["text"] == "a line"


@node
def test_a_page_that_stops_lets_its_store_go_only_after_the_opening_settled_and_the_client_stopped():
    got = keeper("""
const session = await started();
const { k, client } = keeperFor(session);
const held = await k.holdStore();
let finish;
const pending = new Promise((resolve) => { finish = resolve; });
k.opening(() => pending.then(() => log.push("opened")));
const k2 = new K.SessionKeeper(session, { ui, role: "top" });
const second = k2.holdStore({ slowMs: 20 }).then((got) => log.push("second has it " + got));
const closing = k.shutdown().then(() => log.push("first is done"));
await tick(60);
log.push("still opening");
finish();
await closing;
await second;
report({ held, log, lockLog: lockLog.filter((l) => l.includes("uqqus-store:")), shown });
""")
    assert got["held"] is True
    assert got["log"] == ["still opening", "opened", "stop", "first is done", "second has it true"]
    store_lock = "uqqus-store:" + prefix(A, NEW)
    assert got["lockLog"] == ["take " + store_lock, "drop " + store_lock, "take " + store_lock]
    assert ["status", "Chat is closing in another tab…"] in got["shown"]



@node
def test_a_page_that_stops_while_its_keys_are_made_or_reset_stops_the_client_only_after_that_work_and_then_lets_the_store_go():
    """Start over (or a first set-up) is changing the account's keys on the server when another tab takes chat over: stopping the client
    then would close the OlmMachine those calls use. The keeper waits for the work (holdFor), then stops the client, then the store goes."""
    got = keeper("""
const session = await started();
const { k } = keeperFor(session);
await k.holdStore();
let finish;
const work = new Promise((resolve) => { finish = resolve; }).then(() => { log.push("keys made"); return "KEY"; });
const answered = k.holdFor(work);
const closing = k.shutdown().then(() => log.push("first is done"));
await tick(60);
log.push("still making");
finish();
await closing;
report({ log, same: answered === work, stores: lockLog.filter((l) => l.includes("uqqus-store:")).map((l) => l.split(" ")[0]) });
""")
    assert got["log"] == ["still making", "keys made", "stop", "first is done"] and got["same"] is True
    assert got["stores"] == ["take", "drop"]

TWO_TABS = """
const session = await started();
let k1 = null, k2 = null;
const chatLocks = () => lockLog.filter((l) => l.includes("uqqus-store:") || l.includes("uqqus-matrix")).map((l) => l.split(" ").slice(0, 2).join(" ").replace(/uqqus-store:.*/, "store"));
await K.holdChatLock({ keeper: () => k1, lost: () => log.push("1 lost") });
k1 = keeperFor(session).k;
"""


@node
def test_a_tab_that_loses_chat_while_its_store_is_opening_stops_in_order_before_the_next_tab_opens_it():
    # holdChatLock itself, its uqqus-matrix taken over (steal) while the first tab's initRustCrypto is still pending (6.4)
    got = keeper(TWO_TABS + """
let finish;
const pending = new Promise((resolve) => { finish = resolve; });
const first = k1.openStore(() => { log.push("1 open starts"); return pending.then(() => log.push("1 opened")); },
                           { prepare: async () => log.push("1 wasm") });
await tick();
await K.holdChatLock({ keeper: () => k2, lost: () => log.push("2 lost") });
k2 = keeperFor(session).k;
const second = k2.openStore(async () => log.push("2 open starts"), { prepare: async () => log.push("2 wasm") });
await tick(40);
log.push("still opening");
finish();
const r1 = await first;
const r2 = await second;
report({ log, r1, r2, stopped: [k1.stopped, k2.stopped], locks: chatLocks() });
""")
    # the opening settles, then the client stops (closing its OlmMachine), then the store lock goes; only then does the new tab open
    assert got["log"] == ["1 wasm", "1 open starts", "1 lost", "still opening", "1 opened", "stop", "2 wasm", "2 open starts"]
    assert got["r2"] is True and got["stopped"] == [True, False]
    assert got["locks"] == ["take uqqus-matrix", "take store", "steal uqqus-matrix", "drop store", "take store"]


@node
def test_a_tab_that_loses_chat_while_the_wasm_loads_never_opens_its_store():
    # taken over between the store lock and the opening (initCryptoWasm): shutdown let the store lock go with nothing opening, so
    # this tab must not start initRustCrypto on a store the new tab may be opening
    got = keeper(TWO_TABS + """
let loaded;
const wasm = new Promise((resolve) => { loaded = resolve; });
const first = k1.openStore(async () => log.push("1 open starts"), { prepare: () => wasm });
await tick();
await K.holdChatLock({ keeper: () => k2, lost: () => log.push("2 lost") });
k2 = keeperFor(session).k;
const second = k2.openStore(async () => log.push("2 open starts"), { prepare: async () => log.push("2 wasm") });
await tick(40);
loaded();
const r1 = await first;
const r2 = await second;
await tick();
report({ log, r1, r2, again: await k1.opening(() => log.push("never")) });
""")
    assert got["log"] == ["1 lost", "stop", "2 wasm", "2 open starts"]
    assert got["r1"] is False and got["r2"] is True and got["again"] is False


@node
def test_a_store_lock_never_freed_gives_up_and_lets_go_when_it_comes():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session);
await k.holdStore();
const k2 = new K.SessionKeeper(session, { ui, role: "top" });
const waited = await attempt(k2.holdStore({ slowMs: 5, giveUpMs: 20 }));
k.releaseStore();
await tick();
report({ waited, lockLog: lockLog.filter((l) => l.includes("uqqus-store:")), busy: [...busy] });
""")
    assert got["waited"]["thrown"] == "StoreFailed"
    assert len(got["lockLog"]) == 4 and got["busy"] == []           # granted late and let go at once


@node
def test_a_page_stopped_while_it_waits_for_the_store_lock_draws_nothing_over_what_it_said():
    """Tab A waits for the store lock (the tab it took chat from is still opening it); tab B takes chat from A meanwhile: A has said
    "Chat is open in another tab." (index.js), and neither the 3 s "closing" line nor the 30 s failure may replace it. The wait ends at
    once, quietly, and the lock, when it comes, is let go."""
    got = keeper("""
const session = await started();
const { k } = keeperFor(session);
await k.holdStore();
const quick = (keeper) => { keeper.holdStore = () => K.SessionKeeper.prototype.holdStore.call(keeper, { slowMs: 30, giveUpMs: 60 }); return keeper; };
const k2 = quick(new K.SessionKeeper(session, { ui, role: "top" }));
shown.length = 0;
let endedSoon = false;
const waiting = attempt(k2.openStore(async () => log.push("opened"), { prepare: async () => log.push("wasm") }));
waiting.then(() => { endedSoon = true; });
await tick(5);
await k2.shutdown();
await tick(1);
const soon = endedSoon;                            // at once, not when a timer or the lock comes
const ended = await waiting;
await tick(90);                                    // past where both timers would have fired
const late = quick(new K.SessionKeeper(session, { ui, role: "top" }));
const lateWait = late.holdStore();
late.stopped = true;                               // stopped some other way: the timers still say nothing, and it ends as not held
const lateEnded = await attempt(lateWait);         // after the 60 ms
k.releaseStore();
await tick();
report({ soon, ended, lateEnded, log, shown, busy: [...busy] });
""")
    assert got["soon"] is True and got["ended"] == {"value": False} and got["lateEnded"] == {"value": False}
    assert got["log"] == [] and got["shown"] == []
    assert got["busy"] == []                       # granted late, and let go at once


@node
def test_a_store_that_wont_unpickle_is_given_up_once_never_deleted_and_says_why_to_the_next_device():
    got = keeper("""
const session = await started();
const client = { initRustCrypto: async () => { throw new Error("The pickle failed to be decrypted while unpickling"); }, stopClient() {} };
const first = await attempt(T.openCryptoStore(client, session));
const afterFirst = { entry: mine(), replace: sent.filter((r) => r.body && r.body.mode === "replace").map((r) => r.body) };
// the page started again (keeper.opening reloads once): the next start names no device and is minted a new one, told why once
sent.length = 0;
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEXT000000") });
const next = await T.ensureSession(BOOT, "chat");
// the same store refusing again in this tab is shown, never another replacement
const again = await attempt(T.openCryptoStore(client, { ...session, storeKey: new Uint8Array(32) }));
report({ first, afterFirst, nextWhy: next.why, nextAsk: sent[0].body, again, retried: [...tab.keys()] });
""")
    assert got["first"]["thrown"] == "StoreReplaced"
    entry = got["afterFirst"]["entry"]
    assert entry["s"] == "broken" and entry["bad"] > 0 and "sealed" not in entry
    assert got["afterFirst"]["replace"] == [{"formkey": "f", "kind": "chat", "mode": "replace", "device_id": NEW, "why": "store_unopenable"}]
    assert "device_id" not in got["nextAsk"] and got["nextWhy"] == "store_unopenable"
    assert [e for e in got["index"]["entries"] if e["d"] == NEW][0]["why"] is None        # told once
    assert got["again"]["thrown"] == "StoreFailed" and len([r for r in got["sent"] if (r["body"] or {}).get("mode") == "replace"]) == 0
    assert got["deleted"] == [] and "uqqus_store_retry:" + prefix(A, NEW) in got["retried"]


@node
def test_any_other_store_failure_is_shown_and_changes_nothing():
    got = keeper("""
const session = await started();
const client = { initRustCrypto: async () => { throw new Error("QuotaExceededError"); } };
const keyView = session.storeKey;
const failed = await attempt(T.openCryptoStore(client, session));
report({ failed, entry: mine(), zeroed: keyView.every((b) => b === 0) });
""")
    assert got["failed"]["thrown"] == "StoreFailed" and got["failed"]["message"] == "Chat couldn't start in this browser."
    assert got["entry"]["s"] == "in" and got["sent"] == [] and got["zeroed"]


@node
def test_a_store_that_isnt_this_devices_own_is_closed_and_given_up():
    got = keeper("""
const session = await started();
await T.markEntry(session.entry, { ed: "ED-THE-DEVICE" });
session.entry.ed = "ED-THE-DEVICE";
const calls = [];
const client = {
  initRustCrypto: async () => calls.push("open"),
  getCrypto: () => ({ getOwnDeviceKeys: async () => ({ ed25519: "ED-SOMEONE-ELSE" }) }),
  stopClient: () => calls.push("stop"),
};
const got = await attempt(T.openCryptoStore(client, session));
report({ got, calls, entry: mine() });
""")
    assert got["got"]["thrown"] == "StoreReplaced" and got["calls"] == ["open", "stop"] and got["entry"]["s"] == "broken"
    assert got["deleted"] == []


@node
def test_a_store_given_up_starts_the_page_again_once():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session);
await k.holdStore();
k.opening(() => Promise.reject(new T.StoreReplaced("store_unopenable")));
await tick(30);
report({ reloads, log, busy: [...busy] });
""")
    assert got["reloads"] == 1 and got["log"] == ["stop"] and got["busy"] == []


@node
def test_what_a_page_shows_when_chat_cant_start():
    got = keeper("""
const results = {};
let tries = 0;
const pressing = { ...ui, stop: (t, o = {}) => { shown.push(["stop", t, o.action || null]); if (o.onAction) o.onAction(); } };
results.unavailable = await K.startSession(async () => {
  tries += 1;
  if (tries <= 5) throw new T.ChatUnavailable("x", { retry: true });
  return { ok: true };
}, pressing, { role: "top", sleep: async (ms) => sleeps.push({ ms }) });
results.unavailableShown = shown.splice(0);
results.storage = await K.startSession(async () => { throw new T.StorageUnavailable("No storage here."); }, ui, { role: "top" });
results.refused = await K.startSession(async () => { throw new T.ChatRefused("Chat is locked."); }, ui, { role: "top" });
results.fixedShown = shown.splice(0);
let password = null;
results.password = await K.startSession(async () => { throw new T.PasswordNeeded("Password first."); }, ui, { role: "top", onPassword: (e) => { password = e.message; } });
await K.startSession(async () => { throw new T.PasswordNeeded("Password first."); }, ui, { role: "top" });
results.passwordReloads = reloads;
for (let i = 0; i < 3; i += 1) await K.startSession(async () => { throw new T.SignedOut("gone"); }, ui, { role: "top" });
results.signedOutShown = shown.splice(0);
asFrame("?embed=1");
results.panel = await K.startSession(async () => { throw new T.ChatRefused("Chat is locked."); }, ui, { role: "panel" });
report({ results, sleeps, password, reloads, posted, shown, tries });
""")
    r = got["results"]
    assert r["unavailable"] == {"ok": True} and got["tries"] == 6
    assert [s["ms"] for s in got["sleeps"]] == [2000, 5000, 15000, 30000]
    assert ["stop", "Chat couldn't connect.", "Retry"] in r["unavailableShown"] and ["status", "Connecting…"] in r["unavailableShown"]
    assert r["storage"] is None and r["refused"] is None
    assert r["fixedShown"] == [["stop", "No storage here.", None], ["stop", "Chat is locked.", None]]
    assert got["password"] == "Password first." and r["passwordReloads"] == 1
    assert r["signedOutShown"] == [["stop", "Chat stopped in this tab.", "Reload"]] and got["reloads"] == 3
    assert r["panel"] is None and got["shown"] == []
    assert got["posted"] == [{"type": "uqqus-chat:stopped", "why": "refused", "text": "Chat is locked.", "origin": "https://site"}]


@node
def test_what_the_page_does_when_the_sdk_gives_up_on_its_token():
    got = keeper("""
const results = {};
async function lose(why, setup) {
  const session = await started();
  tab.clear();
  const { k, client } = keeperFor(session);
  if (setup) setup(k);
  SO.watchSignedOut(client, k);
  shown.length = 0; log.length = 0;
  const before = reloads;
  k.reason = why;
  await client.handlers[SO.SESSION_LOGGED_OUT]();
  await tick(1700);                              // a notice waits a moment before the reload
  results[why + (setup ? "+" : "")] = { shown: shown.slice(), log: log.slice(), reloads: reloads - before };
}
await lose("renewed");
await lose("unusable");
await lose("removed");
await lose("signed_out");
await lose("refused", (k) => { k.refusal = { need_password: true, error: "Password first." }; });
await lose("refused", null);
// a 401 that bypassed fetchFn: the keeper is asked, and a device the site says is gone starts again
const session = await started();
tab.clear();
const { k, client } = keeperFor(session);
SO.watchSignedOut(client, k);
serve = async () => ({ status: 409, body: { device_gone: true, reason: "others" } });
const before = reloads;
log.length = 0;
await client.handlers[SO.SESSION_LOGGED_OUT]();
await client.handlers[SO.SESSION_LOGGED_OUT]();  // heard once
await tick(1700);
results.bypassed = { log: log.slice(), reloads: reloads - before, entry: mine(), asked: renewals().length };
report({ results });
""")
    r = got["results"]
    for why in ("renewed", "unusable", "signed_out"):
        assert r[why]["reloads"] == 1 and r[why]["log"][0] == "stop" and r[why]["shown"] == [], why
    assert r["removed"]["reloads"] == 1
    assert r["removed"]["shown"] == [["notice", "Chat was signed out on this browser from another device. Starting again here…"]]
    assert r["refused+"]["reloads"] == 0 and r["refused+"]["shown"] == [["stop", "Password first.", "Reload"]]
    assert r["refused"]["reloads"] == 0 and r["refused"]["shown"][0][0] == "stop" and r["refused"]["shown"][0][2] is None
    assert r["bypassed"]["reloads"] == 1 and r["bypassed"]["entry"]["s"] == "removed" and r["bypassed"]["entry"]["why"] == "others"
    assert r["bypassed"]["asked"] == 1 and r["bypassed"]["log"].count("stop") == 2      # heard once: one renewal, one notice
    assert got["deleted"] == []


@node
def test_a_lost_connection_shows_a_banner_and_starts_again_once_the_site_answers():
    got = keeper("""
const session = await started();
let press = null;
const offline = { ...ui, banner: (t, o = {}) => { shown.push(["banner", t, o.action || null]); if (o.onAction) press = o.onAction; } };
const { k, client } = keeperFor(session, { ui: offline });
SO.watchSignedOut(client, k);
serve = async () => ({ status: 503, body: { retry: true } });
k.reason = "transient";
await client.handlers[SO.SESSION_LOGGED_OUT]();
const banner = shown.slice();
const offlineReloads = reloads;
serve = async () => renewAnswer("tok-2");
press();                                         // Retry on the banner: the renewal works, the page starts again
await tick(30);
report({ banner, offlineReloads, reloads, log, last: shown[shown.length - 1] });
""")
    assert got["banner"] == [["banner", "Chat lost its connection. Retrying…", "Retry"]] and got["offlineReloads"] == 0
    assert got["reloads"] == 1 and "set tok-2" in got["log"] and got["last"] == ["banner", None, None]


@node
def test_every_reason_for_a_new_device_has_its_line_or_none():
    got = keeper("""
const reasons = ["others", "password", "reset", "mfa", "mfa_recovery", "child_password", "deleted_elsewhere", "signed_out_here",
                 "store_unopenable", "store_missing", "key_changed", "stale", "legacy", "mint_failed", "child_locked", "deleted", "taken_over", null];
report({ lines: Object.fromEntries(reasons.map((r) => [String(r), L.newDeviceLine(r)])) });
""")
    lines = got["lines"]
    for quiet in ("legacy", "mint_failed", "child_locked", "deleted", "taken_over", "null"):   # taken_over: a device no page ever used
        assert lines[quiet] is None, quiet
    said = [v for k, v in lines.items() if v]
    assert len(said) == 12 and all(line.endswith("starting again here.") for line in said)
    assert lines["deleted_elsewhere"] == lines["signed_out_here"]


def test_the_lines_are_marked_and_shown_once_above_the_unlock_box():
    from chat_node_harness import SRC
    lines = (SRC / "lines.js").read_text(encoding="utf-8")
    assert lines.count('return tr("') == 11
    crypto = (SRC / "crypto.js").read_text(encoding="utf-8")
    # shown once (and not at all by a page that stopped meanwhile: tests/test_chat_unlock_stops.py)
    assert "pendingLine = newDeviceLine(why);" in crypto and "if (pendingLine && notify && !gone()) notify(pendingLine);" in crypto
    assert "shown = { ...shown, text: [pendingLine].concat(options.text || []) };\n    pendingLine = null;" in crypto
    index = (SRC / "index.js").read_text(encoding="utf-8")
    # asked with the line the first time only; an Unlock asked again from the thread (send_gate.js) has nothing new to say
    assert "let first = { newCode, why, notify: keeper.ui.notice };" in index and "ensureEncryptionReady(client, boot, { notify: keeper.ui.notice, ...asked, stopped: chatGone, hold: (work) => keeper.holdFor(work),\n        wait: keysWaitLine, inSight," in index
    assert "const asked = first || {};\n      first = null;" in index
    assert "markEntry(session.entry, { why: null })" in index


def test_the_parents_hear_a_stopped_frame():
    from chat_node_harness import ROOT
    assets = ROOT / "ruqqus" / "assets" / "js"
    panels = (assets / "side_panels.js").read_text(encoding="utf-8")
    assert "event.data.type === 'uqqus-chat:stopped'" in panels and "stale.chat = true;" in panels
    assert "if (stale[name] && frames[name]) {" in panels                  # made again the next time it is shown
    share = (assets / "share_chat.js").read_text(encoding="utf-8")
    assert "frame.src = '/chat?embed=1&share=1';" in share and "data.type === 'uqqus-chat:stopped'" in share
    guild = (ROOT / "ruqqus" / "templates" / "guild" / "chat.html").read_text(encoding="utf-8")
    assert "event.data.type === 'uqqus-chat:stopped'" in guild and "event.source !== frame.contentWindow" in guild
    for text in (panels, share, guild):
        assert "event.origin !== window.location.origin" in text



# --- the raw store key (1.2): zeroed on every way a page stops before the store has it ------------------------------------------

@node
def test_the_store_key_is_zeroed_however_the_store_never_opens():
    got = keeper("""
const zeroed = (s) => s.storeKey.every((b) => b === 0);
const results = {};
// the wasm (prepare) fails
let s = await started();
let k = keeperFor(s).k;
results.wasm = { got: await attempt(k.openStore(async () => log.push("opened"), { prepare: async () => { throw new Error("wasm"); } })), zeroed: zeroed(s) };
k.releaseStore();
// taken over while the wasm loads: openStore answers false without opening
s = await started();
k = keeperFor(s).k;
results.taken = { got: await attempt(k.openStore(async () => log.push("opened"), { prepare: async () => { k.shutdown(); } })), zeroed: zeroed(s) };
// the store lock never came
s = await started();
const other = keeperFor(s).k;
await other.holdStore();
k = new K.SessionKeeper(s, { ui, role: "top" });
const realHold = k.holdStore.bind(k);
k.holdStore = () => realHold({ slowMs: 5, giveUpMs: 20 });
results.lock = { got: await attempt(k.openStore(async () => log.push("opened"))), zeroed: zeroed(s) };
other.releaseStore();
await tick();
// stopped before it ever asked for the store (another tab took over while the session was on its way)
s = await started();
k = keeperFor(s).k;
await k.shutdown();
results.shutdown = { zeroed: zeroed(s) };
report({ results, log });
""")
    r = got["results"]
    assert r["wasm"]["got"]["thrown"] == "Error" and r["wasm"]["zeroed"]
    assert r["taken"]["got"]["value"] is False and r["taken"]["zeroed"]
    assert r["lock"]["got"]["thrown"] == "StoreFailed" and r["lock"]["zeroed"]
    assert r["shutdown"]["zeroed"]
    assert "opened" not in got["log"]


@node
def test_the_store_key_is_handed_to_the_store_before_anything_zeroes_it():
    got = keeper("""
const s = await started();
const { k } = keeperFor(s);
let seen = null;
const done = await k.openStore(async () => { seen = Array.from(s.storeKey); });
report({ opened: done, seenWhole: seen.some((b) => b !== 0), zeroedAfter: s.storeKey.every((b) => b === 0) });
""")
    assert got["opened"] is True and got["seenWhole"] and got["zeroedAfter"]


# --- a stale answer from before a Log out and a sign-in back never undoes the fresh sign-in --------------------------------------

@node
def test_a_refusal_from_before_a_log_out_and_a_sign_in_back_leaves_the_fresh_token():
    for answer in ('({ status: 0, type: "opaqueredirect" })', '({ status: 409, body: { device_gone: true, reason: "password" } })'):
        got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
const fresh = await boxFor("@ruqqus_1:s", "UQNEW0000000", "chat", "tok-after-sign-in");
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { status: 0, type: "opaqueredirect" };
  // meanwhile: Log out (epoch up, everything out), then the same account signed back in here and took the device up again
  const idx = getIndex();
  idx.epoch += 1;
  const e = idx.entries.find((x) => x.d === "UQNEW0000000");
  Object.assign(e, { s: "in", sealed: fresh, gen: e.gen + 2 });
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return """ + answer + """;
};
await call(k, "tok-UQNEW0000000");
report({ entry: mine(), plain: await opened(mine()) });
""")
        assert got["entry"]["s"] == "in" and got["plain"] == "tok-after-sign-in", answer
        assert got["entry"].get("why") is None, answer


# --- a form key is only ever this page's own account's -----------------------------------------------------------------------------

@node
def test_a_renewal_after_another_account_signed_in_here_never_posts_as_that_account():
    got = keeper("""
const session = await started();
const { k } = keeperFor(session, { fetch: async () => unknown() });
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { body: { formkey: "theirs", mxid: "@ruqqus_2:s" } };
  return r.body.formkey === "theirs" ? { status: 409, body: { device_gone: true, reason: "deleted_elsewhere" } } : { status: 401 };
};
await call(k, "tok-UQNEW0000000");
report({ reason: k.reason, entry: mine(), formkey: session.boot.formkey, shown });
""")
    assert [r["url"] for r in got["sent"]] == ["/api/chat/token", "/api/chat/formkey"]       # never sent again with their key
    assert got["formkey"] == "f" and got["reason"] == "signed_out"
    assert got["entry"]["s"] == "in" and got["entry"].get("why") is None                # never marked removed


# --- a page that lost chat to another tab, and messages it held ------------------------------------------------------------------

@node
def test_a_start_taken_over_meanwhile_shows_nothing_and_never_reloads():
    got = keeper("""
const r = await K.startSession(async () => { throw new T.TakenOver("x"); }, ui, { role: "top" });
asFrame("?embed=1&share=1");
const share = await K.startSession(async () => { throw new T.TakenOver("x"); }, ui, { role: "share" });
report({ r, share, shown, posted, reloads });
""")
    assert got["r"] is None and got["share"] is None and got["shown"] == [] and got["posted"] == [] and got["reloads"] == 0


@node
def test_a_start_retrying_after_losing_chat_to_another_tab_leaves_that_tabs_note_and_asks_nothing_more():
    """Tab A's start gets a 503 while tab B takes chat over (index.js holdChatLock draws "Chat is open in another tab." with Use it
    here): A must not draw "Connecting…" over that note, wait, ask the site again, reload, or offer Retry."""
    got = keeper("""
let held = true, asked = 0;
const r = await K.startSession(async () => { asked += 1; held = false; throw new T.ChatUnavailable("x", { retry: true }); }, ui,
  { role: "top", sleep: async (ms) => sleeps.push({ ms }), stillMine: () => held });
const first = { r, asked, shown: shown.splice(0), sleeps: sleeps.splice(0) };
// a 503 after the retries ran out: Retry is offered, and the tab loses chat before it is pressed
held = true; asked = 0;
const pressing = { ...ui, stop: (t, o = {}) => { shown.push(["stop", t, o.action || null]); held = false; if (o.onAction) o.onAction(); } };
const r2 = await K.startSession(async () => { asked += 1; throw new T.ChatUnavailable("x", { retry: true }); }, pressing,
  { role: "top", sleep: async (ms) => sleeps.push({ ms }), stillMine: () => held });
// lost before a password reload or a signed-out reload: nothing reloads (a reload would take chat back from the other tab)
const r3 = await K.startSession(async () => { held = false; throw new T.SignedOut("gone"); }, ui, { role: "top", stillMine: () => held });
report({ first, r2, asked, shown, sleeps, reloads, r3 });
""")
    assert got["first"] == {"r": None, "asked": 1, "shown": [], "sleeps": []}
    assert got["r2"] is None and got["asked"] == 5 and len(got["sleeps"]) == 4
    assert ["status", "Connecting…"] in got["shown"] and got["shown"][-1] == ["stop", "Chat couldn't connect.", "Retry"]
    assert got["r3"] is None and got["reloads"] == 0


@node
def test_a_page_that_ends_with_messages_waiting_says_so_instead_of_reloading():
    got = keeper("""
const session = await started();
const top = keeperFor(session).k;
top.unsent = () => 2;
await top.end("removed", { notice: "a line" });
const topShown = shown.splice(0);
const topReloads = reloads;
asFrame("?embed=1&share=1");
const share = keeperFor(await started(), { role: "share" }).k;
share.unsent = () => 1;
await share.end("logged_out");
const none = keeperFor(await started(), { role: "top" }).k;
none.unsent = () => 0;
await none.end("logged_out");
report({ topShown, topReloads, posted, reloads });
""")
    assert got["topShown"] == [["notice", "a line"],
                               ["stop", "2 messages you wrote here weren't sent: chat stopped in this tab before they could go.", "Reload"]]
    assert got["topReloads"] == 0
    stopped = [p for p in got["posted"] if p["type"] == "uqqus-chat:stopped"]
    assert stopped[0]["text"] == "A message you wrote here wasn't sent: chat stopped in this tab before it could go."
    assert got["reloads"] == 1                     # with nothing waiting, a top page starts again as before


@node
def test_a_page_that_ends_while_new_keys_copies_are_being_saved_reloads_only_once_they_are():
    """crypto.js keysPending: the device was signed out elsewhere (or another tab logged out) while the member was at "Protect your
    messages" or "Save your recovery code" after new keys went on the server. A reload then would leave keys no copy opens, or a code
    nobody saw: the page stops its client at once, and reloads (or tells its parent) only once the copies are finished."""
    got = keeper("""
const session = await started();
const top = keeperFor(session).k;
let finish;
top.keysPending = () => new Promise((resolve) => { finish = resolve; });
const ended = top.end("removed").then(() => log.push("ended"));
await tick(60);
const stoppedFirst = top.stopped, reloadsBefore = reloads, logBefore = log.slice();
finish();
await ended;
asFrame("?embed=1");
const panel = keeperFor(await started(), { role: "panel" }).k;
let finishPanel;
panel.keysPending = () => new Promise((resolve) => { finishPanel = resolve; });
const panelEnded = panel.end("logged_out");
await tick(60);
const postedBefore = posted.filter((p) => p.type === "uqqus-chat:stopped").length;
finishPanel();
await panelEnded;
const none = keeperFor(await started(), { role: "top" }).k;
none.keysPending = () => null;
const reloadsMid = reloads;
await none.end("removed");
report({ stoppedFirst, reloadsBefore, logBefore, reloads, reloadsMid, postedBefore,
         postedAfter: posted.filter((p) => p.type === "uqqus-chat:stopped").length });
""")
    assert got["stoppedFirst"] is True and got["reloadsBefore"] == 0 and "ended" not in got["logBefore"]
    assert "stop" in got["logBefore"]                  # the client stopped at once: only the reload waits
    assert got["reloadsMid"] == 1                      # then it reloaded
    assert got["postedBefore"] == 0 and got["postedAfter"] == 1      # a panel frame tells its parent only once they are finished
    assert got["reloads"] == 2                         # nothing pending: as before


# --- the gap between ensureSession's write and the keeper listening (3.3) ---------------------------------------------------------

@node
def test_a_change_made_before_the_keeper_listened_stops_the_page_with_no_event():
    """Another tab's Log out, deletion here or sign-in that landed after ensureSession wrote the index but before attach() added its
    listener (ensureSession's store clean-up, index.js readWaiting's unseal) fired its `storage` event with nobody listening: attach()
    looks once, and the page ends as watchIndex would. An index that is as this page left it stops nothing."""
    for change, why in (
        ('e.s = "out"; delete e.sealed; idx.owner = null; idx.epoch += 1;', "logged_out"),
        ('e.s = "forget"; delete e.sealed;', "forgotten"),
        ('idx.owner = "@ruqqus_2:s";', "other_account"),
        ("", None),
    ):
        got = keeper("""
const session = await started();
const idx = getIndex();
const e = idx.entries.find((x) => x.d === "UQNEW0000000");
""" + change + """
store.set("uqqus_chat_devices", JSON.stringify(idx));    // no storage event: nobody was listening yet
const { k } = keeperFor(session);
await tick();
report({ log, reloads, stopped: k.stopped });
""")
        if why is None:
            assert got["log"] == [] and got["reloads"] == 0 and got["stopped"] is False
        else:
            assert got["log"] == ["stop"] and got["reloads"] == 1 and got["stopped"] is True, why


# --- the first sync of a call window or a Space: never waited for after the page stopped ------------------------------------------

@node
def test_the_first_sync_ends_when_the_page_stops_before_it():
    """A call window or a Space waits for its client's first sync. A keeper that stops meanwhile with no reload (a renewal the site
    refused, over the reload budget) stops the client, which then never syncs: firstSync answers false, so the Space lets its
    uqqus-call lock go and gives Join back instead of waiting for ever."""
    got = keeper("""
function syncingClient() {
  const handlers = new Map();
  return {
    started: 0,
    on(name, fn) { handlers.set(fn, name); },
    off(name, fn) { handlers.delete(fn); },
    emit(state) { for (const fn of [...handlers.keys()]) fn(state); },
    get listening() { return handlers.size; },
    async startClient() { this.started += 1; },
    stopClient() { log.push("stop"); },
  };
}
const results = {};
// synced: true
let session = await started();
let k = keeperFor(session).k;
let c = syncingClient();
let waiting = K.firstSync(c, k, "sync", {});
c.emit("PREPARED");
results.synced = { got: await waiting, started: c.started, listening: c.listening };
// the page stops first (a refused renewal: shutdown and no reload): false, and a sync after that changes nothing
session = await started();
k = keeperFor(session).k;
c = syncingClient();
waiting = K.firstSync(c, k, "sync", {});
await tick();
k.shutdown();
results.stopped = { got: await waiting, listening: c.listening };
// stopped already: false at once, the client never started
c = syncingClient();
results.already = { got: await K.firstSync(c, k, "sync", {}), started: c.started };
report({ results });
""")
    r = got["results"]
    assert r["synced"] == {"got": True, "started": 1, "listening": 0}
    assert r["stopped"] == {"got": False, "listening": 0}
    assert r["already"] == {"got": False, "started": 0}


def test_the_call_window_and_a_space_wait_for_their_first_sync_through_the_keeper():
    from chat_node_harness import SRC
    for name in ("call.js", "space.js"):
        source = (SRC / name).read_text(encoding="utf-8")
        start = source[source.index("async function startMatrix()"):]
        start = start[:start.index("\n}\n")]
        assert "firstSync(client, keeper, ClientEvent.Sync," in start and "throw new NotStarted();" in start, name
        assert "new Promise(" not in start and "client.startClient(" not in start, name      # no wait the keeper can't end
    space = (SRC / "space.js").read_text(encoding="utf-8")
    start = space[space.index("async function startMatrix()"):]
    after = start[start.index("firstSync("):]
    assert after.index("await keeper.shutdown();") < after.index("state.keeper = null;") < after.index("throw new NotStarted();")
    join = space[space.index("async function joinSpace()"):]
    assert "if (!state.client) letCallLockGo();" in join[:join.index("let turn = null;")]


@node
def test_a_page_another_tab_took_chat_from_acts_on_nothing_its_renewal_finds_afterwards():
    """Tab A is in its timed renewal, waiting after a failed try, when tab B takes chat over (holdChatLock's catch: shutdown, so
    `stopped` but not `ending`) and then signs another account in. A must ask the site nothing more and never reload: a reload would
    take chat back from B with steal. The same for a renewal already out when it stopped, the offline banner's Retry, and end()."""
    got = keeper("""
const results = {};
function otherAccount() {
  const idx = getIndex();
  idx.owner = "@ruqqus_2:s";
  store.set("uqqus_chat_devices", JSON.stringify(idx));
}
// 1. stopped in the timed renewal's backoff sleep, the index then changed
{
  const session = await started({ extra: { expires_in_ms: 3600000 } });
  clock = Date.now();
  const ref = {};
  ref.made = keeperFor(session, { sleep: async (ms) => {
    clock += ms;
    if (!ref.made.k.stopped) { ref.made.k.shutdown(); otherAccount(); }
  } });
  const { k, client } = ref.made;
  const lost = [];
  SO.watchSignedOut(client, k);
  const onLost = k.onLost;
  k.onLost = (why) => { lost.push(why); onLost(why); };
  clock = k.exp - 10 * 60 * 1000;
  serve = async () => ({ status: 503, body: {} });
  shown.length = 0;
  const before = reloads;
  await k.renewSoon(true);
  await tick(1700);
  results.backoff = { reloads: reloads - before, lost, asked: renewals().length, shown: shown.slice(), ending: k.ending };
  setIndex(null, [], getIndex().epoch + 1);
}
// 1b. stopped in the backoff sleep with nothing else changed: the site is never asked again
{
  const session = await started({ extra: { expires_in_ms: 3600000 } });
  clock = Date.now();
  const ref = {};
  let mark = 0;
  ref.made = keeperFor(session, { sleep: async (ms) => {
    clock += ms;
    if (!ref.made.k.stopped) { ref.made.k.shutdown(); mark = lockLog.length; }
  } });
  const { k } = ref.made;
  clock = k.exp - 10 * 60 * 1000;
  serve = async () => ({ status: 503, body: {} });
  await k.renewSoon(true);
  results.quiet = { asked: renewals().length, locked: lockLog.slice(mark).filter((l) => l === "take uqqus-chat-session").length };
  setIndex(null, [], getIndex().epoch + 1);
}
// 1c. stopped while the timed renewal's request was out, which then finds the device gone
{
  const session = await started({ extra: { expires_in_ms: 3600000 } });
  clock = Date.now();
  const { k, client } = keeperFor(session);
  const lost = [];
  k.onLost = (why) => lost.push(why);
  log.length = 0;
  clock = k.exp - 10 * 60 * 1000;
  serve = async () => { k.shutdown(); return { status: 409, body: { device_gone: true, reason: "others" } }; };
  await k.renewSoon(true);
  results.out = { lost, reason: k.reason };
  setIndex(null, [], getIndex().epoch + 1);
}
// 2. a renewal the SDK's give-up asked for, out when the page stopped, answered with the password sentence
{
  const session = await started();
  const { k, client } = keeperFor(session);
  SO.watchSignedOut(client, k);
  serve = async () => { k.shutdown(); return { status: 403, body: { need_password: true, error: "Password first." } }; };
  shown.length = 0;
  const before = reloads;
  await client.handlers[SO.SESSION_LOGGED_OUT]();
  await tick(1700);
  results.inflight = { reloads: reloads - before, shown: shown.slice() };
  setIndex(null, [], getIndex().epoch + 1);
}
// 3. the offline banner's Retry pressed after the page stopped
{
  const session = await started();
  let press = null;
  const offline = { ...ui, banner: (t, o = {}) => { shown.push(["banner", t]); if (o.onAction) press = o.onAction; } };
  const { k, client } = keeperFor(session, { ui: offline });
  SO.watchSignedOut(client, k);
  serve = async () => ({ status: 503, body: { retry: true } });
  k.reason = "transient";
  await client.handlers[SO.SESSION_LOGGED_OUT]();
  await k.shutdown();
  otherAccount();
  sent.length = 0;
  shown.length = 0;
  const before = reloads;
  await press();
  await tick(1700);
  results.retry = { reloads: reloads - before, asked: renewals().length, shown: shown.slice() };
  setIndex(null, [], getIndex().epoch + 1);
}
// 3b. Retry pressed, the page stopping while its renewal is out, which then finds the device gone
{
  const session = await started();
  let press = null;
  const offline = { ...ui, banner: (t, o = {}) => { shown.push(["banner", t]); if (o.onAction) press = o.onAction; } };
  const { k, client } = keeperFor(session, { ui: offline });
  SO.watchSignedOut(client, k);
  serve = async () => ({ status: 503, body: { retry: true } });
  k.reason = "transient";
  await client.handlers[SO.SESSION_LOGGED_OUT]();
  serve = async () => { k.shutdown(); return { status: 409, body: { device_gone: true, reason: "others" } }; };
  shown.length = 0;
  const before = reloads;
  await press();
  await tick(1700);
  results.retryOut = { reloads: reloads - before, shown: shown.slice() };
  setIndex(null, [], getIndex().epoch + 1);
}
// 4. end() on a page stopped without ending
{
  const session = await started();
  const { k } = keeperFor(session);
  await k.shutdown();
  const before = reloads;
  await k.end("signed_out");
  await tick(30);
  results.end = { reloads: reloads - before, ending: k.ending };
}
report({ results });
""")
    r = got["results"]
    assert r["backoff"] == {"reloads": 0, "lost": [], "asked": 1, "shown": [], "ending": False}, r["backoff"]
    assert r["quiet"] == {"asked": 1, "locked": 0}, r["quiet"]
    assert r["out"] == {"lost": [], "reason": None}, r["out"]
    assert r["inflight"] == {"reloads": 0, "shown": []}, r["inflight"]
    assert r["retry"] == {"reloads": 0, "asked": 0, "shown": []}, r["retry"]
    assert r["retryOut"] == {"reloads": 0, "shown": []}, r["retryOut"]
    assert r["end"] == {"reloads": 0, "ending": False}, r["end"]


@node
def test_each_failed_timed_renewal_replaces_the_retry_timer_and_a_retry_asks_again_whether_it_is_due():
    """While the site is down, every look at the page (the tab shown, the network back) that fails re-arms the minute's retry: each
    must replace the last, or each leaves its own chain posting every minute and, once the site answers, renewing a token renewed
    seconds before."""
    got = keeper("""
const session = await started({ extra: { expires_in_ms: 3600000 } });
clock = Date.now();
const { k, client } = keeperFor(session);
clock = k.exp + 1000;                              // past its end: each look tries once and gives up at once
serve = async () => ({ status: 503, body: {} });
const timers = [];
const realClear = clearTimeout;
globalThis.setTimeout = (fn, ms) => {
  if (ms !== 60 * 1000) return realSetTimeout(fn, ms);
  const handle = { fn, cleared: false, unref() {} };
  timers.push(handle);
  return handle;
};
globalThis.clearTimeout = (h) => { if (h && h.fn) h.cleared = true; else realClear(h); };
for (let i = 0; i < 3; i += 1) await k.renewSoon(false);
const live = timers.filter((t) => !t.cleared).length;
let n = 1;
serve = async () => { n += 1; return renewAnswer("tok-" + n, { expires_in_ms: 3600000 }); };
sent.length = 0;
clock += 60 * 1000;
for (const t of timers.slice()) { if (!t.cleared) { await t.fn(); await tick(30); } }
const afterRecovery = renewals().length;
const recovered = client.token;
// a retry that fires when the token was renewed meanwhile (by a 401, by another tab) leaves it alone
const before = renewals().length;
serve = async () => ({ status: 503, body: {} });
clock = k.exp + 1000;
await k.renewSoon(false);
const retry = timers.filter((t) => !t.cleared).pop();
serve = async () => renewAnswer("tok-fresh", { expires_in_ms: 3600000 });
await k.renew("x");                                // renewed meanwhile
const asked = renewals().length;
await retry.fn();
await tick(30);
globalThis.setTimeout = realSetTimeout;
globalThis.clearTimeout = realClear;
report({ live, afterRecovery, token: recovered, fresh: client.token, extra: renewals().length - asked, before });
""")
    assert got["live"] == 1, f"{got['live']} retry chains running at once"
    assert got["afterRecovery"] == 1 and got["token"] == "tok-2"
    assert got["extra"] == 0 and got["fresh"] == "tok-fresh", "a retry renewed a token renewed moments before"


# --- Start over: the one key upload the site makes for the member (keeper.js armKeyReset, routes/chat_key_reset_pages.py)

UPLOAD = r"""
const UP = "https://m/_matrix/client/v3/keys/device_signing/upload";
const KEYS = { master_key: { user_id: "@ruqqus_1:s", usage: ["master"], keys: { "ed25519:A": "A" } } };
const chatServer = [];
const upload = (k, body = KEYS, url = UP) => k.fetch(url, { method: "POST", body: JSON.stringify(body),
                                                     headers: { Authorization: "Bearer tok-UQNEW0000000" } });
"""


@node
def test_an_armed_upload_goes_to_the_site_once_with_its_grant_and_never_to_the_chat_server():
    got = keeper(UPLOAD + """
const session = await started();
const { k } = keeperFor(session, { fetch: async (u) => { chatServer.push(String(u)); return new Response("{}", { status: 200 }); } });
serve = async (req) => (req.url === "/api/chat/signing_keys" ? { status: 200, body: {} } : { status: 500 });
// nothing armed: the upload goes to the chat server as before
await upload(k);
k.armKeyReset("G1");
const res = await upload(k, { ...KEYS, auth: { type: "m.login.dummy", session: "s" } });
const after = await upload(k);                     // used once: the next one goes to the chat server again
report({ status: res.status, after: after.status, chatServer, toSite: sent.filter((r) => r.url === "/api/chat/signing_keys").map((r) => r.body) });
""")
    assert got["status"] == 200 and got["after"] == 200
    assert got["chatServer"] == ["https://m/_matrix/client/v3/keys/device_signing/upload"] * 2
    [body] = got["toSite"]
    assert body["grant"] == "G1" and body["formkey"] == "f"
    import json as _json
    assert "auth" not in _json.loads(body["keys"]) and "master_key" in _json.loads(body["keys"])
    assert all("G1" not in u for u in got["chatServer"])


@node
def test_only_that_path_and_a_post_are_taken():
    got = keeper(UPLOAD + """
const session = await started();
const { k } = keeperFor(session, { fetch: async (u) => { chatServer.push(String(u)); return new Response("{}", { status: 200 }); } });
serve = async () => ({ status: 200, body: {} });
k.armKeyReset("G1");
await k.fetch(UP, { method: "GET" });
await upload(k, KEYS, "https://m/_matrix/client/v3/keys/upload");
await upload(k, KEYS, "https://elsewhere/_matrix/client/v3/keys/device_signing/upload");
const still = k.keyResetGrant;
report({ chatServer, still, toSite: sent.filter((r) => r.url === "/api/chat/signing_keys").length });
""")
    assert got["toSite"] == 0 and got["still"] == "G1" and len(got["chatServer"]) == 3


@node
def test_the_sites_answer_reads_as_the_chat_servers():
    got = keeper(UPLOAD + """
const session = await started();
const { k } = keeperFor(session, { fetch: async () => new Response("{}", { status: 200 }) });
const answers = [];
for (const [status, body] of [[502, { matrix_status: 400, body: { errcode: "M_INVALID_SIGNATURE" } }],
                              [503, { error: "down", grant: "kept" }],
                              [403, { error: "again", grant: "gone" }],
                              [429, { error: "Too many requests" }]]) {
  serve = async () => ({ status, body });
  k.armKeyReset("G1");
  const res = await upload(k);
  answers.push({ status: res.status, body: await res.json(), armed: k.keyResetGrant });
}
report({ answers });
""")
    a, b, c, d = got["answers"]
    assert d["armed"] == "G1"                                         # the rate limit answered before the grant was read: kept
    assert a["status"] == 400 and a["body"]["errcode"] == "M_INVALID_SIGNATURE" and a["armed"] is None
    assert b["status"] == 503 and b["armed"] == "G1"                  # the site kept it: armed again for the retry
    assert c["status"] == 403 and c["armed"] is None
