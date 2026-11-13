"""The rescue (rescue.js; the chat plan's phase 3): a store this browser kept of an earlier chat device of the account, opened raw with the
real crypto wasm, gives its secrets only when it holds all three private cross-signing keys and they are the account's keys the chat server
lists NOW (a Start over anywhere since leaves nothing to rescue); nothing is deleted or marked. Run under node with the harness's stand-ins
for the browser (tests/chat_node_harness.py); the crypto is the installed wasm, not a fake."""
import pytest

from chat_node_harness import IN_CI, SRC, node, run

CHAT_SRC = SRC.parent
WASM = CHAT_SRC / "node_modules" / "@matrix-org" / "matrix-sdk-crypto-wasm"
installed = pytest.mark.skipif(not WASM.exists() and not IN_CI,
                               reason="the chat bundle's node_modules are not installed (npm ci in chat_src)")

RESCUE = r"""
const R = await import(SRC + "/rescue.js");
const w = await import(%r);
const U = "@ruqqus_1:s";
// a real machine per kept store, standing in for StoreHandle.openWithKey + OlmMachine.initFromStore
const machines = new Map();
async function real(d, { crossSigning = true, backup = false } = {}) {
  const m = await w.OlmMachine.initialize(new w.UserId(U), new w.DeviceId(d));
  if (crossSigning) await m.bootstrapCrossSigning(true);
  if (backup) await m.saveBackupDecryptionKey(w.BackupDecryptionKey.createRandomKey(), "7");
  machines.set(d, m);
  return m;
}
async function masterOf(m) {
  const id = await m.getIdentity(new w.UserId(U));
  return R.keyOf(JSON.parse(id.masterKey));
}
const opened = [];
const open = async (e, key) => { opened.push({ d: e.d, lock: busy.has("uqqus-store:" + e.p) }); return machines.get(e.d); };
const session = { user_id: U, device_id: "UQCUR0000000", boot: { formkey: "f", mxid: U } };
serve = async (r) => {
  if (r.url !== "/api/chat/store_keys") return { status: 404 };
  const keys = {};
  for (const d of r.body.device_ids.split(",")) keys[d] = { key: keyOf(d), kid: "kid1" };
  return { body: { keys } };
};
const current = entry(U, "UQCUR0000000", "chat", { x: 1 });
const kept = (d, s = "removed", used = 1000) => entry(U, d, "chat", { s, used });
""" % (WASM / "node.mjs").as_uri()


def rescue(scenario):
    return run(RESCUE + scenario)


@node
@installed
def test_a_kept_store_holding_the_accounts_current_keys_gives_them_and_nothing_is_touched():
    got = rescue("""
const old = await real("UQOLD0000000", { backup: true });
setIndex(U, [current, kept("UQOLD0000000")]);
const master = await masterOf(old);                     // read first: the rescue closes the machine it opened
const found = await R.findRescue(session, { serverMaster: async () => master, open });
// the bundle opens in a new device as the same identity
const neu = await w.OlmMachine.initialize(new w.UserId(U), new w.DeviceId("UQNEW0000000"));
await neu.importSecretsBundle(w.SecretsBundle.from_json(found.bundle));
const status = await neu.crossSigningStatus();
report({ d: found.d, matched: found.master === master, parts: Object.keys(found.bundle).sort(), same: (await masterOf(neu)) === master,
         all: status.hasMaster && status.hasSelfSigning && status.hasUserSigning, opened, master });
""")
    assert got["d"] == "UQOLD0000000" and got["matched"] is True and got["parts"] == ["backup", "cross_signing"]
    assert got["same"] is True and got["all"] is True
    assert got["opened"] == [{"d": "UQOLD0000000", "lock": True}]           # under the store's own lock
    assert got["deleted"] == [] and [e["s"] for e in got["index"]["entries"]] == ["in", "removed"]


@node
@installed
def test_keys_a_start_over_has_replaced_since_are_never_offered():
    got = rescue("""
await real("UQOLD0000000");
const now = await real("UQELSE000000");                 // a Start over elsewhere: the server lists another master key
setIndex(U, [current, kept("UQOLD0000000")]);
report({ found: await R.findRescue(session, { serverMaster: async () => masterOf(now), open }) });
""")
    assert got["found"] is None


@node
@installed
def test_a_store_without_the_private_keys_is_passed_over_for_the_next():
    got = rescue("""
const master = await masterOf(await real("UQOLD1000000"));
await real("UQNOKEYS0000", { crossSigning: false });
setIndex(U, [current, kept("UQNOKEYS0000", "removed", 2000), kept("UQOLD1000000", "removed", 1000), kept("UQLOGOUT0000", "out", 3000)]);
const found = await R.findRescue(session, { serverMaster: async () => master, open });
report({ d: found && found.d, backup: found && (found.bundle.backup || null), order: opened.map((o) => o.d) });
""")
    # newest first, signed-out (`removed`) stores only (the harvest takes those); one with no backup key still gives its cross-signing keys
    assert got["order"] == ["UQNOKEYS0000", "UQOLD1000000"]
    assert got["d"] == "UQOLD1000000" and got["backup"] is None


@node
@installed
def test_this_pages_own_device_another_accounts_and_finished_stores_are_never_looked_at():
    got = rescue("""
const m = await real("UQOLD0000000");
setIndex(U, [current, entry(U, "UQGONE000000", "chat", { s: "removed", taken: 1 }), entry("@ruqqus_2:s", "UQTHEM000000", "chat", { s: "removed" })]);
report({ found: await R.findRescue(session, { serverMaster: async () => masterOf(m), open }), opened });
""")
    assert got["found"] is None and got["opened"] == []


@node
@installed
def test_nothing_is_asked_when_the_server_lists_no_keys_or_cant_be_asked():
    got = rescue("""
await real("UQOLD0000000");
setIndex(U, [current, kept("UQOLD0000000")]);
const none = await R.findRescue(session, { serverMaster: async () => null, open });
const down = await R.findRescue(session, { serverMaster: async () => { throw new Error("offline"); }, open });
report({ none, down, opened, asked: sent.length });
""")
    assert got["none"] is None and got["down"] is None and got["opened"] == [] and got["asked"] == 0


def test_the_master_key_is_read_from_the_chat_server_by_keys_query():
    text = (SRC / "rescue.js").read_text(encoding="utf-8")
    assert 'client.http.authedRequest("POST", "/keys/query", undefined, { device_keys: { [mxid]: [] } })' in text
    assert "deleteStore" not in text and "withIndex" not in text and "updateRemoved" not in text   # it never deletes or marks a store
