"""The index and ensureSession (token.js; the chat plan's phase 2, sections 2 and 3.2), run under node with stand-ins for the browser
(tests/chat_node_harness.py): what a page start asks the site for, what it writes, what it keeps, and the only stores it deletes."""
import json

from chat_node_harness import A, B, NEW, OLD, node, prefix, run, stores

INDEX = "uqqus_chat_devices"
PROBE = "uqqus_chat_probe"                   # written and removed at once, before the site is asked
START = 'const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat"));\n'


def tokens(got):
    return [r for r in got["sent"] if r["url"] == "/api/chat/token"]


def entries(got, u=None):
    return [e for e in got["index"]["entries"] if u is None or e["u"] == u]


@node
def test_a_reload_with_a_token_that_doesnt_expire_asks_for_keys_only_and_writes_nothing():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "the-old-token") })]);
const before = store.get("uqqus_chat_devices");
writes.length = 0;
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got, same: store.get("uqqus_chat_devices") === before });
""")
    [ask] = tokens(got)
    assert ask["body"]["want"] == "key" and ask["body"]["device_id"] == OLD and ask["body"]["mode"] == "boot" and ask["body"]["kind"] == "chat"
    assert ask["redirect"] == "manual"
    assert got["got"]["value"]["accessToken"] == "the-old-token" and got["got"]["value"]["device_id"] == OLD
    assert got["same"] and INDEX not in got["writes"]               # no storage event for other tabs (gap 20)
    assert got["deleted"] == []


@node
def test_used_is_written_at_most_hourly():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { used: Date.now() - 2 * 3600 * 1000, sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""")
    [e] = entries(got)
    assert e["used"] > 1 and got["writes"].count(INDEX) == 1 and e["gen"] == 1


@node
def test_a_first_start_mints_one_device_and_keeps_its_token_only_sealed():
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + """
report({ got });
""")
    [ask] = tokens(got)
    assert ask["body"]["want"] == "token" and "device_id" not in ask["body"] and len(ask["body"]["mint_key"]) == 32
    session = got["got"]["value"]
    assert session["accessToken"] == "tok-" + NEW and session["prefix"] == prefix(A, NEW) and session["kind"] == "chat"
    assert session["exp"] is None
    [e] = entries(got)
    assert e["s"] == "in" and e["d"] == NEW and e["kid"] == "kid1" and set(e["sealed"]) == {"iv", "ct"} and e["x"] == 0
    assert got["index"]["owner"] == A
    assert all("tok-" + NEW not in value for value in got["stored"].values())       # the token is nowhere in plain text


@node
def test_an_expiring_token_records_when_it_ends():
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", { extra: { expires_in_ms: 3600000 } }) });
""" + START + """
report({ got, now: Date.now() });
""")
    [e] = entries(got)
    assert e["life"] == 3600000 and 0 <= got["now"] + 3600000 - e["exp"] < 5000      # the same millisecond is fine


def _start_with_sealed(life, left_ms):
    """A page start whose stored token lasts `life` ms in all and has `left_ms` of it left: what it asks the site for."""
    return run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", {
  life: %d, exp: Date.now() + %d, sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "the-old-token") })]);
serve = async (r) => ({ body: answer("@ruqqus_1:s", "UQOLD0000000",
  r.body.want === "key" ? { access_token: null } : { access_token: "the-new-token", extra: { expires_in_ms: %d } }) });
""" % (life, left_ms, life) + START + """
report({ got });
""")


@node
def test_a_sealed_token_ending_within_ten_minutes_asks_for_a_new_one_and_one_with_time_left_asks_for_keys_only():
    soon = _start_with_sealed(3600000, 5 * 60 * 1000)
    [ask] = tokens(soon)
    assert ask["body"]["want"] == "token" and ask["body"]["device_id"] == OLD
    assert soon["got"]["value"]["accessToken"] == "the-new-token"
    later = _start_with_sealed(3600000, 30 * 60 * 1000)
    [ask] = tokens(later)
    assert ask["body"]["want"] == "key" and later["got"]["value"]["accessToken"] == "the-old-token"


@node
def test_a_short_token_is_renewed_a_third_of_its_life_before_its_end_not_ten_minutes():
    # the specs' 90 s tokens (CHAT_TOKEN_REPORT_SECONDS): ten minutes before the end would be before they were given, so every start
    # would re-token; a third of the life is 30 s
    for left, want in ((45 * 1000, "key"), (20 * 1000, "token")):
        got = _start_with_sealed(90 * 1000, left)
        [ask] = tokens(got)
        assert ask["body"]["want"] == want, left


@node
def test_renew_before_is_ten_minutes_or_a_third_of_a_shorter_life():
    got = run("""
report({ hour: T.renewBefore(3600000), short: T.renewBefore(90000), none: T.renewBefore(null), half: T.renewBefore(30 * 60 * 1000) });
""")
    assert got["hour"] == 600000 and got["half"] == 600000 and got["none"] == 600000 and got["short"] == 30000


@node
def test_another_accounts_entries_are_kept_without_their_tokens():
    got = run("""
setIndex("@ruqqus_2:s", [
  entry("@ruqqus_2:s", "UQBBB0000000", "chat", { sealed: await boxFor("@ruqqus_2:s", "UQBBB0000000", "chat", "b-token") }),
  entry("@ruqqus_2:s", "UQBBC0000000", "call", { sealed: await boxFor("@ruqqus_2:s", "UQBBC0000000", "call", "b-call") }),
]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + """
report({ got });
""", dbs=stores(prefix(B, "UQBBB0000000")) + stores(prefix(B, "UQBBC0000000", "call")))
    theirs = entries(got, B)
    assert [e["s"] for e in theirs] == ["out", "out"] and not any("sealed" in e for e in theirs)
    assert all(e["gen"] == 2 for e in theirs)
    assert got["deleted"] == [] and got["index"]["owner"] == A
    assert "device_id" not in tokens(got)[0]["body"]                   # never another account's device


@node
def test_a_new_device_for_the_same_account_marks_the_old_one_removed_and_deletes_nothing():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out", x: 1 })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", { extra: { previous: { device_id: "UQOLD0000000", reason: "password" } } }) });
""" + START + """
report({ got });
""", dbs=stores(prefix(A, OLD)))
    assert tokens(got)[0]["body"]["device_id"] == OLD and tokens(got)[0]["body"]["want"] == "token"
    old, new = entries(got)
    assert old["s"] == "removed" and old["x"] == 1 and new["s"] == "in" and new["why"] == "password"
    assert got["got"]["value"]["why"] == "password"
    assert got["deleted"] == [] and set(stores(prefix(A, OLD))) <= set(got["dbs"])


