"""What keeper.js counts on from the installed matrix-js-sdk (the chat plan's 4.4 and 9.2): its fetchFn swaps the token and hands back the
401, and the SDK retries with the new one; a 401 nothing renewed is the SDK's Session.logged_out; a token renewed and refused again is
retried exactly once and then logged out (the keeper's 60-second rule, without which this loops); two requests refused at once share one
renewal; a fetch that throws is a ConnectionError, never a log-out; and a MemoryStore with no localStorage keeps nothing there. Runs the
real keeper.js under node against the SDK in ruqqus/assets/chat_src/node_modules (CI installs it: build step 4's node job)."""
import json
import shutil
import subprocess

import pytest

from chat_node_harness import IN_CI, SRC, node, run

CHAT_SRC = SRC.parent
SDK = CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "index.js"
# Skipped locally without `npm ci` in chat_src, never under CI, where a missing install fails instead (9.5).
installed = pytest.mark.skipif(not SDK.exists() and not IN_CI,
                               reason="the chat bundle's node_modules are not installed (npm ci in chat_src)")

# The exact versions this client's private-member pins were checked against (chat plan, phase 2, Appendix B). Bumping one is one
# deliberate commit: the pin here and in package.json, `npm install`, then this file and the private-member pins again.
PINS = {"matrix-js-sdk": "43.0.0", "@matrix-org/matrix-sdk-crypto-wasm": "18.9.0", "esbuild": "0.24.2"}


