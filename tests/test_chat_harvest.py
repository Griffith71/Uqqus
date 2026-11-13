"""The stores this browser kept of an account's older chat devices (harvest.js; the chat plan's phase 2, 2.4 and 8), run under node with
stand-ins for the browser (tests/chat_node_harness.py) and a fake OlmMachine in place of the opened store: which keys move to the current
chat device, that a store goes only after its keys were imported or when it was found empty, the store locks, the failures, and the
installed SDK's private members the client leans on (RustBackupManager.maxBackupLoopStartDelayMillis, its maybeUploadKey)."""
import re

import pytest

from chat_node_harness import A, B, IN_CI, ROOT, SRC, node, prefix, run, stores

CHAT_SRC = SRC.parent
SDK = CHAT_SRC / "node_modules" / "matrix-js-sdk"
WASM = CHAT_SRC / "node_modules" / "@matrix-org" / "matrix-sdk-crypto-wasm"
# harvest.js imports the crypto wasm, so the node scenarios need chat_src's node_modules (npm ci); never skipped under CI (9.5).
installed = pytest.mark.skipif(not WASM.exists() and not IN_CI,
                               reason="the chat bundle's node_modules are not installed (npm ci in chat_src)")

HARVEST = r"""
const H = await import(SRC + "/harvest.js");
const events = [];
const realDelete = indexedDB.deleteDatabase.bind(indexedDB);
indexedDB.deleteDatabase = (name) => { events.push("delete " + name.split("::")[0] + " lock=" + busy.has("uqqus-store:" + name.split("::")[0])); return realDelete(name); };
// what each kept store holds: { total, backedUp, version, master, selfSigning, userSigning, decryption, sessions: [imported...], throws }
const held = new Map();
const opened = [];
const exported = [];
async function open(e, key) {
  const lock = busy.has("uqqus-store:" + e.p);
  opened.push({ p: e.p, lock, key: Buffer.from(key).toString("base64url") });
  events.push("open " + e.p + " lock=" + lock);
  const h = held.get(e.p) || {};
  if (h.throws === "open") throw new Error("failed to be decrypted while unpickling");
  let closed = false;
  return {
    async roomKeyCounts() { return { total: h.total || 0, backedUp: h.backedUp || 0, free() {} }; },
    async crossSigningStatus() { return { hasMaster: !!h.master, hasSelfSigning: !!h.selfSigning, hasUserSigning: !!h.userSigning, free() {} }; },
    async getBackupKeys() { return { backupVersion: h.version, decryptionKey: h.decryption ? { free() {} } : undefined, free() {} }; },
    async exportRoomKeys(rule) {
      if (h.throws === "export") throw new Error("export failed");
      const sessions = (h.sessions || []).map((imported, i) => ({ id: e.d + "-" + i, hasBeenImported: () => imported }));
      const taken = sessions.filter((s) => rule(s)).map((s) => ({ session_id: s.id }));
      exported.push({ p: e.p, ids: taken.map((s) => s.session_id) });
      events.push("export " + e.p);
      return JSON.stringify(taken);
    },
    close() { if (!closed) { closed = true; events.push("close " + e.p); } },
  };
}
// the page's own crypto: the current chat device
let current = "v1";
let importFails = false;
const imported = [];
let nudged = 0;
const client = { getCrypto: () => ({
  getActiveSessionBackupVersion: async () => current,
  async importRoomKeysAsJson(json) {
    events.push("import-start");
    await new Promise((r) => setTimeout(r, 20));
    if (importFails) throw new Error("import failed");
    imported.push(...JSON.parse(json).map((s) => s.session_id));
    events.push("import-end lock=" + [...busy].some((n) => n.startsWith("uqqus-store:")));
  },
  backupManager: { maybeUploadKey() { nudged += 1; } },
}) };
const session = { user_id: "@ruqqus_1:s", boot: { formkey: "f", mxid: "@ruqqus_1:s" } };
// the site serves the keys of the account's own site devices (not `unknown`), with today's key id
let unknown = new Set();
let kid = "kid1";
serve = async (r) => {
  if (r.url !== "/api/chat/store_keys") return { status: 404 };
  const keys = {};
  for (const d of r.body.device_ids.split(",")) if (!unknown.has(d)) keys[d] = { key: keyOf(d), kid };
  return { body: { keys } };
};
const current_ = entry("@ruqqus_1:s", "UQCUR0000000", "chat", { x: 1 });
function kept(d, k = "chat", fields = {}) { return entry("@ruqqus_1:s", d, k, { s: "removed", used: 1000, ...fields }); }
function byD(d) { const i = getIndex(); return i && i.entries ? i.entries.find((e) => e.d === d) || null : null; }
function hreport(extra = {}) { report({ events, opened, exported, imported, nudged, ...extra }); }
"""