@node
def test_the_same_account_after_a_log_out_takes_up_its_device_and_its_store():
    got = run("""
setIndex(null, [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out" }), entry("@ruqqus_1:s", "UQOLC0000000", "call", { s: "out" })], 3);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000") });
""" + START + """
report({ got });
""", dbs=stores(prefix(A, OLD)))
    assert tokens(got)[0]["body"]["device_id"] == OLD
    chat, call = entries(got)
    assert chat["s"] == "in" and "sealed" in chat and call["s"] == "out"          # the call window takes up its own device itself
    assert got["deleted"] == [] and got["index"]["epoch"] == 3 and got["index"]["owner"] == A


@node
def test_a_changed_key_replaces_the_device_and_never_reuses_its_id():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { kid: "kid0", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  if (r.body.device_id) return { body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) };
  return { body: answer("@ruqqus_1:s", "UQNEW0000000") };
};
""" + START + """
report({ got });
""", dbs=stores(prefix(A, OLD)))
    asks = [r["body"] for r in tokens(got)]
    assert [a["mode"] for a in asks] == ["boot", "replace", "boot"]
    assert asks[1]["device_id"] == OLD and asks[1]["why"] == "key_changed"
    assert "device_id" not in asks[2] and asks[2]["want"] == "token"
    assert asks[0]["mint_key"] == asks[2]["mint_key"]
    assert sorted(got["deleted"]) == sorted(stores(prefix(A, OLD)))      # exception 1: it can never be opened again
    [e] = entries(got)
    assert e["d"] == NEW and e["why"] == "key_changed"


@node
def test_a_store_that_went_missing_replaces_the_device():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { ed: "ED", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  if (r.body.device_id) return { body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) };
  return { body: answer("@ruqqus_1:s", "UQNEW0000000") };
};
""" + START + """
report({ got });
""")
    asks = [r["body"] for r in tokens(got)]
    assert asks[1]["mode"] == "replace" and asks[1]["why"] == "store_missing" and asks[1]["device_id"] == OLD
    assert "device_id" not in asks[2] and len(asks) == 3                 # the re-ask names no device, once
    # the old entry is kept, `removed` (2.6), and nothing is deleted: a missing store isn't one of 2.4's cases
    assert [(e["d"], e["s"]) for e in entries(got)] == [(OLD, "removed"), (NEW, "in")]
    assert got["deleted"] == [] and got["got"]["value"]["why"] == "store_missing"


KEY_SPY = """
const keys = [];
const importKey = crypto.subtle.importKey.bind(crypto.subtle);
crypto.subtle.importKey = (format, raw, ...rest) => { if (format === "raw") keys.push(raw); return importKey(format, raw, ...rest); };
"""


@node
def test_a_start_that_fails_after_the_answer_zeroes_the_keys_it_was_given():
    # a Log out lands while the answer is on its way (settle throws SignedOut), and storage that won't take the index
    # (StorageUnavailable from the write): neither drops the storage key or the waiting key with their bytes still in them (1.2)
    for trigger in ('store.set("uqqus_chat_devices", JSON.stringify({ v: 2, owner: null, epoch: 9, entries: [] }));',
                    "storageBroken = true;"):
        got = run(KEY_SPY + """
serve = async () => { """ + trigger + """ return { body: answer("@ruqqus_1:s", "UQNEW0000000") }; };
""" + START + """
report({ got, keys: keys.map((k) => Array.from(k).every((b) => b === 0)), sizes: keys.map((k) => k.length) });
""")
        assert "thrown" in got["got"], got["got"]
        assert got["sizes"] == [32, 32] and got["keys"] == [True, True], (trigger, got["keys"])


