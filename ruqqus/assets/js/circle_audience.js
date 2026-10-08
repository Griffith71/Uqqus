/*
 * "Who can see this" in the menu of one's own post (partials/audience_modal.html, routes/circles.py).
 * The menu item carries the post's id and its current audience; the sheet shows three choices and saves one.
 * Making a Circle post public again asks first (the route needs `confirm=1` for it). Text only, no HTML.
 */
(function () {
  'use strict';

  var modal = document.getElementById('audienceModal');
  if (!modal) return;

  var save = document.getElementById('audience-save');
  var status = document.getElementById('audience-status');
  var target = null;

  function say(text, isError) {
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  function post(path, fields) {
    var body = new URLSearchParams();
    body.set('formkey', formkey());
    Object.keys(fields || {}).forEach(function (k) { body.set(k, fields[k]); });
    return fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; }); });
  }

  function chosen() {
    var picked = modal.querySelector('input[name="audience-choice"]:checked');
    return picked ? picked.value : null;
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('.audience-item') : null;
    if (!item) return;
    event.preventDefault();
    target = { id: item.getAttribute('data-post-id'), audience: item.getAttribute('data-audience') || '0' };
    var radio = document.getElementById('audience-option-' + target.audience);
    if (radio) radio.checked = true;
    say('');
    $('#audienceModal').modal('show');
  });

  save.addEventListener('click', function () {
    var value = chosen();
    if (!target || value === null) { say('Choose who can see this.', true); return; }
    if (value === target.audience) { $('#audienceModal').modal('hide'); return; }
    var fields = { audience: value };
    if (value === '0' && target.audience !== '0') {
      if (!window.confirm('Make this post public? Everyone will be able to see it.')) return;
      fields.confirm = '1';
    }
    say('');
    save.disabled = true;
    post('/api/post/' + encodeURIComponent(target.id) + '/audience', fields).then(function (res) {
      save.disabled = false;
      if (!res.ok) { say(res.data.error || 'Could not change that.', true); return; }
      window.location.reload();
    }).catch(function () { save.disabled = false; say('Could not change that. Try again.', true); });
  });
})();