def harvest_run(scenario, **kwargs):
    return run(HARVEST + scenario, **kwargs)


def dbs(*prefixes):
    return [name for p in prefixes for name in stores(p)]


P1, P2, P3 = prefix(A, "UQRM10000000"), prefix(A, "UQRM20000000"), prefix(A, "UQRM30000000")
PCALL = prefix(A, "UQRMCALL0000", "call")


def gone(got):
    return sorted({name.split("::")[0] for name in got["deleted"]})


def left(got):
    return {e["d"] for e in got["index"]["entries"]}


def stored(got, d):
    return next((e for e in got["index"]["entries"] if e["d"] == d), None)


# --- which keys move ----------------------------------------------------------------------------------------------------------------

@node
@installed
def test_the_same_backup_with_every_session_backed_up_exports_nothing_and_the_store_goes():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 3, backedUp: 3, version: "v1", sessions: [false, false, true], master: true });
const out = await H.harvest(client, session, { open });
hreport({ out });
""" % P1, dbs=dbs(P1))
    assert got["exported"] == [] and got["imported"] == []
    assert gone(got) == [P1] and "UQRM10000000" not in left(got)
    assert got["out"] == [{"d": "UQRM10000000", "what": "gone"}]


@node
@installed
def test_the_same_backup_missing_sessions_exports_everything_imported_sessions_included():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 3, backedUp: 1, version: "v1", sessions: [false, true, true] });
await H.harvest(client, session, { open });
hreport();
""" % P1, dbs=dbs(P1))
    assert got["imported"] == ["UQRM10000000-0", "UQRM10000000-1", "UQRM10000000-2"]
    assert got["nudged"] == 1                                       # the backup loop is asked to upload them
    assert gone(got) == [P1]


@node
@installed
@pytest.mark.parametrize("current", ['"v2"', "null"])
def test_another_backup_or_none_exports_everything(current):
    got = harvest_run("""
current = %s;
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 2, backedUp: 2, version: "v1", sessions: [true, false] });
await H.harvest(client, session, { open });
hreport();
""" % (current, P1), dbs=dbs(P1))
    assert got["imported"] == ["UQRM10000000-0", "UQRM10000000-1"] and gone(got) == [P1]


@node
@installed
def test_an_old_store_with_no_backup_version_exports_everything():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 1, backedUp: 1, version: undefined, sessions: [true] });
await H.harvest(client, session, { open });
hreport();
""" % P1, dbs=dbs(P1))
    assert got["imported"] == ["UQRM10000000-0"]


def test_the_export_rule():
    """The same rule written out (harvest.js exportRule): none only for the same backup with every session in it."""
    text = (SRC / "harvest.js").read_text(encoding="utf-8")
    rule = text[text.index("export function exportRule("):text.index("function close(")]
    assert "if (old && old === current && backedUp >= total) return null;" in rule and "return () => true;" in rule


# --- deletion only after the import, or when found empty ---------------------------------------------------------------------------

@node
@installed
def test_the_store_is_deleted_only_after_the_import_resolved_and_under_its_lock():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 2, backedUp: 0, version: "v1", sessions: [false, false] });
await H.harvest(client, session, { open });
hreport();
""" % P1, dbs=dbs(P1))
    ev = got["events"]
    assert ev[0] == f"open {P1} lock=true"
    end = ev.index("import-end lock=true")                          # the store lock is held across the import
    # the old machine is closed once its export is in hand, before the import (memory: never both machines and the string at once), and
    # closed once; the store itself goes only after the import resolved
    assert ev.index(f"export {P1}") < ev.index(f"close {P1}") < ev.index("import-start") < end < ev.index(f"delete {P1} lock=true")
    assert ev.count(f"close {P1}") == 1
    assert all(e.endswith("lock=true") for e in ev if e.startswith("delete "))
    # the key is asked with the session lock free (never held across the network)
    [ask] = [r for r in got["sent"] if r["url"] == "/api/chat/store_keys"]
    assert ask["locked"] is False and ask["body"]["device_ids"] == "UQRM10000000"


@node
@installed
def test_an_import_that_throws_keeps_the_store_and_counts_a_failure_and_the_third_makes_it_broken():
    got = harvest_run("""
importFails = true;
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000"), kept("UQRM20000000", "chat", { fails: 2, why: "password" })]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
const out = await H.harvest(client, session, { open });
hreport({ out });
""" % (P1, P2), dbs=dbs(P1, P2))
    assert got["deleted"] == [] and {"UQRM10000000", "UQRM20000000"} <= left(got)
    first, third = stored(got, "UQRM10000000"), stored(got, "UQRM20000000")
    assert first["fails"] == 1 and first["s"] == "removed"
    assert third["fails"] == 3 and third["s"] == "broken" and third["bad"] > 0 and third["why"] is None
    assert [o["what"] for o in got["out"]] == ["failed", "failed"]