@node
def test_a_store_that_is_there_is_used():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { ed: "ED", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""", dbs=stores(prefix(A, OLD)))
    assert len(tokens(got)) == 1 and got["got"]["value"]["device_id"] == OLD and got["got"]["value"]["entry"]["ed"] == "ED"


@node
def test_a_start_whose_store_check_cant_list_databases_opens_without_making_one():
    got = run("""
delete globalThis.indexedDB.databases;
const opened = [];
globalThis.indexedDB.open = (name) => {
  opened.push(name);
  const request = { transaction: { abort() { opened.push("aborted"); setTimeout(() => request.onerror(), 0); } } };
  setTimeout(() => request.onupgradeneeded({ oldVersion: 0 }), 0);
  return request;
};
report({ missing: !(await T.storeExists("uqqus-chat-ruqqus_1-UQOLD0000000")), opened });
""")
    assert got["missing"] and got["opened"] == ["uqqus-chat-ruqqus_1-UQOLD0000000::matrix-sdk-crypto", "aborted"]


@node
def test_the_caps_evict_the_oldest_other_account_and_the_oldest_removed_stores_only():
    got = run("""
const now = Date.now();
const all = [];
// the signed-in account, whose newest use is the oldest of all: its current device, 7 removed stores (one over the cap), whatever their kind or x
all.push(entry("@ruqqus_1:s", "UQOLD0000000", "chat", { used: now - 30 * 60 * 1000, sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }));
for (let i = 0; i < 7; i += 1) all.push(entry("@ruqqus_1:s", "UQRM" + i + "0000000", i % 2 ? "call" : "chat", { s: "removed", x: i % 2, used: 1 + i }));
// four other accounts, all used here since (within the hour, so the signed-in account's `used` isn't moved): @ruqqus_3 is the oldest of them
for (const [n, ago] of [[3, 20], [4, 5], [5, 4], [6, 3]]) all.push(entry("@ruqqus_" + n + ":s", "UQACC" + n + "000000", "chat", { s: "out", used: now - ago * 60 * 1000 }));
setIndex("@ruqqus_1:s", all);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""")
    gone = sorted({name.split("::")[0] for name in got["deleted"]})
    assert gone == sorted([prefix(A, "UQRM00000000"), prefix("@ruqqus_3:s", "UQACC3000000")])
    left = {e["d"] for e in entries(got)}
    assert OLD in left and "UQRM00000000" not in left and "UQACC3000000" not in left
    assert {"UQRM10000000", "UQRM20000000", "UQRM60000000"} <= left              # removed stores within the cap are kept


@node
def test_no_other_rule_deletes_a_removed_or_broken_store():
    got = run("""
setIndex("@ruqqus_1:s", [
  entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }),
  entry("@ruqqus_1:s", "UQRMA0000000", "chat", { s: "removed", x: 0 }),
  entry("@ruqqus_1:s", "UQRMB0000000", "call", { s: "removed", x: 0 }),
  entry("@ruqqus_1:s", "UQBRK0000000", "chat", { s: "broken", bad: Date.now() - 29 * 24 * 3600 * 1000 }),
  entry("@ruqqus_2:s", "UQRMC0000000", "chat", { s: "removed", x: 1 }),
]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""")
    assert got["deleted"] == [] and len(entries(got)) == 5


@node
def test_a_store_broken_for_30_days_goes_at_a_start_and_a_younger_one_stays():
    """2.4 case 4: nobody knows why a broken store wouldn't open, so it is kept 30 days for a later look, then deleted (any account's)."""
    got = run("""
const day = 24 * 3600 * 1000;
setIndex("@ruqqus_1:s", [
  entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }),
  entry("@ruqqus_1:s", "UQBRKOLD0000", "chat", { s: "broken", bad: Date.now() - 31 * day }),
  entry("@ruqqus_1:s", "UQBRKNEW0000", "call", { s: "broken", bad: Date.now() - 29 * day }),
  entry("@ruqqus_2:s", "UQBRK2000000", "chat", { s: "broken", bad: Date.now() - 31 * day }),
  entry("@ruqqus_1:s", "UQRMOLD00000", "chat", { s: "removed", used: Date.now() - 400 * day }),
]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""")
    gone = sorted({name.split("::")[0] for name in got["deleted"]})
    assert gone == sorted([prefix(A, "UQBRKOLD0000"), prefix(B, "UQBRK2000000")])
    left = {e["d"] for e in entries(got)}
    assert left == {OLD, "UQBRKNEW0000", "UQRMOLD00000"}           # a removed store is never deleted for its age


@node
def test_an_old_keys_removed_store_goes_and_current_devices_of_other_kinds_are_left_to_their_pages():
    got = run("""
setIndex("@ruqqus_1:s", [
  entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }),
  entry("@ruqqus_1:s", "UQRMA0000000", "chat", { s: "removed", kid: "kid0" }),
  entry("@ruqqus_1:s", "UQCAL0000000", "call", { s: "out", kid: "kid0" }),
]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + """
report({ got });
""")
    assert sorted(got["deleted"]) == sorted(stores(prefix(A, "UQRMA0000000")))
    assert [e["d"] for e in entries(got)] == [OLD, "UQCAL0000000"]


@node
def test_a_log_out_while_the_answer_was_out_drops_it_and_no_lock_is_held_across_the_network():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out" })], 4);
const before = store.get("uqqus_chat_devices");
serve = async () => {
  const idx = getIndex();
  idx.epoch += 1; idx.owner = null;                    // all_js.js leaveChatSessions in another tab
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return { body: answer("@ruqqus_1:s", "UQOLD0000000") };
};
""" + START + """
report({ got });
""")
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"]["epoch"] == 5 and got["index"]["owner"] is None
    assert "sealed" not in got["index"]["entries"][0] and got["index"]["entries"][0]["s"] == "out"
    assert all(not r["locked"] and not r["lockedAfter"] for r in got["sent"])


@node
def test_a_newer_token_another_page_sealed_meanwhile_is_taken_not_overwritten():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out", gen: 3 })]);
const theirs = await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "their-token");
serve = async () => {
  const idx = getIndex();
  Object.assign(idx.entries[0], { s: "in", sealed: theirs, gen: 5 });
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return { body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: "our-token" }) };
};
""" + START + """
report({ got, theirs });
""")
    assert got["got"]["value"]["accessToken"] == "their-token"
    [e] = entries(got)
    assert e["sealed"] == got["theirs"] and e["gen"] == 5


@node
def test_two_tabs_at_once_hold_no_lock_across_the_network_and_end_with_one_current_device():
    got = run("""
const other = await import(SRC + "/token.js?second-tab");
let n = 0;
serve = async (r) => {
  n += 1;
  const d = "UQTAB" + n + "000000";
  await new Promise((resolve) => setTimeout(resolve, 5));
  return { body: answer("@ruqqus_1:s", d) };
};
const results = await Promise.all([T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat"),
                                   other.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat")]);
report({ results });
""")
    assert len(tokens(got)) == 2 and all(not r["locked"] for r in got["sent"])
    live = [e for e in entries(got) if e["s"] == "in"]
    assert len(live) == 1 and len(entries(got)) == 2
    assert not any(name.startswith("uqqus-chat-") for name in got["deleted"])          # the other device is `removed`, its store kept
    assert tokens(got)[0]["body"]["mint_key"] != tokens(got)[1]["body"]["mint_key"]       # two pages, two keys


