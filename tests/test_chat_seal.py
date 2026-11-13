"""seal.js (the chat plan's phase 2, section 3.1), run under node: what this browser keeps of a chat sign-in opens only with the key the
site gives a signed-in page of that account, and only in the entry it was sealed for."""
import json

from chat_node_harness import ROOT, node, run

VECTORS = json.loads((ROOT / "tests" / "data" / "chat_store_key_vectors.json").read_text(encoding="utf-8"))["vectors"]


@node
def test_a_box_opens_with_its_key_in_its_own_entry_only():
    got = run("""
const key = await seal.tokenKey(seal.b64urlDecode(keyOf("UQOLD0000000")));
const other = await seal.tokenKey(seal.b64urlDecode(keyOf("UQNEW0000000")));
const e = { u: "@ruqqus_1:s", d: "UQOLD0000000", k: "chat" };
const box = await seal.seal(key, seal.tokenAad(e), { t: "secret-token-text" });
const again = await seal.seal(key, seal.tokenAad(e), { t: "secret-token-text" });
const opened = await seal.unseal(key, seal.tokenAad(e), box);
report({
  opened,
  otherUser: await seal.unseal(key, seal.tokenAad({ ...e, u: "@ruqqus_2:s" }), box),
  otherDevice: await seal.unseal(key, seal.tokenAad({ ...e, d: "UQNEW0000000" }), box),
  otherKind: await seal.unseal(key, seal.tokenAad({ ...e, k: "call" }), box),
  wrongKey: await seal.unseal(other, seal.tokenAad(e), box),
  waitingAad: await seal.unseal(key, seal.waitingAad(e.u), box),
  cut: await seal.unseal(key, seal.tokenAad(e), { iv: box.iv, ct: box.ct.slice(0, -4) }),
  notABox: await seal.unseal(key, seal.tokenAad(e), "nonsense"),
  noKey: await seal.unseal(null, seal.tokenAad(e), box),
  ivs: [box.iv, again.iv],
  text: JSON.stringify(box),
  ivBytes: seal.b64urlDecode(box.iv).length,
});
""")
    assert got["opened"] == {"t": "secret-token-text"}
    for case in ("otherUser", "otherDevice", "otherKind", "wrongKey", "waitingAad", "cut", "notABox", "noKey"):
        assert got[case] is None, case
    assert got["ivs"][0] != got["ivs"][1] and got["ivBytes"] == 12
    assert "secret-token-text" not in got["text"] and "token" not in got["text"]


@node
def test_the_token_key_and_the_waiting_key_are_derived_apart():
    got = run("""
const raw = seal.b64urlDecode(keyOf("UQOLD0000000"));
const tk = await seal.tokenKey(raw);
const wk = await seal.waitingKey(raw);
const box = await seal.seal(tk, "v2|same", { t: "x" });
report({ crossed: await seal.unseal(wk, "v2|same", box), extractable: tk.extractable, usages: tk.usages });
""")
    assert got["crossed"] is None                                 # one raw key, two HKDF labels: never the same AES key
    assert got["extractable"] is False and sorted(got["usages"]) == ["decrypt", "encrypt"]


@node
def test_the_sites_vector_keys_are_32_bytes_and_seal():
    got = run("""
const vectors = __VECTORS__;
const out = [];
for (const v of vectors) {
  const raw = seal.b64urlDecode(v.key);
  const waiting = seal.b64urlDecode(v.waiting);
  const tk = await seal.tokenKey(raw);
  const wk = await seal.waitingKey(waiting);
  const e = { u: v.mxid, d: v.device, k: "chat" };
  const box = await seal.seal(tk, seal.tokenAad(e), { t: "t" });
  const wbox = await seal.seal(wk, seal.waitingAad(v.mxid), { "!r": [] });
  out.push({ len: raw.length, wlen: waiting.length, back: seal.b64urlEncode(raw) === v.key,
             opened: await seal.unseal(tk, seal.tokenAad(e), box), wopened: await seal.unseal(wk, seal.waitingAad(v.mxid), wbox) });
}
report({ out });
""".replace("__VECTORS__", json.dumps(VECTORS)))
    assert len(got["out"]) == len(VECTORS) >= 3
    for one in got["out"]:
        assert one["len"] == 32 and one["wlen"] == 32 and one["back"]
        assert one["opened"] == {"t": "t"} and one["wopened"] == {"!r": []}


def test_seal_imports_nothing():
    source = (ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "seal.js").read_text(encoding="utf-8")
    assert "import " not in source and "localStorage" not in source
    assert '"uqqus chat token seal v1"' in source and '"uqqus chat waiting v1"' in source
    assert "`v2|token|${e.u}|${e.d}|${e.k}`" in source and "`v2|waiting|${u}`" in source
    assert 'name: "HKDF", hash: "SHA-256"' in source and "tagLength: 128" in source and "new Uint8Array(12)" in source
