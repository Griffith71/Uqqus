"""Every site POST of the chat page, the call window and a Space page (site.js sitePost; the chat plan's 5.3, row 807, and gap 9), run
under node with tests/chat_node_harness.py's stand-ins: a form key a credential event elsewhere made stale is refreshed once, in place,
and the POST sent again, so a call's heartbeat or a Space's goes on with no reload; only this page's own account's key is ever taken. And
the pages use it for every POST they make (a beacon at `pagehide` reads the key as it is then)."""
import re

from chat_node_harness import SRC, node, run

SITE = 'const S = await import(SRC + "/site.js");\n'


@node
def test_a_stale_form_key_is_refreshed_once_in_place_and_the_post_sent_again():
    for refusal in ('{ status: 401 }', '{ status: 0, type: "opaqueredirect" }', '{ status: 302 }'):
        got = run(SITE + """
serve = async (r) => {
  if (r.url === "/api/chat/formkey") return { body: { formkey: "fresh", mxid: "@ruqqus_1:s" } };
  return r.body.formkey === "fresh" ? { body: { in_call: true } } : __REFUSAL__;
};
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
const resp = await S.sitePost(boot, "/api/calls/heartbeat", { room: "!r", device_id: "D", gone: undefined, none: null });
const later = await S.sitePost(boot, "/api/calls/heartbeat", { room: "!r" });
report({ status: resp.status, data: await resp.json(), laterStatus: later.status, formkey: boot.formkey });
""".replace("__REFUSAL__", refusal))
        urls = [r["url"] for r in got["sent"]]
        assert urls == ["/api/calls/heartbeat", "/api/chat/formkey", "/api/calls/heartbeat", "/api/calls/heartbeat"], refusal
        assert got["status"] == 200 and got["data"] == {"in_call": True} and got["formkey"] == "fresh", refusal
        assert got["sent"][2]["body"] == {"formkey": "fresh", "room": "!r", "device_id": "D"}, refusal     # nothing undefined sent
        assert all(r["redirect"] == "manual" for r in got["sent"]), refusal
        assert got["sent"][3]["body"]["formkey"] == "fresh" and got["laterStatus"] == 200, refusal         # later POSTs use the new key


@node
def test_a_sign_in_that_is_over_or_another_accounts_comes_back_as_the_refusal():
    got = run(SITE + """
const out = {};
serve = async (r) => (r.url === "/api/chat/formkey" ? { status: 0, type: "opaqueredirect" } : { status: 401 });
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
out.over = (await S.sitePost(boot, "/api/spaces/1/heartbeat", {})).status;
serve = async (r) => (r.url === "/api/chat/formkey" ? { body: { formkey: "theirs", mxid: "@ruqqus_2:s" } } : { status: 401 });
out.other = (await S.sitePost(boot, "/api/spaces/1/heartbeat", {})).status;
out.formkey = boot.formkey;
sent.length = 0;
out.plain = (await S.sitePost("a-plain-key", "/api/chat/check_password", { password: "p" })).status;
report(out);
""")
    assert got["over"] == 401 and got["other"] == 401 and got["formkey"] == "stale"      # never another account's key
    assert got["plain"] == 401 and [r["url"] for r in got["sent"]] == ["/api/chat/check_password"]    # nothing to compare: not asked


@node
def test_a_form_key_that_couldnt_be_asked_for_now_comes_back_as_unavailable_never_as_the_refusal():
    """A 429 (30 a minute per member, shared by every page refreshing after one event), a 5xx while the app restarts, an answer that
    isn't the route's, a timeout or a network error: the sign-in isn't over, so a call's or a Space's heartbeat reads a 503 (try again),
    never the 401 that ends it, and the next POST asks for the key again."""
    for failure in ('({ status: 429 })', '({ status: 500 })', '({ status: 502, body: "<html>" })', '({ body: { formkey: "" } })',
                    '(() => { throw new Error("TimeoutError"); })()'):
        got = run(SITE + """
let keys = 0;
serve = async (r) => {
  if (r.url === "/api/chat/formkey") { keys += 1; return keys === 1 ? __FAILURE__ : { body: { formkey: "fresh", mxid: "@ruqqus_1:s" } }; }
  return r.body.formkey === "fresh" ? { body: { in_call: true } } : { status: 401 };
};
const boot = { formkey: "stale", mxid: "@ruqqus_1:s" };
const first = await S.sitePost(boot, "/api/calls/heartbeat", { room: "!r" });
const out = { status: first.status, ok: first.ok, data: await first.json(), formkey: boot.formkey };
const again = await S.sitePost(boot, "/api/calls/heartbeat", { room: "!r" });
out.again = again.status;
out.after = boot.formkey;
report(out);
""".replace("__FAILURE__", failure))
        assert got["status"] == 503 and got["ok"] is False and got["data"] == {} and got["formkey"] == "stale", failure
        assert got["again"] == 200 and got["after"] == "fresh", failure
        assert [r["url"] for r in got["sent"]] == ["/api/calls/heartbeat", "/api/chat/formkey", "/api/calls/heartbeat",
                                                    "/api/chat/formkey", "/api/calls/heartbeat"], failure


