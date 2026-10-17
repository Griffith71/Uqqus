/*
 * A post's live thread on its page (templates/partials/live_panel.html, routes/live_threads.py), read like Reddit's:
 * "LIVE · ends in 5 h · 12 watching" (a count only: every 30 s while the tab is visible this page says it is still
 * watching), times that keep ticking ("5 min ago"), new updates as they come (`live.update` carries only an id: the
 * update is asked for, drawn for this reader) with a "3 new updates" pill when the reader has scrolled down, updates
 * crossed out ("Corrected") or deleted (`live.changed`), and the pinned summary (`live.summary`).
 * For the post's author and co-authors: Go live for a length, Extend, End now, Edit summary, post an update, and
 * Cross out / Delete on updates. Visitors have no live stream: their page asks for new updates with each heartbeat.
 * Stored HTML (an update, the summary) was sanitized when it was written (helpers/sanitize.py); everything else goes in
 * as text.
 */
(function () {
  var panel = document.getElementById('live-panel');
  if (!panel) return;
  var post = panel.getAttribute('data-post');
  var list = document.getElementById('live-updates');
  var may = panel.getAttribute('data-may') === 'true';
  var live = panel.getAttribute('data-live') === 'true';
  var pill = document.getElementById('live-new-updates');
  var unseen = 0;

  // --- times ---------------------------------------------------------------------------------------------------
  function ago(seconds) {
    if (seconds < 45) return 'just now';
    if (seconds < 3600) return Math.round(seconds / 60) + ' min ago';
    if (seconds < 86400) return Math.round(seconds / 3600) + ' h ago';
    return null;
  }

  function until(seconds) {
    if (seconds <= 0) return 'now';
    var total = Math.max(1, Math.round(seconds / 60));     // whole minutes first, so it never says "60 min"
    var hours = Math.floor(total / 60);
    var minutes = total % 60;
    if (hours >= 48) return 'in ' + Math.round(hours / 24) + ' days';
    if (hours >= 1) return 'in ' + hours + ' h' + (minutes ? ' ' + minutes + ' min' : '');
    return 'in ' + total + ' min';
  }

  function formatTimes(root) {
    var now = Date.now() / 1000;
    root.querySelectorAll('[data-live-time]').forEach(function (el) {
      var at = Number(el.getAttribute('data-live-time'));
      var when = new Date(at * 1000);
      el.textContent = ago(now - at) || when.toLocaleString([], { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
      el.title = when.toLocaleString();
      el.setAttribute('datetime', when.toISOString());
    });
    root.querySelectorAll('[data-live-until]').forEach(function (el) {
      var at = Number(el.getAttribute('data-live-until'));
      el.textContent = until(at - now);
      el.title = new Date(at * 1000).toLocaleString();
      if (live && at - now <= 0) markEnded();
    });
  }

  function markEnded() {
    live = false;
    panel.classList.remove('is-live');
    panel.setAttribute('data-live', 'false');
    var badge = document.getElementById('live-panel-badge');
    if (badge) badge.hidden = true;
    var form = document.getElementById('live-update-form');
    if (form) form.classList.add('d-none');
    var status = panel.querySelector('.live-status');
    if (status) {
      status.querySelectorAll('.live-status-text').forEach(function (n) { n.remove(); });
      if (!document.getElementById('live-panel-ended')) {
        var ended = document.createElement('span');
        ended.className = 'live-ended';
        ended.id = 'live-panel-ended';
        ended.textContent = 'This live thread has ended';
        status.appendChild(ended);
      }
    }
  }

  formatTimes(panel);
  setInterval(function () { formatTimes(panel); }, 30000);

  // --- updates -------------------------------------------------------------------------------------------------
  function lastId() {
    var first = list.querySelector('[data-update]');
    return first ? Number(first.getAttribute('data-update')) : 0;
  }

  function build(u) {
    var item = document.createElement('div');
    item.className = 'live-update' + (u.struck ? ' is-struck' : '');
    item.setAttribute('role', 'listitem');
    item.setAttribute('data-update', String(u.id));
    if (u.may_change) item.setAttribute('data-may-change', 'true');
    var meta = document.createElement('div');
    meta.className = 'live-update-meta text-small text-muted';
    var time = document.createElement('time');
    time.setAttribute('data-live-time', String(u.created_utc));
    meta.appendChild(time);
    if (u.author) meta.appendChild(document.createTextNode(' · ' + u.author));
    if (u.struck) {
      meta.appendChild(document.createTextNode(' · '));
      var corrected = document.createElement('span');
      corrected.className = 'live-corrected';
      corrected.textContent = 'Corrected';
      meta.appendChild(corrected);
    }
    var body = document.createElement('div');
    body.className = 'live-update-body';
    if (u.hidden) {
      body.classList.add('font-italic', 'text-muted');
      body.textContent = 'Hidden by your word filter';
    } else {
      body.innerHTML = u.body_html || '';            // sanitized when it was written
    }
    item.appendChild(meta);
    item.appendChild(body);
    addMenu(item);
    formatTimes(item);
    return item;
  }

  function draw(u) {
    if (list.querySelector('[data-update="' + u.id + '"]')) return;
    var item = build(u);
    item.classList.add('is-new');
    list.insertBefore(item, list.firstChild);
    // the reader has scrolled past the top of the thread: say so instead of moving the page
    if (panel.getBoundingClientRect().top < 0) {
      unseen += 1;
      pill.textContent = unseen === 1 ? '1 new update' : unseen + ' new updates';
      pill.hidden = false;
    }
  }

  function replace(u) {
    var old = list.querySelector('[data-update="' + u.id + '"]');
    if (!old) return;
    if (u.gone) { old.remove(); return; }
    old.replaceWith(build(u));
  }

  pill.addEventListener('click', function () {
    unseen = 0;
    pill.hidden = true;
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  function getJson(url) {
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function fetchNew() {
    return getJson('/api/post/' + post + '/live_updates?after=' + lastId()).then(function (data) {
      if (!data) return;
      (data.updates || []).forEach(draw);
      if (live && data.thread && !data.thread.live) markEnded();
    });
  }

  function send(url, fields) {
    var body = new URLSearchParams(fields || {});
    body.set('formkey', typeof formkey === 'function' ? formkey() : '');
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (r.status === 204) return {};
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.error || 'That didn’t work.');
          return data;
        });
      });
  }

  // Cross out / Undo / Delete on an update, for its writer and the post's author
  function addMenu(item) {
    if (item.getAttribute('data-may-change') !== 'true' || item.querySelector('.live-update-actions')) return;
    var id = item.getAttribute('data-update');
    var struck = item.classList.contains('is-struck');
    var actions = document.createElement('div');
    actions.className = 'live-update-actions';
    var cross = document.createElement('button');
    cross.type = 'button';
    cross.className = 'btn btn-link btn-sm p-0';
    cross.textContent = struck ? 'Undo correction' : 'Cross out';
    cross.addEventListener('click', function () {
      send('/api/live_updates/' + id + '/strike', { on: struck ? '0' : '1' }).then(replace)
        .catch(function (e) { window.alert(e.message); });
    });
    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn-link btn-sm p-0 text-danger ml-3';
    del.textContent = 'Delete';
    del.addEventListener('click', function () {
      if (!window.confirm('Delete this update? This can’t be undone.')) return;
      send('/api/live_updates/' + id + '/delete').then(function () { replace({ id: id, gone: true }); })
        .catch(function (e) { window.alert(e.message); });
    });
    actions.appendChild(cross);
    actions.appendChild(del);
    item.appendChild(actions);
  }
  list.querySelectorAll('.live-update').forEach(addMenu);

  // --- the runners' controls -----------------------------------------------------------------------------------
  var controlStatus = document.getElementById('live-control-status');
  function control(fields, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return;
    if (controlStatus) controlStatus.textContent = 'Saving…';
    send('/api/post/' + post + '/live', fields).then(function () { window.location.reload(); })
      .catch(function (e) { if (controlStatus) controlStatus.textContent = e.message; });
  }
  var startButton = document.getElementById('live-start');
  if (startButton) {
    startButton.addEventListener('click', function () {
      control({ on: '1', hours: document.getElementById('live-start-hours').value });
    });
  }
  var extendButton = document.getElementById('live-extend');
  if (extendButton) {
    extendButton.addEventListener('click', function () {
      control({ extend: '1', hours: document.getElementById('live-extend-hours').value });
    });
  }
  var endButton = document.getElementById('live-end');
  if (endButton) {
    endButton.addEventListener('click', function () { control({ on: '0' }, 'End the live thread now?'); });
  }

  var form = document.getElementById('live-update-form');
  if (form) {
    var box = document.getElementById('live-update-body');
    var status = document.getElementById('live-update-status');
    form.addEventListener('submit', function () {
      var text = box.value.trim();
      if (!text) return;
      status.textContent = 'Posting…';
      send('/api/post/' + post + '/live_updates', { body: text }).then(function (u) {
        box.value = '';
        status.textContent = '';
        draw(u);
      }).catch(function (e) { status.textContent = e.message; });
    });
  }

  // the pinned summary
  var summaryBody = document.getElementById('live-summary-body');
  function drawSummary(thread) {
    if (!summaryBody || !thread) return;
    if (thread.summary_html) {
      summaryBody.innerHTML = thread.summary_html;   // sanitized when it was written
      return;
    }
    summaryBody.textContent = '';
    var note = document.createElement('p');
    note.className = 'mb-0 text-muted' + (thread.summary_hidden ? ' font-italic' : '');
    note.textContent = thread.summary_hidden ? 'Hidden by your word filter' : 'Key facts and links people should see first.';
    summaryBody.appendChild(note);
  }
  var summaryForm = document.getElementById('live-summary-form');
  var summaryEdit = document.getElementById('live-summary-edit');
  if (summaryForm && summaryEdit) {
    summaryEdit.addEventListener('click', function () {
      summaryForm.hidden = false;
      summaryEdit.hidden = true;
      document.getElementById('live-summary-text').focus();
    });
    document.getElementById('live-summary-cancel').addEventListener('click', function () {
      summaryForm.hidden = true;
      summaryEdit.hidden = false;
    });
    summaryForm.addEventListener('submit', function () {
      send('/api/post/' + post + '/live_summary', { summary: document.getElementById('live-summary-text').value })
        .then(function (data) {
          drawSummary(data.thread);
          summaryForm.hidden = true;
          summaryEdit.hidden = false;
        }).catch(function (e) { window.alert(e.message); });
    });
  }

  // "Go live" from a post's menu lands here (?live=start)
  if (/[?&]live=start\b/.test(window.location.search) && may) {
    var target = document.getElementById('live-start-hours') || document.getElementById('live-update-body');
    panel.scrollIntoView({ block: 'start' });
    if (target) target.focus();
  }

  // --- who is watching (a count only) --------------------------------------------------------------------------
  var watching = document.getElementById('live-watching');
  function heartbeat() {
    if (!live || document.hidden) return;
    fetch('/api/post/' + post + '/live_watch', {
      method: 'POST', credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
      if (!data) return;
      if (watching && data.watching > 0) {
        document.getElementById('live-watching-count').textContent = String(data.watching);
        watching.hidden = false;
      }
      if (!data.live) markEnded();
    }).catch(function () {});
    if (!window.UqqusLive) fetchNew();               // no live stream (a visitor): ask with the heartbeat
  }
  heartbeat();
  setInterval(heartbeat, 30000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) heartbeat(); });

  // --- what the live stream says --------------------------------------------------------------------------------
  if (window.UqqusLive) {
    window.UqqusLive.watch('post:' + post);
    var pending = null;           // one ask for a burst of updates
    window.UqqusLive.on('live.update', function () {
      if (pending) return;
      pending = setTimeout(function () { pending = null; fetchNew(); }, 300 + Math.random() * 400);
    });
    window.UqqusLive.on('live.changed', function (data) {
      if (!data || !data.id) return;
      getJson('/api/live_updates/' + Number(data.id)).then(function (u) { if (u) replace(u); });
    });
    window.UqqusLive.on('live.summary', function () {
      getJson('/api/post/' + post + '/live_updates?after=' + lastId()).then(function (d) { if (d) drawSummary(d.thread); });
    });
    window.UqqusLive.on('live.status', function () { if (!may) window.location.reload(); });
    window.UqqusLive.on('live.ended', markEnded);
    window.UqqusLive.on('comment.new', function () {
      document.getElementById('live-new-comments').classList.remove('d-none');
    });
  }
})();