def test_the_chat_bundles_dependencies_are_pinned_exactly_and_locked():
    package = json.loads((CHAT_SRC / "package.json").read_text(encoding="utf-8"))
    declared = {**package["dependencies"], **package["devDependencies"]}
    assert declared == PINS, "exact versions only: no ^ or ~"
    lock = json.loads((CHAT_SRC / "package-lock.json").read_text(encoding="utf-8"))
    assert {**lock["packages"][""]["dependencies"], **lock["packages"][""]["devDependencies"]} == PINS
    for name, version in PINS.items():
        assert lock["packages"]["node_modules/" + name]["version"] == version, name
    # one crypto-wasm in the whole tree: the copy the harvest imports is the one the SDK uses
    copies = [k for k in lock["packages"] if k.endswith("node_modules/@matrix-org/matrix-sdk-crypto-wasm")]
    assert copies == ["node_modules/@matrix-org/matrix-sdk-crypto-wasm"]
    ignore = (CHAT_SRC.parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "!ruqqus/assets/chat_src/package-lock.json" in ignore.split("package-lock.json\n", 1)[1], "tracked, after the blanket rule"
    docker = (CHAT_SRC.parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY ruqqus/assets/chat_src/package.json ruqqus/assets/chat_src/package-lock.json ./\nRUN npm ci\n" in docker
    assert "npm install" not in docker
    ci = (CHAT_SRC.parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    python_job = ci.split("\n  python:", 1)[1].split("\n  postgres:", 1)[0]
    assert "cache-dependency-path: ruqqus/assets/chat_src/package-lock.json" in python_job
    assert python_job.index("run: npm ci") < python_job.index("run: pytest -q")
    assert "working-directory: ruqqus/assets/chat_src" in python_job


@installed
def test_the_installed_versions_are_the_pinned_ones_and_there_is_one_crypto_wasm():
    for name, version in PINS.items():
        got = json.loads((CHAT_SRC / "node_modules" / name / "package.json").read_text(encoding="utf-8"))["version"]
        assert got == version, name
    npm = shutil.which("npm")
    assert npm or not IN_CI, "npm is missing"
    if not npm:
        pytest.skip("npm is not installed")
    out = subprocess.run([npm, "ls", "@matrix-org/matrix-sdk-crypto-wasm", "--all", "--json"], cwd=CHAT_SRC,
                         capture_output=True, text=True, timeout=120)
    found = set()

    def walk(node_):
        for name, dep in (node_.get("dependencies") or {}).items():
            if name == "@matrix-org/matrix-sdk-crypto-wasm":
                found.add(dep.get("version"))
            walk(dep)
    walk(json.loads(out.stdout))
    assert found == {"18.9.0"}, found

CONTRACT = r"""
const say = console.log.bind(console);
for (const name of ["debug", "info", "warn", "error", "trace"]) console[name] = () => {};
console.log = (...args) => { if (typeof args[0] === "string" && args[0].startsWith('{"sent"')) say(...args); };
const sdk = await import(__SDK__);
const K = await import(SRC + "/keeper.js");
const SO = await import(SRC + "/signed_out.js");
globalThis.window = { tr: (t) => t, location: { origin: "https://site", search: "", reload() {} }, addEventListener() {} };
window.parent = window;
const ui = { status() {}, stop() {}, banner() {}, notice() {} };
const hsSeen = [];
let hsAnswer = () => new Response("{}", { status: 200 });
const hsFetch = async (input, init) => {
  const auth = (init && init.headers && init.headers.Authorization) || "";
  hsSeen.push(auth.replace("Bearer ", ""));
  return hsAnswer(auth.replace("Bearer ", ""));
};
const refused = () => new Response(JSON.stringify({ errcode: "M_UNKNOWN_TOKEN", error: "Unknown token" }),
                                   { status: 401, headers: { "Content-Type": "application/json" } });
const ok = () => new Response(JSON.stringify({ user_id: "@ruqqus_1:s", device_id: "UQNEW0000000" }),
                              { status: 200, headers: { "Content-Type": "application/json" } });
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", { access_token: "tok-1" }) });
const session = await T.ensureSession({ formkey: "f", mxid: "@ruqqus_1:s" }, "chat");
sent.length = 0;
let clock = Date.now();
const { client, keeper } = K.makeClient(sdk, session, {}, {
  ui, role: "top", fetch: hsFetch, now: () => clock, sleep: async (ms) => { clock += ms; },
});
let loggedOut = 0;
client.on(sdk.HttpApiEvent.SessionLoggedOut, () => { loggedOut += 1; });
const renewals = () => sent.filter((r) => r.url === "/api/chat/token" && r.body.mode === "renew").length;
const outcome = async (p) => { try { await p; return "ok"; } catch (e) { return e.constructor.name + ":" + (e.errcode || ""); } };
"""


def contract(scenario):
    return run(CONTRACT.replace("__SDK__", repr(SDK.as_uri())) + scenario)


@node
@installed
def test_a_swapped_token_is_retried_and_nobody_is_logged_out():
    got = contract("""
hsAnswer = (token) => (token === "tok-1" ? refused() : ok());
serve = async () => ({ body: answer("@ruqqus_1:s", "UQNEW0000000", { access_token: "tok-2" }) });
const result = await outcome(client.whoami());
report({ result, hsSeen, renewals: renewals(), loggedOut, now: client.getAccessToken() });
""")
    assert got["result"] == "ok" and got["hsSeen"] == ["tok-1", "tok-2"] and got["renewals"] == 1
    assert got["loggedOut"] == 0 and got["now"] == "tok-2"


@node
@installed
def test_no_swap_is_the_sdks_log_out():
    got = contract("""
hsAnswer = () => refused();
serve = async () => ({ status: 409, body: { device_gone: true, reason: "password" } });
const result = await outcome(client.whoami());
report({ result, hsSeen, renewals: renewals(), loggedOut, reason: keeper.reason });
""")
    assert got["result"] == "MatrixError:M_UNKNOWN_TOKEN" and got["hsSeen"] == ["tok-1"] and got["renewals"] == 1
    assert got["loggedOut"] == 1 and got["reason"] == "removed"


@node
@installed
def test_a_renewed_token_refused_too_is_retried_once_then_logged_out_never_looped():
    got = contract("""
hsAnswer = () => refused();
let n = 1;
serve = async () => { n += 1; return { body: answer("@ruqqus_1:s", "UQNEW0000000", { access_token: "tok-" + n }) }; };
const result = await outcome(client.whoami());
report({ result, hsSeen, renewals: renewals(), loggedOut, reason: keeper.reason });
""")
    assert got["hsSeen"] == ["tok-1", "tok-2"] and got["renewals"] == 1
    assert got["loggedOut"] == 1 and got["reason"] == "unusable" and got["result"] == "MatrixError:M_UNKNOWN_TOKEN"


@node
@installed
def test_two_requests_refused_together_share_one_renewal_and_are_both_retried():
    got = contract("""
hsAnswer = (token) => (token === "tok-1" ? refused() : ok());
serve = async () => { await new Promise((r) => setTimeout(r, 30)); return { body: answer("@ruqqus_1:s", "UQNEW0000000", { access_token: "tok-2" }) }; };
const results = await Promise.all([outcome(client.whoami()), outcome(client.whoami())]);
report({ results, hsSeen, renewals: renewals(), loggedOut });
""")
    assert got["results"] == ["ok", "ok"] and got["renewals"] == 1 and got["loggedOut"] == 0
    assert sorted(got["hsSeen"]) == ["tok-1", "tok-1", "tok-2", "tok-2"]


@node
@installed
def test_two_requests_truly_in_flight_together_both_refused_share_one_renewal():
    """(D), gap 23: the homeserver holds both refusals until BOTH requests have reached it, so neither 401 can be answered before the
    other request was sent with the old token; then the second 401 lands while the first one's renewal is still out. One renewal POST,
    both retried with the new token, and no log-out."""
    got = contract("""
const events = [];
let inFlight = 0, release, releasedBy = null;
const both = new Promise((r) => { release = r; });
setTimeout(() => { releasedBy = releasedBy || "timeout"; release(); }, 2000);
hsAnswer = async (token) => {
  if (token !== "tok-1") return ok();
  inFlight += 1;
  events.push("in:" + inFlight);
  if (inFlight === 2) { releasedBy = releasedBy || "both"; release(); }
  await both;
  events.push("401");
  return refused();
};
serve = async () => {
  events.push("renew:start");
  await new Promise((r) => setTimeout(r, 50));
  events.push("renew:end");
  return { body: answer("@ruqqus_1:s", "UQNEW0000000", { access_token: "tok-2" }) };
};
const results = await Promise.all([outcome(client.whoami()), outcome(client.whoami())]);
report({ results, hsSeen, renewals: renewals(), loggedOut, events, releasedBy, now: client.getAccessToken() });
""")
    assert got["releasedBy"] == "both", "both requests reached the homeserver with the old token before either was refused"
    assert got["events"][:3] == ["in:1", "in:2", "401"]
    assert got["events"].count("renew:start") == 1 and got["renewals"] == 1
    # the second refusal arrives while the one renewal is still out, and waits for it instead of starting another
    assert got["events"].index("401", got["events"].index("401") + 1) < got["events"].index("renew:end")
    assert got["results"] == ["ok", "ok"] and got["loggedOut"] == 0 and got["now"] == "tok-2"
    assert got["hsSeen"] == ["tok-1", "tok-1", "tok-2", "tok-2"]


@node
@installed
def test_a_fetch_that_throws_a_type_error_is_a_connection_error_and_never_a_renewal_or_log_out():
    """(E), gap 23: the network failing under the client (fetch rejects with a TypeError, as a browser's does offline) is the SDK's
    ConnectionError, which sync backs off from. The keeper asks the site nothing and the token stays."""
    got = contract("""
hsAnswer = () => { throw new TypeError("Failed to fetch"); };
const result = await outcome(client.whoami());
report({ result, loggedOut, renewals: renewals(), reason: keeper.reason, token: client.getAccessToken() });
""")
    assert got["result"].startswith("ConnectionError") and got["loggedOut"] == 0
    assert got["renewals"] == 0 and got["reason"] is None and got["token"] == "tok-1"


@node
@installed
def test_a_site_that_cant_answer_is_a_connection_error_not_a_log_out():
    got = contract("""
hsAnswer = () => refused();
serve = async () => ({ status: 503, body: { retry: true } });
const result = await outcome(client.whoami());
report({ result, loggedOut, reason: keeper.reason, token: client.getAccessToken() });
""")
    assert got["result"].startswith("ConnectionError") and got["loggedOut"] == 0
    assert got["reason"] is None and got["token"] == "tok-1"


@node
@installed
def test_the_event_name_and_the_memory_store():
    got = contract("""
const before = store.size;
const memory = new sdk.MemoryStore();
memory.setFilterIdByName("FILTER_SYNC_@ruqqus_1:s", "7");
const kept = memory.getFilterIdByName("FILTER_SYNC_@ruqqus_1:s");
report({ name: sdk.HttpApiEvent.SessionLoggedOut, mine: SO.SESSION_LOGGED_OUT, before, after: store.size,
         keys: [...store.keys()].filter((k) => k.startsWith("mxjssdk")), kept });
""")
    assert got["name"] == got["mine"] == "Session.logged_out"
    assert got["keys"] == [] and got["after"] == got["before"]


@installed
def test_the_cross_signing_upload_is_the_path_and_body_the_keeper_sends_through_the_site():
    """keeper.js sends exactly one request to the site for a Start over: the SDK's POST to KEY_UPLOAD_PATH, whose body is the key upload,
    with `auth` only when the UIA callback passed one (OutgoingRequestProcessor.makeRequestWithUIA). If the SDK moves it, the intercept
    stops firing and Start over fails as it once did, safely: this says so first."""
    processor = (CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "rust-crypto" / "OutgoingRequestProcessor.js").read_text(encoding="utf-8")
    keeper = (CHAT_SRC / "src" / "keeper.js").read_text(encoding="utf-8")
    path = '"/_matrix/client/v3/keys/device_signing/upload"'
    assert f"makeRequestWithUIA(Method.Post, {path}" in processor
    assert f"export const KEY_UPLOAD_PATH = {path};" in keeper
    reset = (CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "rust-crypto" / "rust-crypto.js").read_text(encoding="utf-8")
    body = reset[reset.index("async resetEncryption(authUploadDeviceSigningKeys) {"):]
    body = body[:body.index("\n  }\n")]
    # one upload of new cross-signing keys, then a new key backup: crypto.js makes only the secret storage after it
    assert body.count("bootstrapCrossSigning(") == 1 and "resetKeyBackup()" in body


@installed
def test_what_the_rescue_calls_is_in_the_installed_sdk_and_wasm():
    """rescue.js and crypto.js offerRescue lean on these: the raw machine's secrets export and identity, and the SDK's import, signing and
    backup info. bootstrapCrossSigning is NOT among them: with the keys on the device it still reads the old secret storage first."""
    wasm = (CHAT_SRC / "node_modules" / "@matrix-org" / "matrix-sdk-crypto-wasm" / "pkg" / "matrix_sdk_crypto_wasm.d.ts").read_text(encoding="utf-8")
    for member in ("exportSecretsBundle(): Promise<Promise<SecretsBundle>>;", "getIdentity(user_id: UserId)", "isVerified(): boolean;",
                   "readonly masterKey: string;"):
        assert member in wasm, member
    api = (CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "crypto-api" / "index.d.ts").read_text(encoding="utf-8")
    for member in ("importSecretsBundle?(secrets: SecretsBundleJson): Promise<void>;", "crossSignDevice(deviceId: string): Promise<void>;",
                   "isKeyBackupTrusted(info: KeyBackupInfo): Promise<BackupTrustInfo>;",
                   "getCrossSigningKeyId(type?: CrossSigningKey): Promise<string | null>;"):
        assert member in api, member
    identity = (CHAT_SRC / "node_modules" / "matrix-js-sdk" / "lib" / "rust-crypto" / "CrossSigningIdentity.js").read_text(encoding="utf-8")
    assert 'await this.secretStorage.get("m.cross_signing.master")' in identity       # why the rescue signs with crossSignDevice