@node
def test_a_page_retrying_its_start_sends_the_same_mint_key():
    got = run("""
let n = 0;
serve = async () => { n += 1; return n === 1 ? { status: 503, body: { retry: true, error: "later" } } : { body: answer("@ruqqus_1:s", "UQNEW0000000") }; };
const first = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat"));
const second = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat"));
report({ first, second });
""")
    assert got["first"]["thrown"] == "ChatUnavailable" and got["first"]["message"] == "later"
    a, b = tokens(got)
    assert a["body"]["mint_key"] == b["body"]["mint_key"] and got["second"]["value"]["device_id"] == NEW
    assert entries(got)[0]["d"] == NEW


@node
def test_a_stale_form_key_is_refreshed_once_and_a_sign_in_that_ended_is_signed_out():
    got = run("""
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { body: { formkey: "fresh", mxid: "@ruqqus_1:s" } };
  return r.body.formkey === "fresh" ? { body: answer("@ruqqus_1:s", "UQNEW0000000") } : { status: 401, body: {} };
};
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
const ok = await attempt(T.ensureSession(boot, "chat"));
report({ ok, formkey: boot.formkey });
""")
    urls = [r["url"] for r in got["sent"]]
    assert urls == ["/api/chat/token", "/api/chat/formkey", "/api/chat/token"]
    assert got["sent"][1]["method"] == "GET" and got["sent"][1]["redirect"] == "manual"
    assert got["sent"][2]["body"]["formkey"] == "fresh" and got["formkey"] == "fresh" and got["ok"]["value"]["device_id"] == NEW

    gone = run("""
serve = async (r) => ({ type: "opaqueredirect", status: 0 });
""" + START + """
report({ got });
""")
    assert gone["got"]["thrown"] == "SignedOut" and [r["url"] for r in gone["sent"]] == ["/api/chat/token", "/api/chat/formkey"]
    assert gone["index"] is None


@node
def test_a_form_key_that_couldnt_be_asked_for_now_is_a_retry_never_signed_out():
    """A start whose stale key can't be refreshed for now (a 429, a 5xx, a timeout) is ChatUnavailable with a retry: nothing stored is
    touched, as for any answer the site couldn't give, and the page never reloads as signed out."""
    for failure in ('({ status: 429 })', '({ status: 503 })', '(() => { throw new Error("TimeoutError"); })()'):
        got = run("""
serve = async (r) => (r.url === "/api/chat/formkey" ? __FAILURE__ : { status: 401, body: {} });
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
let retry = null;
const got = await attempt(T.ensureSession(boot, "chat").catch((e) => { retry = e.retry; throw e; }));
report({ got, retry, formkey: boot.formkey });
""".replace("__FAILURE__", failure), local={"uqqus_chat_devices": "{}"})
        assert got["got"]["thrown"] == "ChatUnavailable" and got["retry"] is True, failure
        assert [r["url"] for r in got["sent"]] == ["/api/chat/token", "/api/chat/formkey"], failure
        assert got["formkey"] == "stale" and got["deleted"] == [] and got["stored"]["uqqus_chat_devices"] == "{}", failure


@node
def test_a_browser_that_cant_keep_data_asks_the_site_nothing():
    got = run(START + "report({ got });", broken=True)
    assert got["got"]["thrown"] == "StorageUnavailable" and got["sent"] == []


@node
def test_without_web_locks_nothing_is_deleted():
    got = run("""
setIndex("@ruqqus_1:s", [
  entry("@ruqqus_1:s", "UQOLD0000000", "chat", { kid: "kid0", sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") }),
  entry("@ruqqus_2:s", "UQGON0000000", "chat", { s: "forget" }),
]);
store.set("ruqqus_chat_session", "{}");
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  return { body: answer("@ruqqus_1:s", r.body.device_id || "UQNEW0000000", { access_token: r.body.device_id ? null : "t" }) };
};
""" + START + """
report({ got });
""", locks=False, dbs=stores(prefix(A, OLD)))
    assert got["got"]["value"]["device_id"] == NEW
    assert got["deleted"] == [] and "ruqqus_chat_session" not in got["stored"]       # keys go; stores only with Web Locks
    assert {e["d"]: e["s"] for e in entries(got)} == {OLD: "removed", "UQGON0000000": "forget", NEW: "in"}


@node
def test_the_timeout_falls_back_to_a_controller_and_a_timer():
    got = run("""
delete AbortSignal.timeout;
const signal = T.timeoutSignal(20);
const early = signal.aborted;
await new Promise((resolve) => setTimeout(resolve, 60));
report({ early, late: signal.aborted });
""")
    assert got["early"] is False and got["late"] is True


@node
def test_the_legacy_client_is_cleared_only_after_the_site_answered_and_only_once():
    legacy = {"ruqqus_chat_session": '{"user_id":"@ruqqus_1:s","access_token":"plain"}', "ruqqus_call_session": "{}",
              "mxjssdk_memory_filter_FILTER_SYNC_@ruqqus_1:s": "x", "density": "compact"}
    old_stores = stores("matrix-js-sdk") + stores("uqqus-call")
    refused = run("""
serve = async () => ({ status: 403, body: { need_password: true, error: "password first" } });
""" + START + "report({ got });", local=legacy, dbs=old_stores)
    assert refused["got"]["thrown"] == "PasswordNeeded" and refused["got"]["message"] == "password first"
    assert refused["stored"] == legacy and refused["deleted"] == [] and refused["writes"] == [PROBE]    # a QR need_password answer writes nothing

    done = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", local=legacy, dbs=old_stores)
    assert set(done["stored"]) == {"density", INDEX}
    assert sorted(done["deleted"]) == sorted(old_stores)

    again = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: null }) });