@node
@installed
@pytest.mark.parametrize("throws", ["open", "export"])
def test_a_store_that_wont_open_or_export_is_kept(throws):
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false], throws: %r });
await H.harvest(client, session, { open });
hreport();
""" % (P1, throws), dbs=dbs(P1))
    assert got["deleted"] == [] and stored(got, "UQRM10000000")["fails"] == 1 and got["imported"] == []


@node
@installed
def test_a_store_that_wont_open_is_charged_one_failure_a_page_load_however_often_it_is_looked_at():
    """The chat page sweeps and then harvests in one load (index.js harvestKept): a store that threw in the sweep is not opened again by that
    load's harvest, nor by a second harvest, and counts one failure, so `broken` (deleted 30 days later) takes three page loads, not one
    bad moment seen twice. Each node run is one page load: the next one counts again."""
    scenario = """
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { fails: %d })]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false], throws: "open" });
const swept = await H.sweepEmpty(session, { open });
const first = await H.harvest(client, session, { open });
const second = await H.harvest(client, session, { open });
hreport({ swept, first, second });
"""
    got = harvest_run(scenario % (0, P1), dbs=dbs(P1))
    assert [o["p"] for o in got["opened"]] == [P1]                   # opened once in the load, by the sweep
    assert got["swept"] == [{"d": "UQRM10000000", "what": "failed"}] and got["first"] == [] and got["second"] == []
    assert stored(got, "UQRM10000000")["fails"] == 1 and stored(got, "UQRM10000000")["s"] == "removed"
    # the next page load (a reload, a call window) counts again: two loads, two failures, still kept and still `removed`
    again = harvest_run(scenario % (1, P1), dbs=dbs(P1))
    assert stored(again, "UQRM10000000")["fails"] == 2 and stored(again, "UQRM10000000")["s"] == "removed"
    assert got["deleted"] == [] and again["deleted"] == []


@node
@installed
def test_a_page_that_stops_before_the_import_imports_nothing_keeps_the_store_and_counts_nothing():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
let stop = false;
const realExport = open;
const out = await H.harvest(client, session, { open: async (e, k) => { const m = await realExport(e, k); const ex = m.exportRoomKeys; m.exportRoomKeys = async (r) => { const j = await ex(r); stop = true; return j; }; return m; }, stopped: () => stop });
hreport({ out });
""" % P1, dbs=dbs(P1))
    assert got["imported"] == [] and got["deleted"] == [] and stored(got, "UQRM10000000")["fails"] == 0
    assert got["out"] == [{"d": "UQRM10000000", "what": "stopped"}] and f"close {P1}" in got["events"]


@node
@installed
def test_a_store_whose_lock_is_held_is_skipped():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 1, backedUp: 0, sessions: [false] });
const swept = await H.sweepEmpty(session, { open });
const out = await H.harvest(client, session, { open });
hreport({ out, swept });
""" % P1, dbs=dbs(P1), held=["uqqus-store:" + P1])
    assert got["opened"] == [] and got["deleted"] == [] and stored(got, "UQRM10000000")["s"] == "removed"
    assert got["out"] == [{"d": "UQRM10000000", "what": "skipped"}]


@node
@installed
def test_without_web_locks_nothing_is_opened_asked_or_deleted():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { empty: 5 })]);
held.set(%r, { total: 0 });
await H.sweepEmpty(session, { open });
await H.harvest(client, session, { open });
hreport();
""" % P1, dbs=dbs(P1), locks=False)
    assert got["opened"] == [] and got["deleted"] == [] and got["sent"] == []


@node
@installed
def test_call_stores_and_never_unlocked_chat_stores_are_harvested_like_unlocked_ones():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRMCALL0000", "call", { x: 0, used: 3000 }), kept("UQRM10000000", "chat", { x: 0, used: 2000 })]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [true] });
await H.harvest(client, session, { open });
hreport();
""" % (PCALL, P1), dbs=dbs(PCALL, P1))
    assert sorted(got["imported"]) == ["UQRM10000000-0", "UQRMCALL0000-0"] and gone(got) == sorted([PCALL, P1])


@node
@installed
def test_at_most_two_stores_are_harvested_a_page_load_newest_first():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { used: 1000 }), kept("UQRM20000000", "chat", { used: 3000 }),
                         kept("UQRM30000000", "chat", { used: 2000 })]);
for (const p of [%r, %r, %r]) held.set(p, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
await H.harvest(client, session, { open });
await H.harvest(client, session, { open });                     // the same page load: nothing more
hreport();
""" % (P1, P2, P3), dbs=dbs(P1, P2, P3))
    assert [o["p"] for o in got["opened"]] == [P2, P3] and "UQRM10000000" in left(got)


