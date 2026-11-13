"""A message written before this device has unlocked its encryption (src/send_gate.js, wired in src/index.js): it waits and goes, in the
order written, once chat has unlocked; it is never refused with "Unlock your messages first: reload the page..." and lost, which is
what a message typed while chat was still starting used to get. Left locked, what waits stays and the thread offers Unlock; the
Send-in-chat frame waits a little for the unlock instead of refusing at once. Also token.js markUnlocked (the entry's `x`, 2.2), and
Settings > Chat apps reading nothing from this browser's storage (3.6). Run under node (tests/chat_node_harness.py)."""
from pathlib import Path

from chat_node_harness import A, NEW, node, run

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ruqqus" / "assets" / "chat_src" / "src"

GATE = r"""
const G = await import(SRC + "/send_gate.js");
const realSetTimeout = setTimeout;
const tick = (ms = 5) => new Promise((resolve) => realSetTimeout(resolve, ms));
const delivered = [], changes = [];
let failNext = false, slow = 0;
const gate = G.makeSendGate({
  deliver: async (item) => {
    if (slow) await tick(slow);
    delivered.push(item.text);
    if (failNext) { failNext = false; throw new Error("send failed"); }
  },
  onChange: () => changes.push([gate.state, gate.pending()]),
});
"""


def gate(scenario):
    return run(GATE + scenario)


@node
def test_a_message_written_while_chat_is_starting_waits_and_goes_once_unlocked_in_order():
    got = gate("""
gate.submit({ roomId: "!a", text: "one" });
gate.submit({ roomId: "!b", text: "two" });
gate.submit({ roomId: "!a", text: "three" });
const waiting = { state: gate.state, all: gate.pending(), a: gate.pending("!a"), b: gate.pending("!b"), sent: [...delivered] };
await gate.ready();
report({ waiting, delivered, after: gate.pending(), state: gate.state, changes });
""")
    assert got["waiting"] == {"state": "starting", "all": 3, "a": 2, "b": 1, "sent": []}    # nothing sent, nothing dropped
    assert got["delivered"] == ["one", "two", "three"] and got["after"] == 0 and got["state"] == "ready"
    assert ["starting", 1] in got["changes"] and got["changes"][-1] == ["ready", 0]     # the thread's line follows the queue


@node
def test_once_unlocked_a_message_goes_at_once_and_one_written_during_the_flush_waits_its_turn():
    got = gate("""
slow = 20;
gate.submit({ roomId: "!a", text: "early" });
const flushing = gate.ready();
gate.submit({ roomId: "!a", text: "during" });         // written while "early" is still going out
await flushing;
await gate.submit({ roomId: "!a", text: "later" });
report({ delivered, after: gate.pending() });
""")
    assert got["delivered"] == ["early", "during", "later"] and got["after"] == 0


@node
def test_a_failed_send_is_the_threads_to_show_and_never_stops_the_ones_after_it():
    got = gate("""
failNext = true;
gate.submit({ roomId: "!a", text: "one" });
gate.submit({ roomId: "!a", text: "two" });
await gate.ready();
report({ delivered, after: gate.pending() });
""")
    assert got["delivered"] == ["one", "two"] and got["after"] == 0


@node
def test_left_locked_nothing_is_dropped_and_unlocking_again_sends_it():
    got = gate("""
gate.submit({ roomId: "!a", text: "one" });
gate.locked();
gate.submit({ roomId: "!a", text: "two" });            // written after the member left it locked: it waits too
const locked = { state: gate.state, pending: gate.pending(), sent: [...delivered], settled: await gate.settled() };
gate.starting();                                       // the thread's Unlock
const again = gate.settled();
const meanwhile = gate.state;
await gate.ready();
report({ locked, meanwhile, settledAgain: await again, delivered });
""")
    assert got["locked"] == {"state": "locked", "pending": 2, "sent": [], "settled": "locked"}
    assert got["meanwhile"] == "starting" and got["settledAgain"] == "ready"
    assert got["delivered"] == ["one", "two"]


@node
def test_settled_waits_for_the_unlock_and_ready_is_never_taken_back():
    got = gate("""
let heard = null;
gate.settled().then((s) => { heard = s; });
await tick();
const before = heard;
await gate.ready();
await tick();
gate.locked();                                         // a late answer can't lock a device that unlocked
gate.starting();
report({ before, heard, state: gate.state, now: await gate.settled() });
""")
    assert got["before"] is None and got["heard"] == "ready" and got["state"] == "ready" and got["now"] == "ready"


