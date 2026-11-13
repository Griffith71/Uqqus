// Messages written before this device has unlocked its encryption (crypto.js ensureEncryptionReady) wait here, in this page, and go
// in the order they were written once it has. A message typed while chat was still starting used to be refused with "Unlock your
// messages first: reload the page..." and lost, although nothing was wrong: the unlock simply hadn't finished. Now nothing is dropped:
// while chat is starting the thread says it is sending once chat is ready; if the member left it locked, the thread offers to unlock
// it, and what waits goes then. Kept in memory only (a message is text the member just typed; it is never written to storage here).
//
// Only what this device encrypts waits: a guild channel and a public channel are written through the site, and a first message to
// someone whose chat isn't set up yet waits in its own sealed list (index.js waitFor), so those never come here.
//
// No imports, so it runs under node (tests/test_chat_send_gate.py).

export const STARTING = "starting";
export const READY = "ready";
export const LOCKED = "locked";
// chat stopped in this page without a reload (keeper.js: a refused renewal, a Log out elsewhere while messages waited): its client is
// stopped, so nothing new is taken or promised, and what waited is never sent (the page has said so: keeper.js unsentLine)
export const STOPPED = "stopped";

// `deliver(item)`: sends one item (answers a promise; a failure is the thread's to show, as for any send). `onChange()`: the gate's
// state or what waits changed, so the page draws its line again.
export function makeSendGate({ deliver, onChange = () => {} }) {
  let state = STARTING;
  const queue = [];
  let flushing = null;
  let settle = null;
  let settled = new Promise((resolve) => { settle = resolve; });

  function changed() {
    try { onChange(); } catch (e) { /* the line is drawn again at the next change */ }
  }

  // one item at a time, in order: a message written meanwhile joins the end and goes after the ones before it
  function flush() {
    if (!flushing) {
      flushing = (async () => {
        while (state === READY && queue.length) {
          const item = queue[0];
          try { await deliver(item); } catch (e) { /* shown by the thread like any failed send */ }
          queue.shift();
          changed();
        }
      })().finally(() => { flushing = null; });
    }
    return flushing;
  }

  return {
    get state() { return state; },

    // how many messages wait, for one room or for all
    pending(roomId = null) {
      return queue.filter((item) => roomId === null || item.roomId === roomId).length;
    },

    // a message the member sent: delivered now if this device is unlocked and nothing waits before it, otherwise kept in order
    submit(item) {
      if (state === STOPPED) return Promise.resolve(false);      // not kept: nothing here can send it any more
      if (state === READY && !queue.length && !flushing) return Promise.resolve(deliver(item)).catch(() => {});
      queue.push(item);
      changed();
      return state === READY ? flush() : Promise.resolve();
    },

    // this device unlocked: what waits goes now
    ready() {
      if (state === STOPPED) return Promise.resolve();
      state = READY;
      settle(READY);
      changed();
      return flush();
    },

    // the member left it locked (or unlocking failed): what waits stays, and the page offers to unlock again
    locked() {
      if (state === READY || state === STOPPED) return;
      state = LOCKED;
      settle(LOCKED);
      changed();
    },

    // unlocking again (the thread's Unlock): "sending once chat is ready" until it answers
    starting() {
      if (state === READY || state === STOPPED) return;
      if (state !== STARTING) settled = new Promise((resolve) => { settle = resolve; });
      state = STARTING;
      changed();
    },

    // chat stopped in this page: for good (a reload starts it again)
    stop() {
      if (state === STOPPED) return;
      state = STOPPED;
      settle(STOPPED);
      changed();
    },

    // answers the state once chat is no longer starting (READY, LOCKED or STOPPED)
    settled() {
      return state === STARTING ? settled : Promise.resolve(state);
    },
  };
}