@node
@installed
def test_only_this_accounts_removed_stores_are_touched():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_,
  entry("@ruqqus_1:s", "UQOUT0000000", "chat", { s: "out" }), entry("@ruqqus_1:s", "UQBRK0000000", "chat", { s: "broken", bad: Date.now() }),
  entry("@ruqqus_1:s", "UQFGT0000000", "call", { s: "forget" }), entry("@ruqqus_2:s", "UQOTHER00000", "chat", { s: "removed" })]);
await H.sweepEmpty(session, { open });
await H.harvest(client, session, { open });
hreport();
""")
    assert got["opened"] == [] and got["deleted"] == [] and got["sent"] == []
    assert len(got["index"]["entries"]) == 5


@node
@installed
def test_a_key_the_site_doesnt_serve_leaves_the_store_and_a_retired_key_id_deletes_it_unopened():
    got = harvest_run("""
unknown = new Set(["UQRM10000000"]);
kid = "kid2";
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000"), kept("UQRM20000000"), kept("UQRM30000000", "chat", { kid: null, used: 1 })]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
await H.harvest(client, session, { open });
hreport();
""" % P3, dbs=dbs(P1, P2, P3))
    # P2's key id isn't the site's now: never opened again (case 1); P3 was harvested, then deleted
    assert gone(got) == sorted([P2, P3])
    assert "UQRM10000000" in left(got) and stored(got, "UQRM10000000")["fails"] == 0
    assert [o["p"] for o in got["opened"]] == [P3]                    # a listed store (kid null) opens with the key served
    assert got["imported"] == ["UQRM30000000-0"]
    assert all(o["key"] for o in got["opened"])


@node
@installed
def test_a_site_that_cant_answer_touches_nothing():
    got = harvest_run("""
serve = async () => ({ status: 503, body: { retry: true } });
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
await H.sweepEmpty(session, { open });
await H.harvest(client, session, { open });
hreport();
""", dbs=dbs(P1))
    assert got["opened"] == [] and got["deleted"] == [] and stored(got, "UQRM10000000")["fails"] == 0


# --- sweepEmpty -----------------------------------------------------------------------------------------------------------------------

@node
@installed
def test_sweep_deletes_a_store_found_empty_and_keeps_one_holding_anything():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { used: 9 }), kept("UQRMCALL0000", "call", { used: 8 })]);
held.set(%r, { total: 0, version: "v1" });
held.set(%r, { total: 4, backedUp: 4 });
const out = await H.sweepEmpty(session, { open });
hreport({ out });
""" % (P1, PCALL), dbs=dbs(P1, PCALL))
    assert gone(got) == [P1] and "UQRM10000000" not in left(got)
    assert got["exported"] == [] and got["imported"] == []
    kept_ = stored(got, "UQRMCALL0000")
    assert kept_["s"] == "removed" and kept_["held"] > 0 and not kept_.get("empty")
    assert got["out"] == [{"d": "UQRM10000000", "what": "gone"}, {"d": "UQRMCALL0000", "what": "kept"}]


@node
@installed
@pytest.mark.parametrize("holding", ["master: true", "selfSigning: true", "userSigning: true", "decryption: true", "total: 1"])
def test_sweep_keeps_a_store_with_any_secret_or_room_key(holding):
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { %s });
await H.sweepEmpty(session, { open });
hreport();
""" % (P1, holding), dbs=dbs(P1))
    assert got["deleted"] == [] and stored(got, "UQRM10000000")["held"] > 0


@node
@installed
def test_sweep_marks_empty_before_it_deletes_and_a_delete_that_failed_is_finished_later_without_opening():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 0 });
await H.sweepEmpty(session, { open });
hreport();
""" % P1, dbs=dbs(P1), failing=stores(P1)[:1])
    assert stored(got, "UQRM10000000")["empty"] > 0                 # marked, and kept while its databases are still there
    again = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { empty: 5 })]);
await H.sweepEmpty(session, { open });
hreport();
""", dbs=dbs(P1))
    assert again["opened"] == [] and gone(again) == [P1] and "UQRM10000000" not in left(again)
    assert again["sent"] == []                                       # no key needed to finish a deletion


@node
@installed
def test_sweep_opens_at_most_two_stores_a_page_load_and_never_looks_twice_at_one_it_kept():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { used: 3 }), kept("UQRM20000000", "chat", { used: 2 }),
                         kept("UQRM30000000", "chat", { used: 1, held: 7 })]);
for (const p of [%r, %r]) held.set(p, { total: 1 });
await H.sweepEmpty(session, { open });
await H.sweepEmpty(session, { open });
hreport();
""" % (P1, P2), dbs=dbs(P1, P2, P3))
    assert [o["p"] for o in got["opened"]] == [P1, P2]               # the third is `held` already: left to the harvest
    [ask] = [r for r in got["sent"] if r["url"] == "/api/chat/store_keys"]
    assert ask["body"]["device_ids"] == "UQRM10000000,UQRM20000000"


