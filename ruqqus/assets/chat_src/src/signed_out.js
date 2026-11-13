// A page whose chat device stops working (the chat plan's 6.2): the chat server answered M_UNKNOWN_TOKEN and the SDK gave up, or a
// proactive renewal failed for good. The site's answer to a renewal says which it was (keeper.js), never the chat server's
// `soft_logout`. Nothing is deleted here and nothing is removed from storage: a store is kept, locked, for its account (token.js says
// when one goes), and a dead token was already taken out of the index by the renewal that found it dead.
//
// No import of matrix-js-sdk: the event's name is the SDK's HttpApiEvent.SessionLoggedOut (tests/test_chat_sdk_contract.py checks it),
// so this runs under node too.

const tr = (text) => (globalThis.window && typeof window.tr === "function" ? window.tr(text) : text);

export const SESSION_LOGGED_OUT = "Session.logged_out";
const RETRY_EVERY = 60 * 1000;

// What the page does for each outcome (6.2's table).
export function afterSignedOut(keeper, why) {
  switch (why) {
    case "renewed":
    case "adopted":
      // rare (the SDK gave up although a token was found): a stopped client is simplest to rebuild
      return keeper.end(why);
    case "unusable":
      // a token renewed or adopted under a minute ago was refused too: the page start asks the site, whose list check finds out
      // whether the device still exists (a new device and a line saying why if not); over the reload budget, "Chat stopped in this tab."
      return keeper.end("unusable");
    case "removed":
      return keeper.end("removed", { notice: tr("Chat was signed out on this browser from another device. Starting again here…") });
    case "refused": {
      // the age lock, or a QR sign-in's password first: the site's own sentence, and no reload
      const body = keeper.refusal || {};
      return keeper.shutdown().then(() => {
        const text = body.error || tr("Chat isn't available for your account right now.");
        if (body.need_password) keeper.ui.stop(text, { why: "password", action: tr("Reload"), onAction: () => window.location.reload() });
        else keeper.ui.stop(text, { why: "refused" });
      });
    }
    case "transient":
      return stayOffline(keeper);
    case "signed_out":
    default:
      // the page redraws as the sign-in page, the password form or the lock message
      return keeper.end("signed_out");
  }
}

// The site or the network couldn't answer: a banner, a renewal tried every minute (and when Retry is pressed), and the page started
// again once it works. Any other answer is acted on as above.
function stayOffline(keeper) {
  let timer = null;
  let trying = false;
  const tryAgain = async () => {
    if (trying) return;
    clearTimeout(timer);
    if (keeper.stopped) return;                   // another tab took chat over, or the page ended: no more tries, nothing drawn
    trying = true;
    const outcome = await keeper.renew(keeper.client ? keeper.client.getAccessToken() : null);
    trying = false;
    if (keeper.stopped) return;                   // stopped while it asked: its outcome is nobody's to act on
    if (outcome === "transient") {
      timer = setTimeout(tryAgain, RETRY_EVERY);
      return;
    }
    keeper.ui.banner(null);
    afterSignedOut(keeper, outcome);
  };
  keeper.ui.banner(tr("Chat lost its connection. Retrying…"), { why: "offline", action: tr("Retry"), onAction: tryAgain });
  timer = setTimeout(tryAgain, RETRY_EVERY);
}

export function watchSignedOut(client, keeper) {
  let heard = false;
  // a page that stopped meanwhile (another tab took chat over: keeper.stopped without ending; or it is ending) acts on nothing more:
  // no reload, no sentence drawn over the note it shows
  const lost = async (why) => {
    if (heard || keeper.stopped) return;
    heard = true;
    await afterSignedOut(keeper, why);
  };
  keeper.onLost = (outcome) => { lost(outcome).catch(() => {}); };
  client.on(SESSION_LOGGED_OUT, async () => {
    if (heard || keeper.stopped) return;
    try { client.stopClient(); } catch (e) { /* stopped already */ }
    // a 401 that bypassed the keeper's fetchFn is classified now; renew() never renews a token obtained under a minute ago
    const why = keeper.reason || (await keeper.renew(client.getAccessToken()));
    await lost(why);                              // asks `stopped` again: the renewal may have outlasted the page's chat
  });
}
