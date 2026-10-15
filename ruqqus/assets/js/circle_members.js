/*
 * "Add to circles" in the "..." menu of someone's profile (partials/circle_member_modal.html, routes/circles.py).
 * GET /api/circles/for/<name> lists the viewer's own custom circles and whether the account is in each; every tick is
 * saved at once (POST /api/circles/<id>/add or /remove) and put back when refused. Names are set with textContent.
 */
(function () {
  'use strict';

  var modal = document.getElementById('circleMemberModal');
  if (!modal) return;
  // the profile header is its own stacking context: inside it the sheet would sit under the page's backdrop
  if (modal.parentNode !== document.body) document.body.appendChild(modal);
  var list = document.getElementById('circle-member-list');
  var status = document.getElementById('circle-member-status');
  var who = document.getElementById('circle-member-name');
  var username = null;

  function say(text, isError) {
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  function post(path, fields) {
    var body = new URLSearchParams();
    body.set('formkey', formkey());
    Object.keys(fields).forEach(function (k) { body.set(k, fields[k]); });
    return fetch(path, { method: 'POST', credentials: 'same-origin', body: body })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); });
  }

  function row(circle) {
    var wrap = document.createElement('div');
    wrap.className = 'custom-control custom-checkbox circle-member-row';
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'custom-control-input';
    box.id = 'circle-member-' + circle.id;
    box.checked = !!circle.in;
    var label = document.createElement('label');
    label.className = 'custom-control-label';
    label.htmlFor = box.id;
    label.textContent = circle.name;
    box.addEventListener('change', function () {
      var adding = box.checked;
      box.disabled = true;
      say('');
      post('/api/circles/' + encodeURIComponent(circle.id) + (adding ? '/add' : '/remove'), { username: username }).then(function (res) {
        box.disabled = false;
        if (!res.ok) { box.checked = !adding; say(res.data.error || 'Could not change that.', true); return; }
        say(res.data.message || '');
      }).catch(function () { box.disabled = false; box.checked = !adding; say('Could not change that. Try again.', true); });
    });
    wrap.appendChild(box);
    wrap.appendChild(label);
    return wrap;
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('[data-circle-member]') : null;
    if (!item) return;
    event.preventDefault();
    username = item.getAttribute('data-circle-member');
    who.textContent = '@' + username;
    list.textContent = '';
    say('Loading your circles…');
    $('#circleMemberModal').modal('show');
    fetch('/api/circles/for/' + encodeURIComponent(username), { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (!res.ok) { say(res.data.error || 'Could not load your circles.', true); return; }
        say('');
        if (!res.data.circles.length) {
          var empty = document.createElement('p');
          empty.className = 'text-small text-muted mb-0';
          empty.textContent = "You haven't made a circle of your own yet. Your Friends circle is automatic: it is everyone you follow who follows you back.";
          list.appendChild(empty);
          return;
        }
        res.data.circles.forEach(function (circle) { list.appendChild(row(circle)); });
      })
      .catch(function () { say('Could not load your circles. Try again.', true); });
  });
})();
