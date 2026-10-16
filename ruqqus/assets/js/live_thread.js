/*
 * A post's live thread on its page (templates/partials/live_panel.html, routes/live_threads.py): Go live / End live
 * and the update box for the post's author and co-authors, new updates as they come (`live.update` on the post's
 * topic carries only an id: the update is asked for, drawn for this reader), and a line when new comments arrive.
 * An update's HTML was sanitized when it was written (helpers/sanitize.py); everything else goes in as text.
 */
(function () {
  var panel = document.getElementById('live-panel');
  if (!panel) return;
  var post = panel.getAttribute('data-post');
  var list = document.getElementById('live-updates');

  function formatTimes(root) {
    root.querySelectorAll('[data-live-time]').forEach(function (el) {
      var when = new Date(Number(el.getAttribute('data-live-time')) * 1000);
      el.textContent = when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      el.title = when.toLocaleString();
    });
  }
  formatTimes(panel);

  function lastId() {
    var first = list.querySelector('[data-update]');
    return first ? Number(first.getAttribute('data-update')) : 0;
  }

  function draw(u) {
    if (list.querySelector('[data-update="' + u.id + '"]')) return;
    var item = document.createElement('div');
    item.className = 'live-update is-new';
    item.setAttribute('role', 'listitem');
    item.setAttribute('data-update', String(u.id));
    var meta = document.createElement('div');
    meta.className = 'live-update-meta text-small text-muted';
    var time = document.createElement('time');
    time.setAttribute('data-live-time', String(u.created_utc));
    meta.appendChild(time);
    if (u.author) meta.appendChild(document.createTextNode(' · ' + u.author));
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
    list.insertBefore(item, list.firstChild);
    formatTimes(item);
  }

  function fetchNew() {
    return fetch('/api/post/' + post + '/live_updates?after=' + lastId(), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) (data.updates || []).forEach(draw); })
      .catch(function () {});
  }

  function send(url, fields) {
    var body = new URLSearchParams(fields || {});
    body.set('formkey', typeof formkey === 'function' ? formkey() : '');
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) throw new Error(data.error || 'That didn’t work.');
          return data;
        });
      });
  }

  var toggle = document.getElementById('live-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var on = toggle.getAttribute('data-on');
      if (on === '0' && !window.confirm('End the live updates?')) return;
      toggle.disabled = true;
      send('/api/post/' + post + '/live', { on: on }).then(function () { window.location.reload(); })
        .catch(function (e) { toggle.disabled = false; window.alert(e.message); });
    });
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

  if (window.UqqusLive) {
    window.UqqusLive.watch('post:' + post);
    var pending = null;           // one ask for a burst of updates
    window.UqqusLive.on('live.update', function () {
      if (pending) return;
      pending = setTimeout(function () { pending = null; fetchNew(); }, 300 + Math.random() * 400);
    });
    window.UqqusLive.on('live.ended', function () {
      var badge = document.getElementById('live-panel-badge');
      if (badge) badge.hidden = true;
      panel.classList.remove('is-live');
    });
    window.UqqusLive.on('comment.new', function () {
      document.getElementById('live-new-comments').classList.remove('d-none');
    });
  }
})();