@node
@installed
def test_the_harvest_takes_a_store_the_sweep_kept():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000", "chat", { held: 5 })]);
held.set(%r, { total: 2, backedUp: 0, version: "v1", sessions: [false, true] });
await H.harvest(client, session, { open });
hreport();
""" % P1, dbs=dbs(P1))
    assert got["imported"] == ["UQRM10000000-0", "UQRM10000000-1"] and gone(got) == [P1]


@node
@installed
def test_an_entry_that_became_forget_meanwhile_is_left_to_the_deletion():
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 0 });
const out = await H.sweepEmpty(session, { open: async (e, k) => {
  const i = getIndex(); i.entries.find((x) => x.d === "UQRM10000000").s = "forget"; store.set("uqqus_chat_devices", JSON.stringify(i));
  return open(e, k);
} });
hreport({ out });
""" % P1, dbs=dbs(P1))
    assert got["deleted"] == [] and stored(got, "UQRM10000000")["s"] == "forget"


@node
@installed
def test_an_export_is_judged_empty_or_not_in_place():
    """Whether an export holds a session is read from its first characters (harvest.js holdsSessions), never by copying the whole string
    without its white space, which doubled the harvest's largest allocation."""
    got = harvest_run("""
const cases = ["[]", " [ ] ", "[\\n]", '[{"session_id":"x"}]', ' [ {"a":1}]', "", "{}", null, "[" + " ".repeat(1000) + "]"];
hreport({ answers: cases.map((c) => H.holdsSessions(c)) });
""")
    assert got["answers"] == [False, False, False, True, True, False, False, False, False]
    text = (SRC / "harvest.js").read_text(encoding="utf-8")
    assert ".replace(" not in text and "if (holdsSessions(json)) {" in text


@node
@installed
def test_a_few_hundred_real_sessions_move_in_one_harvest():
    """The plan's 8, "What it costs": a store of a few hundred sessions, measured with the real crypto wasm (the one the harvest imports):
    300 megolm sessions made in a real OlmMachine, exported by the harvest and imported into a second real one standing in for the
    current chat device. The export is about 550 characters a session, so a store of tens of thousands of sessions makes a string of
    several MB; a heavy store is not tried in a browser (the plan's "Not in Phase 2")."""
    got = harvest_run("""
const w = await import(%r);
const real = await w.OlmMachine.initialize(new w.UserId("@ruqqus_1:s"), new w.DeviceId("UQRM10000000"));
for (let i = 0; i < 300; i += 1) await real.shareRoomKey(new w.RoomId("!r" + i + ":s"), [], new w.EncryptionSettings());
const into = await w.OlmMachine.initialize(new w.UserId("@ruqqus_1:s"), new w.DeviceId("UQCUR0000000"));
let size = 0, took = 0;
const page = { getCrypto: () => ({
  getActiveSessionBackupVersion: async () => null,
  async importRoomKeysAsJson(json) { size = json.length; const t = Date.now(); await into.importExportedRoomKeys(json, () => {}); took = Date.now() - t; },
  backupManager: { maybeUploadKey() {} },
}) };
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
const started = Date.now();
const out = await H.harvest(page, session, { open: async () => real });
const counts = await into.roomKeyCounts();
hreport({ out, size, took, all: Date.now() - started, moved: Number(counts.total) });
""" % (WASM / "node.mjs").as_uri(), dbs=dbs(P1))
    assert got["out"] == [{"d": "UQRM10000000", "what": "gone"}] and got["moved"] == 300
    assert 300 * 300 < got["size"] < 300 * 1000                   # about 550 characters a session
    assert gone(got) == [P1]


# --- the wiring, and the installed SDK it leans on -------------------------------------------------------------------------------

def test_the_chat_page_sweeps_once_its_store_opened_and_harvests_after_it_unlocked():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert 'import { harvest, sweepEmpty } from "./harvest.js";' in index
    opened = index.index("if (!opened || chatLockLost || keeper.stopped) return;")
    sweep = index.index("const swept = sweepEmpty(session, { stopped: chatGone })")
    assert opened < sweep < index.index("client.getCrypto().setDeviceIsolationMode(")
    # the harvest always follows the sweep, and is the one call
    assert ("const harvestKept = () => swept.then(() => harvest(client, session, { stopped: chatGone, hold: (work) => keeper.holdFor(work) }))"
            "\n    .catch(() => {});") in index
    assert index.count("harvest(") == 1
    # after ensureEncryptionReady answered true, markUnlocked and signOwnDevices; never in the hidden Send-in-chat frame
    ready = index[index.index("sendGate.ready();"):index.index("unlockChat();")]
    assert ready.index("markUnlocked(session)") < ready.index("await signOwnDevices(client, [signedCallDevice])") < ready.index("harvestKept()")
    assert 'if (frameRole() !== "share") harvestKept();' in ready