""" + START + "report({ got });")
    assert again["deleted"] == []


@node
def test_every_refusal_touches_nothing():
    for status, body, thrown in ((403, {"age_locked": True, "error": "locked"}, "ChatRefused"), (503, {"retry": True}, "ChatUnavailable"),
                                 (429, {}, "ChatUnavailable"), (400, {"error": "x"}, "ChatUnavailable")):
        got = run("""
setIndex("@ruqqus_2:s", [entry("@ruqqus_2:s", "UQBBB0000000", "chat", { sealed: await boxFor("@ruqqus_2:s", "UQBBB0000000", "chat", "b") })]);
const before = store.get("uqqus_chat_devices");
writes.length = 0;
serve = async () => ({ status: __STATUS__, body: __BODY__ });
""".replace("__STATUS__", str(status)).replace("__BODY__", json.dumps(body)) + START + """
report({ got, same: store.get("uqqus_chat_devices") === before });
""")
        assert got["got"]["thrown"] == thrown and got["same"] and got["writes"] == [PROBE] and got["deleted"] == [], status


@node
def test_an_answer_for_another_account_is_never_used():
    got = run("""
serve = async () => ({ body: answer("@ruqqus_2:s", "UQNEW0000000") });
""" + START + "report({ got });")
    assert got["got"]["thrown"] == "SignedOut" and got["index"] is None


@node
def test_a_sealed_token_that_wont_open_asks_for_a_new_one_for_the_same_device():
    got = run("""
const wrong = await boxFor("@ruqqus_1:s", "UQNEW0000000", "chat", "t");        // sealed under another device's key
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: wrong })]);
serve = async (r) => ({ body: answer("@ruqqus_1:s", "UQOLD0000000", { access_token: r.body.want === "key" ? null : "renewed" }) });
""" + START + "report({ got });")
    assert [(r["body"]["want"], r["body"]["device_id"]) for r in tokens(got)] == [("key", OLD), ("token", OLD)]
    assert got["got"]["value"]["accessToken"] == "renewed"


@node
def test_an_account_deleted_here_has_its_stores_go_at_the_next_start_when_free():
    got = run("""