@node
def test_what_asking_for_the_key_found():
    got = run(SITE + """
const out = {};
const cases = {
  refreshed: { body: { formkey: "k2", mxid: "@ruqqus_1:s" } },
  redirect: { status: 0, type: "opaqueredirect" }, unauthorised: { status: 401 }, found: { status: 302 },
  other: { body: { formkey: "theirs", mxid: "@ruqqus_2:s" } },
  limited: { status: 429 }, down: { status: 503 }, broken: { status: 500 }, empty: { body: {} },
};
for (const [name, answer] of Object.entries(cases)) {
  serve = async () => answer;
  out[name] = await S.refreshFormkey({ formkey: "k", mxid: "@ruqqus_1:s" });
}
serve = async () => { throw new Error("offline"); };
out.offline = await S.refreshFormkey({ formkey: "k", mxid: "@ruqqus_1:s" });
out.plain = await S.refreshFormkey("k");
report(out);
""")
    assert got["refreshed"] == "refreshed"
    assert all(got[n] == "over" for n in ("redirect", "unauthorised", "found", "other", "plain")), got
    assert all(got[n] == "transient" for n in ("limited", "down", "broken", "empty", "offline")), got


@node
def test_a_refusal_that_isnt_the_form_key_is_never_retried():
    got = run(SITE + """
serve = async () => ({ status: 403, body: { error: "no" } });
const resp = await S.sitePost({ formkey: "f", mxid: "@ruqqus_1:s" }, "/api/chat/groups/1/add", { usernames: "x" });
report({ status: resp.status });
""")
    assert got["status"] == 403 and [r["url"] for r in got["sent"]] == ["/api/chat/groups/1/add"]


def read(name):
    return (SRC / name).read_text(encoding="utf-8")


def test_the_pages_send_every_post_through_it():
    call, space = read("call.js"), read("space.js")
    assert "const resp = await sitePost(boot, `/api/calls/${action}`," in call
    assert "const resp = await sitePost(boot, `/api/spaces/${boot.spaceId}/${action}`," in space
    assert "const resp = await sitePost(boot, url, fields);" in space and "sitePost(boot, `/api/vote/post/" in space
    assert "const resp = await sitePost(site, url, extra || {});" in read("api.js")
    assert "const resp = await sitePost(site, url, fields);" in read("keylock.js")
    extras = read("chat_extras.js")
    assert "const resp = await sitePost(site, url, fields);" in extras and 'sitePost(boot, "/api/chat/start?json=1"' in extras
    # nothing posts with a copy of the key: only the leave beacons read it, as it is at `pagehide`
    for name in ("index.js", "call.js", "space.js", "api.js", "keylock.js", "chat_extras.js", "groups.js", "crypto.js", "guild_channels.js"):
        text = read(name)
        assert not re.search(r'method:\s*"POST"', text), name
        assert text.count("boot.formkey") == (1 if name in ("call.js", "space.js") else 0), name
    assert 'form.set("formkey", boot.formkey);' in call and 'form.set("formkey", boot.formkey);' in space
    # postSite (the token POSTs) and sitePost refresh through the same refreshFormkey, which compares the account
    token = read("token.js")
    assert 'import { REFRESHED, SITE_TIMEOUT, TRANSIENT, refreshFormkey, refusedKey, timeoutSignal } from "./site.js";' in token
    assert "const refreshed = attempt === 0 ? await refreshFormkey(boot) : null;" in token
    assert "if (refreshed === TRANSIENT) return { status: 0, body: {} };" in token
    assert "if (data.mxid !== holder.mxid) return OVER;" in read("site.js")