@installed
def test_the_guild_chat_tabs_frame_harvests_too_when_its_device_was_unlocked_here():
    """The plan's 8 names the guild Chat tab's `single` frame among the roles that harvest. It never unlocks (no box in a frame of the
    guild page), so it harvests only when this device was unlocked here before (the entry's `x`): its store then holds the account's keys,
    and its backup switches itself on as the client starts. A device never unlocked here leaves the older stores to the chat page."""
    index = (SRC / "index.js").read_text(encoding="utf-8")
    single = index[index.index("  if (boot.single) {\n    sendGate.locked();"):]
    single = single[:single.index("    return;\n  }") + 1]
    assert "if (session.entry && session.entry.x) harvestKept();" in single
    # the client switches its backup on as it starts (the harvest's import is then uploaded), in the installed SDK
    rust = (SDK / "lib" / "rust-crypto" / "rust-crypto.js").read_text(encoding="utf-8")
    ctor = rust[rust.index("class RustCrypto"):]
    ctor = ctor[ctor.index("constructor("):]
    assert "this.checkKeyBackupAndEnable();" in ctor[:ctor.index("\n  }\n")]


@pytest.mark.parametrize("name", ["call.js", "space.js"])
def test_the_call_window_and_a_space_only_sweep(name):
    text = (SRC / name).read_text(encoding="utf-8")
    assert 'import { sweepEmpty } from "./harvest.js";' in text and "harvest(" not in text
    assert "sweepEmpty(session, { stopped: () => keeper.stopped })" in text
    assert text.index("keeper.openStore(") < text.index("sweepEmpty(session") < text.index("await firstSync(")


def test_the_harvest_opens_kept_stores_through_the_crypto_wasm_never_through_initRustCrypto():
    text = (SRC / "harvest.js").read_text(encoding="utf-8")
    assert 'from "@matrix-org/matrix-sdk-crypto-wasm";' in text
    assert "StoreHandle.openWithKey(entry.p, key)" in text and "OlmMachine.initFromStore(new UserId(entry.u), new DeviceId(entry.d), store)" in text
    assert "initRustCrypto" not in text.split("// The index is written only", 1)[1]
    assert '{ ifAvailable: true }' in text and "STORE_LOCK + prefix" in text
    # deleted only through the two marks (found empty, or harvested once the import resolved) or a retired key id
    assert text.count("deleteHeld(") == 4 and text.count("deleteStore(") == 1


def test_the_backup_loop_starts_within_two_seconds():
    """8: a private static of the installed SDK, set by the chat page. The bundle is built from the locked SDK (9.5)."""
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert 'import { RustBackupManager } from "matrix-js-sdk/lib/rust-crypto/backup.js";' in index
    assert re.search(r"^RustBackupManager\.maxBackupLoopStartDelayMillis = 2000;$", index, re.M)


@installed
def test_the_installed_sdk_has_the_private_members_the_client_sets_and_calls():
    backup = (SDK / "lib" / "rust-crypto" / "backup.js").read_text(encoding="utf-8")
    assert '_defineProperty(RustBackupManager, "maxBackupLoopStartDelayMillis", 10000);' in backup
    assert "const delay = Math.random() * RustBackupManager.maxBackupLoopStartDelayMillis;" in backup
    assert "export class RustBackupManager " in backup and "  async maybeUploadKey() {" in backup
    assert "  async importRoomKeysAsJson(jsonKeys, opts) {\n    await this.olmMachine.importExportedRoomKeys(" in backup
    rust = (SDK / "lib" / "rust-crypto" / "rust-crypto.js").read_text(encoding="utf-8")
    assert "this.backupManager = new RustBackupManager(" in rust
    assert "void this.backupManager.maybeUploadKey();" in rust
    assert "  async getActiveSessionBackupVersion() {" in rust
    # the SDK's package has no exports map, so the deep import path resolves to the very module the client uses
    assert '"exports"' not in (SDK / "package.json").read_text(encoding="utf-8")
    dts = (WASM / "pkg" / "matrix_sdk_crypto_wasm.d.ts").read_text(encoding="utf-8")
    for member in ("static openWithKey(store_name: string, store_key: Uint8Array", "static initFromStore(user_id: UserId, device_id: DeviceId",
                   "roomKeyCounts(): Promise<RoomKeyCounts>;", "crossSigningStatus(): Promise<CrossSigningStatus>;",
                   "getBackupKeys(): Promise<BackupKeys>;", "exportRoomKeys(predicate: (session: InboundGroupSession) => boolean): Promise<string>;",
                   "readonly hasMaster: boolean;", "readonly hasSelfSigning: boolean;", "readonly hasUserSigning: boolean;",
                   "get backupVersion(): string | undefined;", "get decryptionKey(): BackupDecryptionKey | undefined;",
                   "    backedUp: number;", "    total: number;", "    close(): void;"):
        assert member in dts, member


