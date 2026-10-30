// Live unread-message count on the navbar chat button (#chat-nav-badge).
// The server renders the initial number; this refreshes it from the
// read-only /api/chat/unread_count endpoint (it never marks anything read).
(function () {
  var badge = document.getElementById("chat-nav-badge");
  var icon = document.getElementById("chat-nav-icon");
  if (!badge) return;

  var INTERVAL_MS = 30000;
  var MAX_BACKOFF_MS = 300000;
  var delay = INTERVAL_MS;
  var timer = null;
  var inflight = false;
  var stopped = false;
  var last = parseInt(badge.textContent, 10) || 0; // what the server drew: the first render is not a rise

  // one small pop when the number goes up (the light language: _light.scss .is-popped; the class goes when it ends)
  function pop() {
    badge.classList.remove("is-popped");
    void badge.offsetWidth;
    badge.classList.add("is-popped");
  }
  badge.addEventListener("animationend", function () { badge.classList.remove("is-popped"); });

  function render(n) {
    n = Number(n) || 0;
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.classList.toggle("d-none", n === 0);
    if (icon) icon.classList.toggle("text-danger", n > 0);
    if (Math.min(n, 100) > Math.min(last, 100)) pop();      // what it shows: "99+" is one number
    last = n;
  }

  function schedule() {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(refresh, delay);
  }

  function refresh() {
    if (inflight || stopped) return;
    if (document.hidden) { schedule(); return; } // don't poll from background tabs
    inflight = true;
    fetch("/api/chat/unread_count", {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" }
    })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { stopped = true; throw new Error("auth"); }
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (data) { render(data.unread); delay = INTERVAL_MS; })
      .catch(function () { if (!stopped) delay = Math.min(delay * 2, MAX_BACKOFF_MS); })
      .then(function () { inflight = false; schedule(); });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refresh();
  });

  window.refreshChatBadge = refresh; // the chat page calls this after marking read
  schedule();
})();