setIndex(null, [entry("@ruqqus_2:s", "UQGON0000000", "chat", { s: "forget" }), entry("@ruqqus_2:s", "UQGOC0000000", "call", { s: "forget" })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", held=["uqqus-store:" + prefix(B, "UQGOC0000000", "call")])
    assert sorted(got["deleted"]) == sorted(stores(prefix(B, "UQGON0000000")))
    assert {e["d"] for e in entries(got, B)} == {"UQGOC0000000"}                     # its lock wasn't free: kept, still `forget`
    assert entries(got, B)[0]["s"] == "forget"


@node
def test_a_store_whose_delete_fails_keeps_its_forget_entry_for_the_ad_hold_and_the_next_start():
    # the plan's 5.4: a delete that threw leaves the entry `forget`, so the index (all the ad hold reads) still names the database
    failed = prefix(B, "UQGON0000000")
    got = run("""
setIndex(null, [entry("@ruqqus_2:s", "UQGON0000000", "chat", { s: "forget" }), entry("@ruqqus_2:s", "UQGOX0000000", "chat", { s: "forget" })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", dbs=stores(failed) + stores(prefix(B, "UQGOX0000000")), failing=[failed + "::matrix-sdk-crypto-meta"])
    assert "thrown" not in got["got"]
    assert failed + "::matrix-sdk-crypto-meta" in got["dbs"]
    assert [(e["d"], e["s"]) for e in entries(got, B)] == [("UQGON0000000", "forget")]   # the one that went is gone from the index


@node
def test_a_call_start_names_its_own_store_and_leaves_the_chat_device_alone():
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQOLD0000000", "chat", "t") })]);
serve = async () => ({ body: answer("@ruqqus_1:s", "UQCAL0000000", { kind: "call" }) });
const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "call"));
report({ got });
""")
    assert tokens(got)[0]["body"]["kind"] == "call" and "device_id" not in tokens(got)[0]["body"]
    assert got["got"]["value"]["prefix"] == prefix(A, "UQCAL0000000", "call")
    assert {(e["d"], e["k"], e["s"]) for e in entries(got)} == {(OLD, "chat", "in"), ("UQCAL0000000", "call", "in")}


@node
def test_an_unreadable_index_is_read_as_empty_and_overwritten_only_after_an_answer():
    refused = run("""
serve = async () => ({ status: 503, body: {} });
""" + START + "report({ got });", local={INDEX: "{not json"})
    assert refused["stored"][INDEX] == "{not json"
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", local={INDEX: '{"v":1}'})
    assert got["index"]["v"] == 2 and [e["d"] for e in entries(got)] == [NEW]


@node
def test_waiting_messages_are_sealed_for_their_account_only():
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
await T.writeWaiting(session, { "!room": [{ text: "hello there plain words", at: 1 }] });
const kept = store.get("uqqus_chat_waiting");
const back = await T.readWaiting(session);
const theirs = await T.readWaiting({ ...session, user_id: "@ruqqus_2:s" });
const afterTheirs = store.has("uqqus_chat_waiting");
store.set("uqqus_chat_waiting", '{"!r":[{"text":"old plain"}]}');
const old = await T.readWaiting(session);
const afterOld = store.has("uqqus_chat_waiting");
await T.writeWaiting(session, { "!a": [] });
await T.writeWaiting(session, {});
report({ kept, back, theirs, afterTheirs, old, afterOld, last: store.has("uqqus_chat_waiting") });
""")
    kept = json.loads(got["kept"])
    assert kept["v"] == 2 and kept["u"] == A and "hello" not in got["kept"]
    assert got["back"] == {"!room": [{"text": "hello there plain words", "at": 1}]}
    assert got["theirs"] == {} and got["afterTheirs"] is False
    assert got["old"] == {} and got["afterOld"] is False and got["last"] is False


@node
def test_opening_the_store_gives_it_its_key_zeroes_it_and_notes_the_device_key():
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
const keyView = session.storeKey;
const client = {
  initRustCrypto: async (options) => { client.options = { prefix: options.cryptoDatabasePrefix, len: options.storageKey.length, sum: options.storageKey.reduce((a, b) => a + b, 0) }; },
  getCrypto: () => ({ getOwnDeviceKeys: async () => ({ ed25519: "EDKEY", curve25519: "C" }) }),
};
await T.openCryptoStore(client, session);
report({ options: client.options, zeroed: keyView.every((b) => b === 0) });
""")
    assert got["options"]["prefix"] == prefix(A, NEW) and got["options"]["len"] == 32 and got["options"]["sum"] > 0
    assert got["zeroed"] and entries(got)[0]["ed"] == "EDKEY"


# --- what the second look under the lock (settle) refuses to write ---------------------------------------------------------------

@node
def test_an_answer_for_one_account_never_lands_after_another_account_settled_meanwhile():
    # A asked (tab 1); before its answer came, B's cookie arrived (no Log out: A's sign-in had ended elsewhere) and B's tab settled
    got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out" })], 4);
const theirs = await boxFor("@ruqqus_2:s", "UQBBB0000000", "chat", "b-token");
serve = async () => {
  const idx = getIndex();
  idx.owner = "@ruqqus_2:s";                         // epoch unchanged: a sign-in, not a Log out
  idx.entries.push(entry("@ruqqus_2:s", "UQBBB0000000", "chat", { sealed: theirs }));
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  return { body: answer("@ruqqus_1:s", "UQOLD0000000") };
};
""" + START + """
report({ got, theirs });
""")
    assert got["got"]["thrown"] == "SignedOut"
    assert got["index"]["owner"] == B and got["index"]["epoch"] == 4
    [b] = entries(got, B)
    assert b["s"] == "in" and b["sealed"] == got["theirs"]
    [a] = entries(got, A)
    assert a["s"] == "out" and "sealed" not in a


@node
def test_a_device_taken_out_of_use_meanwhile_is_never_brought_back():
    for state in ("broken", "removed", "forget"):
        got = run("""
setIndex("@ruqqus_1:s", [entry("@ruqqus_1:s", "UQOLD0000000", "chat", { s: "out" })]);
let n = 0;
serve = async () => {
  n += 1;
  if (n === 1) {
    const idx = getIndex();                          // another tab gave the device up (its store wouldn't open), or a deletion here
    Object.assign(idx.entries[0], { s: "__STATE__", gen: 9 });
    store.set("uqqus_chat_devices", JSON.stringify(idx));
    return { body: answer("@ruqqus_1:s", "UQOLD0000000") };
  }
  return { body: answer("@ruqqus_1:s", "UQNEW0000000") };
};
""".replace("__STATE__", state) + START + """
report({ got });
""")
        asks = [r["body"] for r in tokens(got)]
        assert len(asks) == 2 and asks[0]["device_id"] == OLD and "device_id" not in asks[1], state     # section A again: no device
        assert got["got"]["value"]["device_id"] == NEW, state
        old = [e for e in entries(got) if e["d"] == OLD]
        assert all(e["s"] == state for e in old), state                                    # never `in` again


@node
def test_a_page_that_lost_chat_while_the_site_answered_writes_nothing_and_demotes_no_one():
    # two pages start at once on a browser with no device here: the second took uqqus-matrix, minted D2 and settled first
    got = run("""
let mine = true;
serve = async () => {
  const idx = getIndex() || { v: 2, owner: null, epoch: 0, entries: [] };
  idx.owner = "@ruqqus_1:s";
  idx.entries.push(entry("@ruqqus_1:s", "UQTAB2000000", "chat", { sealed: await boxFor("@ruqqus_1:s", "UQTAB2000000", "chat", "t2") }));
  store.set("uqqus_chat_devices", JSON.stringify(idx));
  mine = false;                                      // holdChatLock's `lost` ran in this page meanwhile
  return { body: answer("@ruqqus_1:s", "UQTAB1000000") };
};
const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat", { stillMine: () => mine }));
report({ got });
""")
    assert got["got"]["thrown"] == "TakenOver"
    [e] = entries(got)
    assert e["d"] == "UQTAB2000000" and e["s"] == "in" and "sealed" in e


@node
def test_a_form_key_for_another_account_is_never_taken():
    got = run("""
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { body: { formkey: "theirs", mxid: "@ruqqus_2:s" } };
  return r.body.formkey === "theirs" ? { body: answer("@ruqqus_2:s", "UQNEW0000000") } : { status: 401, body: {} };
};
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
const got = await attempt(T.ensureSession(boot, "chat"));
const noMxid = await T.refreshFormkey({ formkey: "x" });
const plain = await T.refreshFormkey("x");
report({ got, formkey: boot.formkey, noMxid, plain });
""")
    assert got["got"]["thrown"] == "SignedOut" and got["formkey"] == "stale"
    assert [r["url"] for r in got["sent"]] == ["/api/chat/token", "/api/chat/formkey"]
    assert got["noMxid"] == "over" and got["plain"] == "over" and len(got["sent"]) == 2      # nothing to compare: never asked


@node
def test_a_device_minted_for_a_page_that_lost_chat_meanwhile_is_signed_out_with_the_site():
    """Left live, it could be the device a password change in this browser spares (chat_signouts.this_sign_in: the same sign-in, the most
    recent) in place of the one the tab that kept chat uses. Only a device minted for this start: one the browser already held is kept."""
    scenario = """