@node
@installed
def test_the_static_is_really_there_when_the_installed_backup_module_runs():
    got = run("""
const m = await import(%r);
const before = m.RustBackupManager.maxBackupLoopStartDelayMillis;
m.RustBackupManager.maxBackupLoopStartDelayMillis = 2000;
report({ before, after: m.RustBackupManager.maxBackupLoopStartDelayMillis, maybe: typeof m.RustBackupManager.prototype.maybeUploadKey });
""" % (SDK / "lib" / "rust-crypto" / "backup.js").as_uri())
    assert got["before"] == 10000 and got["after"] == 2000 and got["maybe"] == "function"


@node
@installed
def test_the_wasm_the_harvest_imports_has_what_it_calls():
    got = run("""
const w = await import(%r);
report({ store: typeof w.StoreHandle.openWithKey, init: typeof w.OlmMachine.initFromStore,
         methods: ["roomKeyCounts", "crossSigningStatus", "getBackupKeys", "exportRoomKeys", "close"].filter((n) => typeof w.OlmMachine.prototype[n] !== "function") });
""" % (WASM / "node.mjs").as_uri())
    assert got["store"] == "function" and got["init"] == "function" and got["methods"] == []


def test_the_harvest_never_runs_in_the_page_that_has_no_chat_device_to_take_the_keys():
    for name in ("call.js", "space.js", "chat_apps.js", "token.js", "keeper.js"):
        assert "harvest(" not in (SRC / name).read_text(encoding="utf-8"), name
    assert (ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "harvest.js").exists()


@node
@installed
def test_an_entry_that_stopped_being_removed_before_its_lock_came_is_never_opened():
    """Asked again under the store lock: an account deleted here meanwhile (`forget`) is the deletion's to finish, never opened."""
    got = harvest_run("""
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 0 });
const site = serve;
serve = async (r) => {
  const i = getIndex(); i.entries.find((x) => x.d === "UQRM10000000").s = "forget"; store.set("uqqus_chat_devices", JSON.stringify(i));
  return site(r);
};
const out = await H.sweepEmpty(session, { open });
hreport({ out });
""" % P1, dbs=dbs(P1))
    assert got["opened"] == [] and got["deleted"] == [] and got["out"] == [{"d": "UQRM10000000", "what": "skipped"}]


@node
@installed
def test_a_page_taken_over_during_the_import_lets_its_store_go_only_after_the_import_settled():
    """Closing the OlmMachine doesn't stop an import already under way (the installed crypto wasm: its future owns its own handle on the
    machine and the store, so the import resolves after close()). So a takeover in the middle of the harvest's import must not release the
    current device's store lock (the real keeper.js shutdown) until the import has settled, or the tab that took over would open that store
    beside a machine still writing it. The import here resolves after the page stopped, as the real one does."""
    got = harvest_run("""
globalThis.window = globalThis.window || { tr: (t) => t, location: { origin: "https://site", search: "" }, addEventListener() {} };
const K = await import(SRC + "/keeper.js");
setIndex("@ruqqus_1:s", [current_, kept("UQRM10000000")]);
held.set(%r, { total: 1, backedUp: 0, version: "v1", sessions: [false] });
const mine = "uqqus-store:" + current_.p;
const k = new K.SessionKeeper({ ...session, device_id: current_.d, kind: "chat", prefix: current_.p }, { ui: {}, role: "top" });
await k.holdStore();
const heldAtStart = busy.has(mine);
k.client = { stopClient() { events.push("stopClient lock=" + busy.has(mine)); } };
const crypto = client.getCrypto();
const page = { getCrypto: () => ({
  ...crypto,
  async importRoomKeysAsJson(json) {
    events.push("import-start");
    setTimeout(() => k.shutdown(), 0);             // another tab takes chat over now (a task of its own: the lock was stolen)
    await new Promise((r) => setTimeout(r, 40));
    imported.push(...JSON.parse(json).map((s) => s.session_id));
    events.push("import-end lock=" + busy.has(mine) + " stopped=" + k.stopped);
  },
}) };
const out = await H.harvest(page, session, { open, stopped: () => k.stopped, hold: (work) => k.holdFor(work) });
await k.closing;
await new Promise((r) => setTimeout(r, 10));
hreport({ out, heldAtStart, heldAfter: busy.has(mine) });
""" % P1, dbs=dbs(P1))
    ev = got["events"]
    assert got["heldAtStart"] is True and got["heldAfter"] is False
    # the import ended with the current store's lock still held, and the client stopped only after it
    assert "import-end lock=true stopped=true" in ev and "stopClient lock=true" in ev
    assert ev.index("import-start") < ev.index("import-end lock=true stopped=true") < ev.index("stopClient lock=true")
    # the keys are in the current device's store, so the old store goes; the stopping crypto isn't nudged
    assert got["imported"] == ["UQRM10000000-0"] and got["nudged"] == 0
    assert got["out"] == [{"d": "UQRM10000000", "what": "gone"}] and gone(got) == [P1]


