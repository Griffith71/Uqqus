// The reader's answer to "may another site's video player load before you press play?" (helpers/consent.py is the rule on the
// server; CLAUDE.md "Video consent"). The banner is partials/consent_banner.html, and the answer is the `video_consent` cookie,
// written here at once and then replaced by the same cookie from the server (routes/consent_pages.py). <html data-video-consent>
// is what everything reads (ask, no or yes; missing for a reader who isn't asked): video_playback.js decides from it, and
// feed_video_autoplay.js stays the only place a player starts.
//
// Top: the cookie, with no page in it, so node can run it (tests/test_consent.py checks it against helpers/consent.py).
// The page's part below it runs only in a browser: the banner is not a dialog. It traps nothing, Escape decides nothing, and
// its two buttons weigh the same.

(function (root) {
  var COOKIE = "video_consent";
  var ANSWER_PATH = "/consent/video";
  var VERSION = 1;
  var REASK_SECONDS = 183 * 86400;
  var KEEP_SECONDS = 400 * 86400;      // longer than the answer counts, so the server can tell a "no" that lapsed from no answer
  var CLOCK_SLACK = 7 * 86400;
  var GOOGLE_WAIT_MS = 5000;

  function parsed(cookies) {
    var found = /(?:^|;\s*)video_consent=(\d{1,4})\.(yes|no)\.(\d{1,11})(?:;|\s*$)/.exec(String(cookies || ""));
    return found && Number(found[1]) === VERSION ? found : null;
  }

  // the choice in a whole document.cookie string ("yes" or "no"), or null
  function readChoice(cookies, now) {
    var found = parsed(cookies);
    if (!found) return null;
    var age = now - Number(found[3]);
    if (age >= REASK_SECONDS || age < -CLOCK_SLACK) return null;
    return found[2];
  }

  // The cookie's whole value in a document.cookie string, or null, and the choice in such a value. No clock in either: what a
  // kept page goes by (below), where the date is usually the server's and this browser's clock may be unable to place it.
  function cookieText(cookies) {
    var found = parsed(cookies);
    return found ? found[1] + "." + found[2] + "." + found[3] : null;
  }

  function choiceIn(text) {
    return text ? text.split(".")[1] : null;
  }

  // what follows "video_consent=" when it is written
  function cookieValue(choice, now, secure) {
    return VERSION + "." + choice + "." + Math.floor(now) + "; path=/; max-age=" + KEEP_SECONDS + "; samesite=lax" + (secure ? "; secure" : "");
  }

  var api = { readChoice: readChoice, cookieText: cookieText, choiceIn: choiceIn, cookieValue: cookieValue, COOKIE: COOKIE, ANSWER_PATH: ANSWER_PATH, VERSION: VERSION, REASK_SECONDS: REASK_SECONDS, KEEP_SECONDS: KEEP_SECONDS, GOOGLE_WAIT_MS: GOOGLE_WAIT_MS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.VideoConsent = api;
  if (typeof document === "undefined") return;

  var page = document.documentElement;

  function nowSeconds() {
    return Date.now() / 1000;
  }

  // The server named the choice from the cookie it was sent, and a cookie that stands has the last word. Runs before
  // feed_video_autoplay.js reads the page. (Going back to a copy of a page in the HTTP cache comes through here too: pages say
  // `Vary: Cookie`, so a copy from before a new answer isn't used, and the server draws it again.)
  var choiceNow = readChoice(document.cookie, nowSeconds());
  if (choiceNow) page.setAttribute("data-video-consent", choiceNow);
  var cookieSeen = cookieText(document.cookie);

  // What decides whether scrolling may start a player: the reader's answer to the banner, their Tap to play, and a device asking
  // for less motion or less data. feed_video_autoplay.js starts players by it and the banner says what is true now by it.
  function environment() {
    var connection = navigator.connection;
    var data = page.dataset;
    return {
      consent: data.videoConsent,
      pref: data.videoAutoplay,
      reducedMotion: !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
      saveData: !!(connection && connection.saveData)
    };
  }
  api.environment = environment;

  function scrollingStartsPlayers() {
    return !!window.VideoPlayback && window.VideoPlayback.autoplayAllowed(environment());
  }

  // players that come in after a choice follow it; one made on this page takes effect at once
  function rescan() {
    if (window.observeFeedVideos) window.observeFeedVideos();
  }

  // The page now says `choice` and said `had`. Taking it back (or saying no where nobody asked): what is loaded goes. A player
  // pressed while being asked stays: the press was meant.
  function follow(choice, had) {
    if (choice === "no" && (had === "yes" || !had)) takeDown();
    rescan();
  }

  // A page the browser kept (the back button) comes back as it was left, older than any answer given since on another page. The
  // cookie having changed is what says so, and no clock: its date is usually the server's, so a browser whose clock is wrong can't
  // place it, and nothing but an answer changes it. Unchanged, it is what the page was drawn from, lapsed or not.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    var had = page.getAttribute("data-video-consent");
    var text = cookieText(document.cookie);
    if (text && text !== cookieSeen) page.setAttribute("data-video-consent", choiceIn(text));
    cookieSeen = text;
    var choice = page.getAttribute("data-video-consent");
    if (choice !== had) {
      setOpen(false);
      opener = null;
    }
    follow(choice, had);
  });

  var banner = null;
  var opener = null;

  function setOpen(open, reopened) {
    if (!banner) return;
    banner.hidden = !open;
    if (!open) return;
    var choice = page.getAttribute("data-video-consent") || "";
    var scrolling = scrollingStartsPlayers();
    banner.querySelector("[data-consent-close]").hidden = !reopened;
    banner.querySelector("[data-consent-now='wait']").hidden = !reopened || scrolling;
    banner.querySelector("[data-consent-now='allowed']").hidden = !reopened || !scrolling;
    banner.querySelectorAll("[data-consent-choice]").forEach(function (button) {
      button.setAttribute("aria-pressed", reopened && button.getAttribute("data-consent-choice") === choice ? "true" : "false");
    });
  }

  function close() {
    setOpen(false);
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  }

  function takeDown() {
    if (window.takeDownFeedVideos) window.takeDownFeedVideos();
    document.querySelectorAll("iframe.space-replay-frame, iframe.story-frame").forEach(function (frame) { frame.remove(); });
  }

  function writeCookie(choice) {
    try {
      document.cookie = "video_consent=" + cookieValue(choice, nowSeconds(), location.protocol === "https:");
    } catch (e) {
      // cookies blocked: the choice lasts this page, and the next one asks again
    }
  }

  function postAnswer(choice) {
    return fetch(ANSWER_PATH, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,                 // the reader may be on their way to another page
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "choice=" + choice
    });
  }

  // WebKit keeps a cookie written above for seven days at most, and one a response set for as long as it says, so the server sets
  // the same cookie. It fails in silence: the one written above stands. One request at a time, the latest answer last, so that a
  // "yes" still on its way can't land after the "no" that followed it (and what it set is put right before the "no" goes).
  var sending = false;
  var waiting = null;

  function sendAnswer(choice) {
    if (!window.fetch) return;
    if (sending) {
      waiting = choice;
      return;
    }
    sending = true;
    function settled() {
      sending = false;
      var next = waiting;
      waiting = null;
      if (!next) return;
      writeCookie(next);
      sendAnswer(next);
    }
    postAnswer(choice).then(settled, settled);
  }

  // leaving the page before the first has come back: the latest goes now, or it would never go
  window.addEventListener("pagehide", function () {
    if (!waiting) return;
    postAnswer(waiting).catch(function () {});
    waiting = null;
  });

  function choose(choice) {
    var had = page.getAttribute("data-video-consent");
    writeCookie(choice);
    sendAnswer(choice);
    page.setAttribute("data-video-consent", choice);
    follow(choice, had);
    close();
  }

  // Google's own message, when the ad tag is on this page, goes first: the two prompts never stack. Five seconds at the most,
  // so a tag that is blocked or broken doesn't lose the question (helpers/ads.py; nothing here runs while ads are off).
  function afterGoogle(then) {
    if (!document.querySelector('script[src*="adsbygoogle.js"]')) return then();
    var done = false;
    function go() {
      if (done) return;
      done = true;
      then();
    }
    window.googlefc = window.googlefc || {};
    window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];
    window.googlefc.callbackQueue.push({ CONSENT_DATA_READY: go });
    setTimeout(go, GOOGLE_WAIT_MS);
  }

  function start() {
    banner = document.getElementById("consent-banner");
    if (!banner) return;
    banner.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-consent-choice]");
      if (button) return choose(button.getAttribute("data-consent-choice"));
      if (event.target.closest && event.target.closest("[data-consent-close]")) close();
    });
    // Only pages with players ask by themselves. The others carry the banner for the Cookie choices link.
    if (banner.getAttribute("data-consent-auto") === "1" && page.getAttribute("data-video-consent") === "ask") {
      afterGoogle(function () {
        if (banner.hidden && page.getAttribute("data-video-consent") === "ask") setOpen(true, false);
      });
    }
  }

  // Cookie choices (footers, menus, the privacy page, Settings). Without this script the link is the privacy page's section.
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("[data-consent-open]");
    if (!link || !banner) return;
    event.preventDefault();
    // the phone menu covers the bottom of the screen: it folds first (Bootstrap's own account menu closes itself)
    if (window.jQuery) window.jQuery(link).closest(".navbar-collapse.show").collapse("hide");
    opener = link;
    setOpen(true, true);
    var first = banner.querySelector("[data-consent-choice]");
    if (first) first.focus();
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})(typeof window !== "undefined" ? window : this);