@node
def test_mark_unlocked_sets_x_once_on_the_chat_entry_and_writes_nothing_again():
    got = run(f"""
serve = async () => ({{ body: answer("{A}", "{NEW}") }});
const session = await T.ensureSession({{ formkey: "f", mxid: "{A}" }}, "chat");
const before = getIndex().entries.find((e) => e.d === "{NEW}");
writes.length = 0;
await T.markUnlocked(session);
const first = writes.length;
const gen = getIndex().entries.find((e) => e.d === "{NEW}").gen;
await T.markUnlocked(session);
const second = writes.length - first;
serve = async () => ({{ body: answer("{A}", "UQCALL000000", {{ kind: "call" }}) }});
const call = await T.ensureSession({{ formkey: "f", mxid: "{A}" }}, "call");
writes.length = 0;
await T.markUnlocked(call);
report({{ before: before.x, x: getIndex().entries.find((e) => e.d === "{NEW}").x, first, second, gen, genBefore: before.gen,
          callWrites: writes.length, sessionX: session.entry.x }});
""")
    assert got["before"] == 0 and got["x"] == 1 and got["sessionX"] == 1
    assert got["first"] == 1 and got["second"] == 0                 # written once: a later unlock fires no storage event
    assert got["gen"] == got["genBefore"]                            # `x` is no rotation: other tabs have nothing to act on
    assert got["callWrites"] == 0                                    # only a chat device is unlocked (a call device never is)


def test_the_chat_page_queues_what_it_encrypts_and_never_refuses_a_send_while_unlocking():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    assert "window.tr(\"Unlock your messages first" not in index
    send = index.split("function onSend(", 1)[1].split("\n}\n", 1)[0]
    assert "window.alert" not in send.split("postToChannel(", 1)[1].split("\n    return;", 1)[1]
    assert "state.locked" not in send
    # what needs no unlocking goes first (written through the site, or into the sealed list for someone not set up yet), then the gate
    assert send.index("postToGuildChannel(") < send.index("postToChannel(") < send.index("waitFor(room.roomId, text)") \
        < send.index("sendGate.submit({ roomId: room.roomId, text, context })")
    assert "const sendGate = makeSendGate({ deliver, onChange: () => drawThreadNotice() });" in index
    notice = index.split("function drawThreadNotice()", 1)[1].split("\n}\n", 1)[0]
    assert 'window.trn("Sending once chat is ready…", "Sending %(num)s messages once chat is ready…", count)' in notice
    assert "sendGate.state === LOCKED" in notice and 'action: window.tr("Unlock"), onAction: () => unlockChat()' in notice
    unlock = index.split("unlockChat = () => {", 1)[1].split("\n  unlockChat();", 1)[0]
    # ready only once unlocked; a refusal or a failure keeps what waits (locked), never drops it
    assert unlock.index("if (!ready) {") < unlock.index("sendGate.locked();") < unlock.index("state.locked = false;") \
        < unlock.index("sendGate.ready();") < unlock.index("markUnlocked(session)") < unlock.index("signOwnDevices(client, [signedCallDevice])")
    assert 'console.warn("Chat: encryption setup failed", e); return false;' in unlock and "sendGate.starting();" in unlock
    shared = index.split("async function sendShared(", 1)[1].split("\n}\n", 1)[0]
    assert "sendGate.settled()" in shared and "SHARE_WAIT_MS" in shared and "unlocked !== READY" in shared
    assert "const SHARE_WAIT_MS = 10 * 1000;" in index                # under share_chat.js's SEND_WAIT, so the sheet hears why
    assert "var SEND_WAIT = 15000;" in (ROOT / "ruqqus" / "assets" / "js" / "share_chat.js").read_text(encoding="utf-8")


@node
def test_a_page_that_stopped_takes_nothing_more_and_promises_nothing():
    """keeper.js stops a page without reloading it (a Log out elsewhere while a message waited, a refused renewal): its client is stopped,
    so nothing new is queued behind a "will be sent once chat is unlocked" that can never come true, and Unlock or ready() sends nothing."""
    got = gate("""
gate.submit({ roomId: "!a", text: "before" });
gate.locked();
gate.stop();
const took = await gate.submit({ roomId: "!a", text: "after" });
gate.starting();
const settled = await gate.settled();
await gate.ready();
gate.locked();
report({ took, delivered, state: gate.state, pending: gate.pending(), settled });
""")
    assert got["took"] is False and got["delivered"] == [] and got["state"] == "stopped" and got["settled"] == "stopped"
    assert got["pending"] == 1                                    # the one that waited is what the page's line counted, no more