def test_the_harvest_holds_its_import_through_the_keeper():
    text = (SRC / "harvest.js").read_text(encoding="utf-8")
    assert "await hold(crypto.importRoomKeysAsJson(json));" in text
    assert "await crypto.importRoomKeysAsJson(" not in text
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert "harvest(client, session, { stopped: chatGone, hold: (work) => keeper.holdFor(work) })" in index


def test_the_pages_say_what_the_harvest_brings_back_and_what_start_over_does():
    """5.5: Settings > Chat apps says a browser signed out there gets back what it could read once unlocked (the harvest). Start over finishes
    here now (the site makes its key upload: CLAUDE.md "The site vouches for a Start over"), so its question says what this browser's kept
    stores bring back; the reset pages say the recovery code is needed."""
    templates = ROOT / "ruqqus" / "templates"
    apps = (templates / "settings_chat_apps.html").read_text(encoding="utf-8")
    assert '{{ _("What it could already read comes back once it is unlocked.") }}' in apps
    for name in ("forgot_password.html", "reset_password.html"):
        page = (templates / name).read_text(encoding="utf-8")
        assert "you'll need your chat recovery code" in page, name
    crypto = (SRC / "crypto.js").read_text(encoding="utf-8")
    assert 'window.tr("Messages this browser could already read will come back here.")' in crypto
    assert "keptStores" not in crypto
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert "keptStores" not in index and "keptStores" not in (SRC / "harvest.js").read_text(encoding="utf-8")
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    row = next(line for line in guide.splitlines() if line.startswith("| **A reset by email, a recovery-code sign-in"))
    assert "Start over can't finish on this chat server" not in row and "Start over with the password" in row
    assert "kept until the harvest" in row
    assert "Start over can't finish" not in guide


def test_the_guide_names_every_way_a_kept_store_holding_keys_can_go_while_chat_is_left_locked():
    """sweepEmpty runs on every page, locked or not (the chat page before unlocking, the call window, a Space): a kept store it fails to
    open or read three times becomes `broken`, and token.js deletes a broken store 30 days later, keys and all. CLAUDE.md's "Left locked"
    sentence must not leave that out, nor say the import isn't held by the keeper."""
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    bullet = next(line for line in guide.splitlines() if line.startswith("- **The harvest and the backup delay**"))
    locked = bullet[bullet.index("**Left locked**"):]
    locked = locked[:locked.index(". ")]
    assert "only a key change, the account's deletion here, the two caps, and a store that failed to open or be read three times" in locked
    assert "`broken`, deleted 30 days later" in locked
    assert "**The import is held by the page's keeper** (`hold`, `keeper.holdFor`" in bullet
    # what the sentence describes is what the code does: the sweep counts failures, the third is broken, and a broken store goes
    harvest = (SRC / "harvest.js").read_text(encoding="utf-8")
    assert "export const MAX_FAILS = 3;" in harvest and 'e.s = "broken";' in harvest
    assert "BROKEN_KEEP" in (SRC / "token.js").read_text(encoding="utf-8")
    # a failure counts once a store a page load, and the guide says so
    assert "**at most once a store a page load** (`failedThisLoad`" in bullet and "failedThisLoad.add(entry.p);" in harvest


def test_what_the_guide_says_the_privacy_page_says_it_does():
    """CLAUDE.md's harvest bullet says the privacy page names both losses it can't cover, and the per-account cap that deletes kept stores
    whether harvested or not: each claim is held to a sentence of the page."""
    guide = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    bullet = next(line for line in guide.splitlines() if line.startswith("- **The harvest and the backup delay**"))
    page = (ROOT / "ruqqus" / "templates" / "help" / "privacy.html").read_text(encoding="utf-8")
    assert ("**Not covered** (the privacy page says so): keys held only by a browser that never comes back, and messages sent while the "
            "account had **no chat device at all**") in bullet
    assert "Messages that only a browser you never use chat on again could read can't be brought back anywhere else." in page
    assert "A message sent to you while your account had no chat device anywhere" in page
    assert "the privacy page names it" in bullet and "the databases of the six most recent are kept; beyond those, the oldest go" in page