let mine = true;
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  mine = false;                                      // holdChatLock's `lost` ran in this page meanwhile
  return { body: answer("@ruqqus_1:s", "UQTAB1000000", { extra: { new_device: %s } }) };
};
const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat", { stillMine: () => mine }));
report({ got });
"""
    got = run(scenario % "true")
    assert got["got"]["thrown"] == "TakenOver"
    [boot, replace] = tokens(got)
    assert boot["body"]["mode"] == "boot"
    assert replace["body"] == {"formkey": "f", "kind": "chat", "mode": "replace", "device_id": "UQTAB1000000", "why": "taken_over"}
    assert not replace["locked"]                     # never with the session lock held
    held = run(scenario % "false")
    assert held["got"]["thrown"] == "TakenOver" and [r["body"]["mode"] for r in tokens(held)] == ["boot"]


@node
def test_a_device_minted_then_dropped_for_a_log_out_meanwhile_is_signed_out_too():
    got = run("""
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  setIndex(null, [], 5);                             // a Log out in another tab while the answer was on its way
  return { body: answer("@ruqqus_1:s", "UQNEW0000000", { extra: { new_device: true } }) };
};
""" + START + "report({ got });")
    assert got["got"]["thrown"] == "SignedOut"
    assert [(r["body"]["mode"], r["body"].get("why")) for r in tokens(got)] == [("boot", None), ("replace", "taken_over")]


UNLISTED = json.dumps({"v": 2, "owner": None, "epoch": 1, "entries": [], "unlisted": True})


@node
def test_a_start_over_an_unlisted_index_gives_every_store_here_its_entry_back():
    """What replaced an unreadable index (a Log out, a deletion: all_js.js) names none of the stores it did: the next start lists the
    databases and writes each one an entry, signed out, so the caps count it, a deletion finds it and the ad hold sees it; then the index
    is no longer `unlisted`. None is deleted for its unknown key id."""
    here = (stores(prefix(B, "UQBBB0000000")) + stores(prefix(B, "UQBBC0000000", "call")) + stores(prefix(A, OLD))
            + stores(prefix(A, "UQCAL0000000", "call")) + ["matrix-js-sdk::matrix-sdk-crypto", "unrelated"])
    for start in (UNLISTED, "not json"):
        got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", local={INDEX: start}, dbs=here)
        assert got["got"]["value"]["device_id"] == NEW
        idx = got["index"]
        assert "unlisted" not in idx and idx["owner"] == A
        found = {(e["u"], e["d"], e["k"]): (e["s"], e["kid"]) for e in idx["entries"]}
        assert found == {(B, "UQBBB0000000", "chat"): ("out", None), (B, "UQBBC0000000", "call"): ("out", None),
                         (A, OLD, "chat"): ("removed", None), (A, "UQCAL0000000", "call"): ("out", None), (A, NEW, "chat"): ("in", "kid1")}
        assert not any(name.startswith(("uqqus-chat-", "uqqus-call-")) for name in got["deleted"])   # the legacy `uqqus-call` goes


@node
def test_an_entry_given_back_from_a_listed_store_is_guarded_when_its_store_goes_and_takes_its_key_on_first_opening():
    """An entry rebuilt from `indexedDB.databases()` held keys there: a call start that finds its store gone meanwhile (evicted, cleared)
    replaces the device instead of opening a fresh store for it (2.6), and one whose store is still there records the real device key
    the first time it opens, never treating the mark as a key that doesn't match."""
    here = stores(prefix(A, "UQCAL0000000", "call"))
    scenario = """
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
const rebuilt = getIndex().entries.find((e) => e.d === "UQCAL0000000");
%s
sent.length = 0;
serve = async (r) => {
  if (r.body.mode === "replace") return { body: { replaced: true } };
  if (r.body.device_id === "UQCAL0000000") return { body: answer("@ruqqus_1:s", "UQCAL0000000", { kind: "call" }) };
  return { body: answer("@ruqqus_1:s", "UQCNEW000000", { kind: "call" }) };
};
const got = await attempt(T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "call"));
let opened = null;
if (got.value && got.value.device_id === "UQCAL0000000") {
  const client = { initRustCrypto: async () => {}, getCrypto: () => ({ getOwnDeviceKeys: async () => ({ ed25519: "ED-REAL" }) }), stopClient() {} };
  opened = await attempt(T.openCryptoStore(client, got.value));
}
report({ got, rebuilt, opened });
"""
    gone = run(scenario % "for (const name of storesOf(rebuilt.p)) dbs.delete(name);", local={INDEX: UNLISTED}, dbs=here)
    assert gone["rebuilt"]["s"] == "out" and gone["rebuilt"]["ed"] == "listed"
    asks = [r["body"] for r in tokens(gone)]
    assert asks[0]["device_id"] == "UQCAL0000000"
    assert asks[1]["mode"] == "replace" and asks[1]["why"] == "store_missing" and asks[1]["device_id"] == "UQCAL0000000"
    assert gone["got"]["value"]["device_id"] == "UQCNEW000000" and gone["got"]["value"]["why"] == "store_missing"
    kept = run(scenario % "", local={INDEX: UNLISTED}, dbs=here)
    assert kept["got"]["value"]["device_id"] == "UQCAL0000000" and kept["opened"] == {}
    assert [e for e in entries(kept) if e["d"] == "UQCAL0000000"][0]["ed"] == "ED-REAL"


@node
def test_a_browser_that_cant_list_its_databases_keeps_the_index_unlisted():
    for start in (UNLISTED, "not json"):
        got = run("""
indexedDB.databases = undefined;
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
""" + START + "report({ got });", local={INDEX: start}, dbs=stores(prefix(B, "UQBBB0000000")))
        assert got["got"]["value"]["device_id"] == NEW
        assert got["index"]["unlisted"] is True and [e["d"] for e in entries(got)] == [NEW]