def test_the_chat_page_closes_its_thread_and_stops_the_gate_when_it_stops_without_a_reload():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    ui = index.split("const chatUi = {", 1)[1].split("\n};\n", 1)[0]
    stop = ui.split("  stop(text, options = {}) {", 1)[1].split("\n  },", 1)[0]
    # only a keeper that has stopped (a start that failed has no client yet and keeps its Retry)
    assert stop.index("if (state.keeper && state.keeper.stopped) {") < stop.index("sendGate.stop();") < stop.index("closeThread();") \
        < stop.index("area.replaceChildren(drawLine(text, options))")
    send = index.split("function onSend(", 1)[1].split("\n}\n", 1)[0]
    assert send.index("if (sendGate.state === STOPPED) {") < send.index("sendGate.submit({ roomId: room.roomId, text, context })")
    notice = index.split("function drawThreadNotice()", 1)[1].split("\n}\n", 1)[0]
    assert notice.index("sendGate.state === STOPPED") < notice.index("sendGate.state === LOCKED")
    assert 'showThreadNotice(window.tr("Chat stopped in this tab."));' in notice



def test_a_page_taken_over_while_its_lists_load_stays_stopped_and_queues_nothing():
    """Another tab took chat over while main() waited for the first conversation lists: the note "Chat is open in another tab" stays
    (a late list answer or a tab pressed draws nothing over it), nothing more starts (no ready, no thread, no unlock that would leave
    what is typed under "Sending once chat is ready" for good), and the gate is stopped, so a send says chat stopped."""
    index = (SRC / "index.js").read_text(encoding="utf-8")
    main = index.split("async function main() {", 1)[1]
    after = main.split("  await listsAtStart();\n", 1)[1]
    assert after.index("if (chatGone()) return;") < after.index("isReady = true;") < after.index("boot.initialRoom") \
        < after.index("unlockChat();")
    lost = index.split("    lost: () => {", 1)[1].split("\n    },\n", 1)[0]
    assert lost.index("const unsent = sendGate.pending();") < lost.index("sendGate.stop();") < lost.index("const role = frameRole();")
    set_tab = index.split("function setTab(tab) {", 1)[1].split("\n}\n", 1)[0]
    assert set_tab.index("if (chatGone()) return;") < set_tab.index("state.tab = tab;")
    lists = index.split("async function refreshLists() {", 1)[1].split("\n}\n", 1)[0]
    assert lists.index("await Promise.all([") < lists.index("if (chatGone()) return;") < lists.index("state.conversations.inbox = inbox;")

def test_lists_refused_when_the_page_starts_are_asked_again_never_left_loading():
    """A refusal of the first lists (a 429 from the site's 100 requests a minute for a member, an app restarting) used to end main()
    and leave "Loading…" for good (found by chat_history.spec.ts). They are asked again, at most every 15 s and then every minute
    (repeated 429s count toward the address's time out), the list says so, and a page that stopped asks nothing more."""
    index = (SRC / "index.js").read_text(encoding="utf-8")
    start = index.split("async function listsAtStart() {", 1)[1].split("\n}\n", 1)[0]
    assert "for (let wait = 15_000; ; wait = Math.min(wait * 2, 60_000))" in start
    assert start.index("await refreshLists();") < start.index("return;") < start.index("if (chatGone()) return;")
    assert 'window.tr("Couldn\'t load your chats. Trying again…")' in start
    assert start.index("setTimeout(resolve, wait)") < start.rindex("if (chatGone()) return;")
    main = index.split("async function main() {", 1)[1]
    assert main.count("await refreshLists();") == 0 and main.count("await listsAtStart();") == 1


def test_the_gate_holds_text_in_memory_only():
    gate_src = (SRC / "send_gate.js").read_text(encoding="utf-8")
    assert "import " not in gate_src and "localStorage" not in gate_src and "sessionStorage" not in gate_src
    assert "indexedDB" not in gate_src
