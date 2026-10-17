/*
 * A shared draft's live editor (templates/collab/editor.html, routes/collab.py, helpers/collab_ot.py).
 *
 * Every change to the title or the text becomes one op (CollabOT.diff of the field before and after), folded with
 * the changes not sent yet (compose) and sent about every 250 ms with the revision it was made on. At most one
 * request is out at a time: an edit waiting for the server (`outstanding`) and the edits made since (`buffer`, one per
 * field). When the server answers, or the live topic says there's a new revision, the script asks for the ops it
 * hasn't seen and brings each one past its own waiting edits (transform) before applying it, keeping the caret
 * where it was. Its own op comes back in that list by its revision number and is not applied twice. An op that can't
 * be brought in (too far behind, or refused) reloads the draft.
 *
 * Text only: nothing here writes HTML; the side panel is parsed with DOMParser and its nodes moved in.
 */
(function () {
  var root = document.getElementById('collab-editor');
  if (!root || !window.CollabOT) return;
  var OT = window.CollabOT;
  var docId = root.getAttribute('data-doc');
  var base = '/api/collab/' + docId;
  var SEND_DELAY = 250;
  var POLL_MS = 8000;
  var PRESENCE_MS = 20000;

  var fields = {
    title: { el: document.getElementById('collab-title') },
    body: { el: document.getElementById('collab-body') }
  };
  Object.keys(fields).forEach(function (f) { fields[f].text = fields[f].el.value; });

  var revision = parseInt(root.getAttribute('data-revision'), 10) || 0;
  var outstanding = null;            // {field, op, key}: sent, not yet seen back
  var buffer = { title: null, body: null };
  var busy = false;                  // a request (send or fetch) is out
  var wantFetch = false;
  var ackOwn = null;                 // the revision the server gave our outstanding op (null: not answered yet)
  var ackEmpty = false;              // the server answered that our op changed nothing
  var ackRevision = 0;               // the draft's revision when it answered
  var sendTimer = null;
  var failures = 0;
  var statusEl = document.getElementById('collab-status');
  var noticeEl = document.getElementById('collab-notice');

  function key() { return typeof formkey === 'function' ? formkey() : ''; }

  function setStatus(text) { if (statusEl) statusEl.textContent = text; }

  function notice(text) {
    if (!noticeEl) return;
    noticeEl.textContent = text || '';
    noticeEl.hidden = !text;
  }

  function pending() { return !!(outstanding || buffer.title || buffer.body); }

  function newKey() {
    var a = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  // --- local edits -------------------------------------------------------------------------------------------------

  function onInput(f) {
    var field = fields[f];
    var value = field.el.value;
    if (value === field.text) return;
    var op = OT.diff(field.text, value, field.el.selectionEnd);
    field.text = value;
    buffer[f] = buffer[f] ? OT.compose(buffer[f], op) : op;
    setStatus('Saving…');
    typing();
    clearTimeout(sendTimer);
    sendTimer = setTimeout(pump, SEND_DELAY);
  }

  Object.keys(fields).forEach(function (f) {
    fields[f].el.addEventListener('input', function () { onInput(f); });
  });

  // --- talking to the server ----------------------------------------------------------------------------------

  function pump() {
    if (busy) return;
    // sent but unanswered (the connection dropped): it may be on the server already, so it is sent again (same key)
    // before anything is fetched, or it could come back looking like someone else's edit
    if (outstanding && ackOwn === null && !ackEmpty) return;
    if (wantFetch) { fetchOps(); return; }
    if (outstanding) return;                       // waiting for our op to come back in the list
    var f = buffer.title ? 'title' : (buffer.body ? 'body' : null);
    if (!f) { setStatus(failures ? 'Offline' : 'Saved'); return; }
    outstanding = { field: f, op: buffer[f], key: newKey() };
    buffer[f] = null;
    send();
  }

  function send() {
    busy = true;
    ackOwn = null;
    ackEmpty = false;
    var body = new URLSearchParams({ field: outstanding.field, base: String(revision), op: JSON.stringify(outstanding.op),
                                     key: outstanding.key, formkey: key() });
    fetch(base + '/ops', { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, data: d }; });
      })
      .then(function (res) {
        busy = false;
        if (res.status === 200) {
          failures = 0;
          ackOwn = res.data.own;
          ackEmpty = res.data.own === null;
          ackRevision = res.data.revision;
          wantFetch = true;
          pump();
        } else if (res.data && res.data.resync) {
          resync('The draft changed under your last edit, so it was reloaded. Check your last few words.');
        } else if (res.status === 429) {
          retryLater();
        } else {
          setStatus('Not saved');
          notice(res.data.error || 'That edit couldn’t be saved.');
          resync();
        }
      })
      .catch(function () { busy = false; retryLater(); });
  }

  function retryLater() {
    // the same op is sent again with the same key: the server applies it once whatever happened before
    failures += 1;
    setStatus('Offline · retrying');
    setTimeout(function () { if (!busy && outstanding && ackOwn === null && !ackEmpty) send(); },
               Math.min(30000, 1000 * Math.pow(2, failures)));
  }

  function fetchOps() {
    busy = true;
    wantFetch = false;
    fetch(base + '/ops?after=' + revision, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, data: d }; });
      })
      .then(function (res) {
        busy = false;
        if (res.status !== 200) {
          if (res.data && res.data.resync) { resync(); return; }
          failures += 1;
          setTimeout(function () { wantFetch = true; pump(); }, Math.min(30000, 1000 * Math.pow(2, failures)));
          return;
        }
        failures = 0;
        (res.data.ops || []).forEach(receive);
        if (outstanding && ackEmpty && revision >= ackRevision) outstanding = null;   // it changed nothing
        pump();
      })
      .catch(function () { busy = false; failures += 1; setTimeout(function () { wantFetch = true; pump(); }, 3000); });
  }

  function receive(row) {
    if (row.r <= revision) return;
    revision = row.r;
    if (outstanding && ackOwn !== null && row.r === ackOwn) {   // our own op, back: it is in the text already
      outstanding = null;
      ackOwn = null;
      return;
    }
    var f = row.f, op = row.o, pair;
    if (!fields[f]) return;
    if (outstanding && outstanding.field === f) {
      pair = OT.transform(outstanding.op, op);
      outstanding.op = pair[0];
      op = pair[1];
    }
    if (buffer[f]) {
      pair = OT.transform(buffer[f], op);
      buffer[f] = pair[0];
      op = pair[1];
    }
    applyRemote(f, op);
  }

  function applyRemote(f, op) {
    var field = fields[f], el = field.el;
    var focused = document.activeElement === el;
    var start = el.selectionStart, end = el.selectionEnd, scroll = el.scrollTop;
    field.text = OT.apply(field.text, op);
    el.value = field.text;
    if (focused) {
      el.setSelectionRange(OT.transformIndex(op, start), OT.transformIndex(op, end));
    }
    el.scrollTop = scroll;
  }

  function resync(message) {
    busy = true;
    fetch(base + '/state', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error('gone'); return r.json(); })
      .then(function (d) {
        busy = false;
        outstanding = null;
        buffer = { title: null, body: null };
        revision = d.revision;
        fields.title.text = fields.title.el.value = d.title;
        fields.body.text = fields.body.el.value = d.body;
        if (message) notice(message);
        setStatus('Saved');
      })
      .catch(function () {
        busy = false;
        notice('You can’t edit this draft any more (you may have left it or been removed).');
        Object.keys(fields).forEach(function (f) { fields[f].el.readOnly = true; });
      });
  }

  // --- hearing about other people's edits ----------------------------------------------------------------------

  function wake() { wantFetch = true; pump(); }

  if (window.UqqusLive) {
    window.UqqusLive.watch('collab:' + docId);
    window.UqqusLive.on('collab.rev', function (data) { if ((data.revision || 0) > revision) wake(); });
    window.UqqusLive.on('collab.changed', refreshPanel);
  }
  setInterval(function () { if (!document.hidden && !busy) wake(); }, POLL_MS);

  // --- who else is here ------------------------------------------------------------------------------------------

  var hereEl = document.getElementById('collab-here');
  var lastTyping = 0;

  function presence(isTyping) {
    var body = new URLSearchParams({ typing: isTyping ? '1' : '0', formkey: key() });
    fetch(base + '/presence', { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !hereEl) return;
        var names = d.here.map(function (p) { return p.name; });
        var typers = d.here.filter(function (p) { return p.typing; }).map(function (p) { return p.name; });
        var text = names.length ? 'Also here: ' + names.join(', ') : '';
        if (typers.length) text += ' · ' + typers.join(' and ') + (typers.length === 1 ? ' is' : ' are') + ' typing…';
        hereEl.textContent = text;
      })
      .catch(function () {});
  }

  function typing() {
    var now = Date.now();
    if (now - lastTyping > 4000) { lastTyping = now; presence(true); }
  }

  presence(false);
  setInterval(function () { if (!document.hidden) presence(false); }, PRESENCE_MS);

  // --- the side panel and publishing -----------------------------------------------------------------------------

  function refreshPanel(force) {
    var panel = document.getElementById('collab-panel');
    if (!panel) return;
    if (force !== true && panel.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;  // don't eat typing
    fetch(panel.getAttribute('data-url'), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (html) {
        var parsed = new DOMParser().parseFromString(html, 'text/html');
        var nodes = Array.prototype.slice.call(parsed.body.childNodes).map(function (n) { return document.importNode(n, true); });
        panel.replaceChildren.apply(panel, nodes);
      })
      .catch(function () {});
  }

  window.guildPagesDone = function () { refreshPanel(true); };

  var publish = document.getElementById('collab-publish');
  var publishError = document.getElementById('collab-publish-error');
  if (publish) {
    publish.addEventListener('click', function () {
      if (pending()) {
        publishError.textContent = 'Wait a moment: your last changes are still being saved.';
        publishError.hidden = false;
        pump();
        return;
      }
      publish.disabled = true;
      publishError.hidden = true;
      var body = new URLSearchParams({ formkey: key() });
      fetch(publish.getAttribute('data-url'), { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          publish.disabled = false;
          if (!res.ok) { publishError.textContent = res.data.error || 'It couldn’t be published.'; publishError.hidden = false; return; }
          if (res.data.proposal) { notice(res.data.outcome === 'passed' ? 'Published.' : 'Publishing is put to a vote: the other authors have been asked.'); refreshPanel(); return; }
          if (res.data.url) window.location.href = res.data.url;
        })
        .catch(function () { publish.disabled = false; publishError.textContent = 'It couldn’t be published. Try again.'; publishError.hidden = false; });
    });
  }

  window.addEventListener('beforeunload', function (event) {
    if (pending()) { event.preventDefault(); event.returnValue = ''; }
  });
})();
