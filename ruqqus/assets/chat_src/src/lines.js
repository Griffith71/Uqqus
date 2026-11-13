// What the chat page says when chat is starting again here as a new device (the chat plan's 6.5): one line above the unlock box, or
// a one-time notice when the sign-in's pre-key unlocked it without asking. `why` is the site's `previous.reason`, or the browser's own
// (token.js, openCryptoStore). Some reasons say nothing: a first device, a failed mint, a child account, a deleted one.
// No imports, so it runs under node (tests/test_chat_keeper.py).

const tr = (text) => (globalThis.window && typeof window.tr === "function" ? window.tr(text) : text);

export function newDeviceLine(why) {
  switch (why) {
    case "others":
      return tr("You logged out everywhere from another device, so chat is starting again here.");
    case "password":
      return tr("Your password was changed, so chat is starting again here.");
    case "reset":
      return tr("Your password was reset, so chat is starting again here.");
    case "mfa":
      return tr("Two-factor sign-in was changed, so chat is starting again here.");
    case "mfa_recovery":
      return tr("You signed in with your two-factor recovery code, so chat is starting again here.");
    case "child_password":
      return tr("Your password was changed for you, so chat is starting again here.");
    case "deleted_elsewhere":
    case "signed_out_here":
      return tr("This browser was signed out of chat from another device, so chat is starting again here.");
    case "store_unopenable":
      return tr("Chat couldn't open what this browser saved, so it is starting again here.");
    case "store_missing":
      return tr("This browser's saved chat data was cleared, so chat is starting again here.");
    case "key_changed":
      return tr("The site's keys changed, so chat is starting again here.");
    case "stale":
      return tr("Chat hadn't been used on this browser for a long time, so it is starting again here.");
    default:
      return null;                                // legacy, mint_failed, child_*, deleted, or nothing to say
  }
}