@node
def test_a_change_to_the_waiting_list_is_made_on_what_is_stored_now():
    """Two tabs of one account each hold a copy: a change from one (a message added, the ones it sent taken off) is made on the stored
    list, so it never drops what the other tab added since; nothing is written when nothing changed; an emptied room goes."""
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
await T.writeWaiting(session, { "!a": [{ text: "one", at: 1 }, { text: "two", at: 2 }] });     // what this tab read at its start
await T.updateWaiting(session, (list) => { list["!b"] = [{ text: "from the other tab", at: 3 }]; });
const afterSend = await T.updateWaiting(session, (list) => { list["!a"].splice(0, 1); });    // this tab sent "one" only
writes.length = 0;
const unchanged = await T.updateWaiting(session, () => {});
const quiet = writes.filter((k) => k === "uqqus_chat_waiting").length;
const emptied = await T.updateWaiting(session, (list) => { list["!a"] = []; list["!b"] = []; });
report({ afterSend, unchanged, quiet, emptied, left: store.has("uqqus_chat_waiting") });
""")
    assert got["afterSend"] == {"!a": [{"text": "two", "at": 2}], "!b": [{"text": "from the other tab", "at": 3}]}
    assert got["unchanged"] == got["afterSend"] and got["quiet"] == 0
    assert got["emptied"] == {} and got["left"] is False


@node
def test_a_change_to_the_waiting_list_never_undoes_a_log_out_or_another_tabs_write_that_came_while_it_unsealed():
    """A page that stopped still takes off what it sent (index.js `removing`): if a Log out removes the list while that change unseals
    it, nothing is written back; and another tab's message added meanwhile is kept, the change made again on what is stored now."""
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
const realDecrypt = crypto.subtle.decrypt.bind(crypto.subtle);
let meanwhile = null;
crypto.subtle.decrypt = async (...args) => { const run = meanwhile; meanwhile = null; if (run) await run(); return realDecrypt(...args); };
const two = { "!a": [{ text: "one", at: 1 }, { text: "two", at: 2 }] };
await T.writeWaiting(session, two);
meanwhile = () => store.delete("uqqus_chat_waiting");             // all_js.js leaveChatSessions
await T.updateWaiting(session, (list) => { if (list["!a"]) list["!a"].splice(0, 1); });
const afterLogOut = store.has("uqqus_chat_waiting");
await T.writeWaiting(session, two);
const theirs = { "!a": [{ text: "one", at: 1 }, { text: "two", at: 2 }], "!b": [{ text: "from the other tab", at: 3 }] };
meanwhile = async () => {                                         // another tab adds a message, sealed as token.js seals it
  const box = await seal.seal(session.waitingKey, seal.waitingAad("@ruqqus_1:s"), theirs);
  store.set("uqqus_chat_waiting", JSON.stringify({ v: 2, u: "@ruqqus_1:s", iv: box.iv, ct: box.ct }));
};
const now = await T.updateWaiting(session, (list) => { if (list["!a"]) list["!a"].splice(0, 1); });
report({ afterLogOut, now, kept: await T.readWaiting(session) });
""")
    assert got["afterLogOut"] is False
    assert got["now"] == got["kept"] == {"!a": [{"text": "two", "at": 2}], "!b": [{"text": "from the other tab", "at": 3}]}


@node
def test_a_stopped_page_taking_off_what_it_sent_never_touches_another_accounts_waiting_list():
    """Account 1's page stopped (account 2 signed in here and opened chat) while a send was on its way; account 2 then put a first message
    in its own sealed list. The late send's `removing` change must leave that list alone: only a page's own start removes another
    account's box (readWaiting)."""
    got = run("""
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000") });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
const theirs = JSON.stringify({ v: 2, u: "@ruqqus_2:s", iv: "aXYxMjM0NTY3ODkw", ct: "Y2lwaGVy" });
store.set("uqqus_chat_waiting", theirs);
writes.length = 0;
const now = await T.updateWaiting(session, (list) => { if (list["!a"]) list["!a"].splice(0, 1); });
const added = await T.updateWaiting(session, (list) => { list["!a"] = [{ text: "late", at: 9 }]; });
report({ now, added, kept: store.get("uqqus_chat_waiting") === theirs, written: writes.filter((k) => k === "uqqus_chat_waiting").length });
""")
    assert got["now"] == {} and got["added"] == {}
    assert got["kept"] is True and got["written"] == 0


def test_a_stopped_chat_page_sends_and_writes_no_waiting_messages():
    """A page that lost chat (another tab) or stopped (a refused renewal, a Log out elsewhere) still has its tabs and search, which run
    sendWaiting: its client is stopped, and its copy of the list is old. It sends nothing and writes nothing; a room's messages leave
    the list only as they are sent, so a send that fails keeps the rest."""
    from chat_node_harness import SRC
    source = (SRC / "index.js").read_text(encoding="utf-8")
    part = source[source.index("let waitingList = {};"):source.index("function waitingNotice(conversation)")]
    assert "writeWaiting" not in source and "saveWaiting" not in source
    assert "return chatLockLost || !!(state.keeper && state.keeper.stopped);" in part
    assert "if (!chatSession || (chatGone() && !removing)) return Promise.resolve(null);" in part
    waiting_for = part[part.index("function waitFor("):part.index("let sendingWaiting")]
    assert waiting_for.index("if (chatGone()) return;") < waiting_for.index("changeWaiting(")
    send = part[part.index("async function sendWaiting()"):]
    assert "if (state.locked || chatGone() || sendingWaiting) return;" in send
    assert "delete waiting[roomId]" not in send and "sent.push(item);" in send and "if (chatGone()) break;" in send
    assert send.index("catch (e) { break; }") < send.index("sent.push(item);") < send.index("left.splice(at, 1);")
    # what was sent leaves the list even when the page stopped right after it (the next page would send it again): only that change may
    assert part.count("{ removing: true }") == 1 and send.index("left.splice(at, 1);") < send.index("{ removing: true }")
