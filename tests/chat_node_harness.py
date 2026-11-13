"""Runs the chat client's own modules (ruqqus/assets/chat_src/src: token.js, seal.js) under node, against stand-ins for what a
browser gives them: localStorage and sessionStorage, IndexedDB (a set of database names), Web Locks (one holder at a time, `ifAvailable`,
and a record of what was held when), and fetch (a scenario's `serve` answers the site). Each test writes its scenario in JavaScript after
PRELUDE and prints what it found with `report({...})`. Used by tests/test_chat_index.py, tests/test_chat_session.py and
tests/test_chat_seal.py."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ruqqus" / "assets" / "chat_src" / "src"

# Skipped where node isn't installed, but never under CI (the `CI` variable, which GitHub Actions sets): there a missing node fails the
# test instead, so the chat client's tests can't be skipped silently (chat plan, phase 2, 9.5).
IN_CI = bool(os.environ.get("CI"))
node = pytest.mark.skipif(shutil.which("node") is None and not IN_CI, reason="node is not installed")

A, B = "@ruqqus_1:s", "@ruqqus_2:s"
OLD, NEW = "UQOLD0000000", "UQNEW0000000"

PRELUDE = r"""
const SRC = __SRC__;
const seal = await import(SRC + "/seal.js");
const store = new Map(Object.entries(__LOCAL__));
const tab = new Map();
const writes = [];
let storageBroken = __BROKEN__;
function shelf(map, record) {
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { if (storageBroken) throw new Error("QuotaExceededError"); map.set(k, String(v)); if (record) writes.push(k); },
    removeItem: (k) => { map.delete(k); },
    key: (i) => [...map.keys()][i] ?? null,
    get length() { return map.size; },
  };
}
globalThis.localStorage = shelf(store, true);
globalThis.sessionStorage = shelf(tab, false);
const dbs = new Set(__DBS__);
const deleted = [];
const failing = new Set(__FAILING__);             // databases whose delete raises its error event (they stay)
globalThis.indexedDB = {
  deleteDatabase(name) {
    const request = {};
    if (failing.has(name)) {
      setTimeout(() => { request.error = new Error("UnknownError"); request.onerror && request.onerror(); }, 0);
      return request;
    }
    deleted.push(name);
    dbs.delete(name);
    setTimeout(() => request.onsuccess && request.onsuccess(), 0);
    return request;
  },
  databases: async () => [...dbs].map((name) => ({ name })),
};
globalThis.window = { tr: (text) => text };

// Web Locks: one holder per name, others wait their turn; `ifAvailable` answers null at once when it is held
const busy = new Set(), queues = new Map(), lockLog = [], thieves = new Map();
const outside = new Set(__HELD__);                 // locks another page holds for the whole scenario
const LOCKS = {
  async request(name, options, work) {
    if (typeof options === "function") { work = options; options = {}; }
    if (options.steal) {
      // `steal` (only ever used for uqqus-matrix): granted at once; the holder's request rejects, as a browser's does (AbortError)
      const before = thieves.get(name);
      let lose;
      const lost = new Promise((_, reject) => { lose = reject; });
      thieves.set(name, { lose });
      lockLog.push((before ? "steal " : "take ") + name);
      busy.add(name);
      if (before) before.lose(new Error("AbortError"));
      return Promise.race([Promise.resolve().then(() => work({ name })), lost]);
    }
    if (options.ifAvailable && (busy.has(name) || outside.has(name))) return work(null);
    const before = queues.get(name) || Promise.resolve();
    let release;
    const mine = new Promise((resolve) => { release = resolve; });
    queues.set(name, before.then(() => mine));
    await before;
    busy.add(name);
    lockLog.push("take " + name);
    try {
      return await work({ name });
    } finally {
      busy.delete(name);
      lockLog.push("drop " + name);
      release();
    }
  },
};
Object.defineProperty(globalThis, "navigator", { value: __LOCKS__ ? { locks: LOCKS } : {}, configurable: true });

// the site: every request is recorded with whether the session lock was held while it was out
const sent = [];
let serve = async () => ({ status: 500 });
globalThis.fetch = async (url, init = {}) => {
  const body = init.body ? Object.fromEntries(new URLSearchParams(init.body.toString())) : null;
  const request = { url, method: init.method || "GET", body, redirect: init.redirect || null, locked: busy.has("uqqus-chat-session") };
  sent.push(request);
  const a = (await serve(request)) || {};
  const status = a.status ?? 200;
  request.lockedAfter = busy.has("uqqus-chat-session");
  return { status, ok: status >= 200 && status < 300, type: a.type || "basic", json: async () => (a.body === undefined ? {} : a.body) };
};

const keyOf = (d) => Buffer.from(("key-" + d).padEnd(32, "#").slice(0, 32)).toString("base64url");
const WAIT = Buffer.from("w".repeat(32)).toString("base64url");
function answer(u, d, options = {}) {
  return {
    homeserver_url: "https://m", user_id: u, device_id: d, kind: options.kind || "chat", kid: options.kid || "kid1",
    storage_key: keyOf(d), waiting_key: WAIT, access_token: "access_token" in options ? options.access_token : "tok-" + d,
    new_device: false, ...(options.extra || {}),
  };
}
async function boxFor(u, d, k, t) {
  const tk = await seal.tokenKey(seal.b64urlDecode(keyOf(d)));
  return seal.seal(tk, seal.tokenAad({ u, d, k }), { t });
}
function entry(u, d, k, fields = {}) {
  const local = u.slice(1).split(":")[0];
  return { u, d, k, p: `uqqus-${k}-${local}-${d}`, kid: "kid1", hs: "https://m", s: "in", x: 0, ed: null, made: 1, used: Date.now(),
           gen: 1, exp: null, life: null, why: null, fails: 0, bad: 0, empty: 0, ...fields };
}
function setIndex(owner, entries, epoch = 0) {
  store.set("uqqus_chat_devices", JSON.stringify({ v: 2, owner, epoch, entries }));
}
function getIndex() {
  try { return JSON.parse(store.get("uqqus_chat_devices") || "null"); } catch (e) { return "unreadable"; }
}
function storesOf(prefix) { return [prefix + "::matrix-sdk-crypto", prefix + "::matrix-sdk-crypto-meta"]; }
function report(extra = {}) {
  console.log(JSON.stringify({ sent, deleted, writes, lockLog, stored: Object.fromEntries(store), index: getIndex(), dbs: [...dbs], ...extra }));
}
async function attempt(promise) {
  try { return { value: await promise }; } catch (e) { return { thrown: e.constructor.name, message: e.message }; }
}
const T = await import(SRC + "/token.js");
"""


def run(scenario, local=None, dbs=(), locks=True, held=(), broken=False, failing=()):
    """`scenario` runs after PRELUDE (top-level await is fine) and must call report(). `local`: what localStorage holds at the start."""
    prelude = (PRELUDE.replace("__SRC__", json.dumps(SRC.as_uri()))
               .replace("__LOCAL__", json.dumps(local or {}))
               .replace("__DBS__", json.dumps(list(dbs)))
               .replace("__HELD__", json.dumps(list(held)))
               .replace("__FAILING__", json.dumps(list(failing)))
               .replace("__LOCKS__", "true" if locks else "false")
               .replace("__BROKEN__", "true" if broken else "false"))
    out = subprocess.run(["node", "--input-type=module", "-e", prelude + scenario], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def stores(prefix):
    return [prefix + "::matrix-sdk-crypto", prefix + "::matrix-sdk-crypto-meta"]


def prefix(u, d, k="chat"):
    return f"uqqus-{k}-{u[1:].split(':')[0]}-{d}"
