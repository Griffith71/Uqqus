/*
 * The page's one live-updates stream (GET /api/live/events, routes/live_events.py, helpers/live_bus.py).
 *
 *   UqqusLive.on('call.ring', function (data) { ... })    an event on the member's own channel
 *   UqqusLive.watch('post:abc')                            also hear a topic (the server keeps it only if allowed)
 *
 * One EventSource per tab, opened on the first `on`. The browser reconnects it by itself after the server ends it
 * (every 10 minutes) or after a network blip; asking for a new topic reopens it with the full list. A tab hidden for
 * HIDDEN_CLOSE_MS closes it and opens it again when shown, so background tabs don't hold a connection each.
 */
(function () {
  if (window.UqqusLive || !window.EventSource) return;

  var HIDDEN_CLOSE_MS = 10 * 60 * 1000;
  var handlers = {};
  var topics = [];
  var source = null;
  var bound = {};
  var hiddenTimer = null;

  function url() {
    return '/api/live/events' + (topics.length ? '?topics=' + encodeURIComponent(topics.join(',')) : '');
  }

  function bind(kind) {
    if (!source || bound[kind]) return;
    bound[kind] = true;
    source.addEventListener(kind, function (event) {
      var message;
      try { message = JSON.parse(event.data); } catch (e) { return; }
      (handlers[kind] || []).forEach(function (fn) {
        try { fn(message.data || {}); } catch (e) { /* one handler's error never stops the others */ }
      });
    });
  }

  var failures = 0;
  var retryTimer = null;

  function open() {
    if (source) source.close();
    clearTimeout(retryTimer);
    bound = {};
    source = new EventSource(url());
    source.addEventListener('open', function () { failures = 0; });
    // the browser reconnects by itself after a dropped connection, but gives up for good on an error answer
    // (a 429 or a 503, audit L-05): then this page reopens it, waiting a little longer each time
    source.addEventListener('error', function () {
      if (!source || source.readyState !== EventSource.CLOSED) return;
      failures += 1;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(function () {
        if (!document.hidden && Object.keys(handlers).length) open();
      }, Math.min(60000, 1000 * Math.pow(2, failures)));
    });
    Object.keys(handlers).forEach(bind);
  }

  // leaving the page closes its stream at once, so the next page's stream never waits behind it
  window.addEventListener('pagehide', function () {
    clearTimeout(retryTimer);
    if (source) source.close();
    source = null;
  });

  function close() {
    if (source) source.close();
    source = null;
    bound = {};
  }

  document.addEventListener('visibilitychange', function () {
    clearTimeout(hiddenTimer);
    if (document.hidden) {
      hiddenTimer = setTimeout(close, HIDDEN_CLOSE_MS);
    } else if (!source && Object.keys(handlers).length) {
      open();
    }
  });

  window.UqqusLive = {
    on: function (kind, fn) {
      (handlers[kind] = handlers[kind] || []).push(fn);
      if (!source) open(); else bind(kind);
    },
    watch: function (topic) {
      if (topics.indexOf(topic) !== -1) return;
      topics.push(topic);
      if (source) open();
    }
  };
})();
