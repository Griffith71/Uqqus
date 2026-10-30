// What the light language needs to know about a thread and a list that are redrawn from scratch every few hundred
// milliseconds (_light.scss): which message just arrived, and which conversation just gained unread messages. Pure (no DOM,
// no SDK), so tests/test_light_assets.py runs it in node with fake events.

export const FRESH_MS = 1200;        // how long a message counts as new: a redraw within it carries the glint on
export const FRESH_WITHIN = 30000;   // an event older than this by this device's clock is history that landed late
export const SEEN_MS = 60000;        // a message that has glinted is remembered this long, so a replayed sync never glints it twice
export const BUMP_MS = 700;          // how long a conversation row's glint carries on across redraws

// Your own message changes its event id when the server confirms it, but keeps its transaction id: that is its key.
export function freshKey(event) {
  return (event.getTxnId && event.getTxnId()) || event.getId();
}

// The SDK's own estimate of when the event happened by THIS device's clock (now minus the server's `age`), so a skewed clock
// can neither hide a new message nor let an old one in.
function happenedAt(event) {
  return typeof event.localTimestamp === "number" ? event.localTimestamp : event.getTs();
}

export function createFresh() {
  const fresh = new Map();   // key -> when its glint began
  const seen = new Map();    // key or event id -> when it glinted
  return {
    // A live timeline event: true when it starts a glint. Never history scrolled back to, an event that is not a shown message
    // (an edit, a reaction), one that landed late, or one that has glinted already.
    note(event, toStartOfTimeline, data, shown, now = Date.now()) {
      for (const [k, t] of fresh) if (now - t >= FRESH_MS) fresh.delete(k);
      for (const [k, t] of seen) if (now - t >= SEEN_MS) seen.delete(k);
      if (toStartOfTimeline || !(data && data.liveEvent) || !shown) return false;
      if (now - happenedAt(event) > FRESH_WITHIN) return false;
      const key = freshKey(event);
      if (seen.has(key) || seen.has(event.getId())) return false;
      fresh.set(key, now);
      seen.set(key, now);
      seen.set(event.getId(), now);
      return true;
    },
    // How far into its glint a message is (ms), or -1 when it is not glinting.
    age(event, now = Date.now()) {
      const key = freshKey(event);
      const since = fresh.get(key);
      if (since === undefined) return -1;
      const age = now - since;
      if (age >= FRESH_MS) {
        fresh.delete(key);
        return -1;
      }
      return age;
    },
  };
}

export function createUnreadBumps() {
  const last = new Map();     // conversation key -> the unread count last seen
  const bumped = new Map();   // conversation key -> when it last went up
  return {
    // Every conversation of every list, whenever the lists are fetched (not only the tab on show): a rise is seen when it happens,
    // never at the moment you open a tab. Not the conversation that is open (you are reading it), and not a first sighting.
    observe(conversations, activeKey, now = Date.now()) {
      const present = new Set();
      for (const c of conversations) {
        present.add(c.key);
        const before = last.get(c.key);
        if (before !== undefined && c.unread_count > before && c.key !== activeKey) bumped.set(c.key, now);
        last.set(c.key, c.unread_count);
      }
      for (const k of [...last.keys()]) {
        if (!present.has(k)) { last.delete(k); bumped.delete(k); }
      }
    },
    // How far into its glint a conversation's row is (ms), or -1.
    age(key, now = Date.now()) {
      const at = bumped.get(key);
      if (at === undefined) return -1;
      const age = now - at;
      if (age >= BUMP_MS) {
        bumped.delete(key);
        return -1;
      }
      return age;
    },
  };
}
