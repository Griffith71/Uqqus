/*
 * "Who can see this" in the menu of one's own post (partials/audience_modal.html, routes/circles.py).
 * The menu item carries the post's id, its audience (0 Public, 4 Circles) and its circles (`data-circles`, tokens
 * separated by commas); the sheet shows the same field as the composer (composer_card.js `AudienceField`) and saves it.
 * The server asks first when something would happen the author should know about (the circles reach nobody yet, or
 * copies of a public post are taken down); making a post public again asks here. Text only, no HTML.
 */
(function () {
  'use strict';

  var modal = document.getElementById('audienceModal');
  if (!modal) return;

  var save = document.getElementById('audience-save');
  var status = document.getElementById('audience-status');
  var field = modal.querySelector('[data-audience-field]');
  var target = null;

  function say(text, isError) {
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  function post(path, fields) {
    var body = new URLSearchParams();
    body.set('formkey', formkey());
    Object.keys(fields || {}).forEach(function (k) {
      var value = fields[k];
      if (Array.isArray(value)) value.forEach(function (one) { body.append(k, one); });
      else body.set(k, value);
    });
    return fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; }); });
  }

  function same(a, b) {
    return a.audience === b.audience && a.circles.slice().sort().join(',') === b.circles.slice().sort().join(',');
  }

  document.addEventListener('click', function (event) {
    // the menu entries (.audience-item) and the "Change" button in the Edit form (.audience-open)
    var item = event.target.closest ? event.target.closest('.audience-item, .audience-open') : null;
    if (!item || !field || !window.AudienceField) return;
    event.preventDefault();
    var tokens = (item.getAttribute('data-circles') || '').split(',').filter(function (t) { return t; });
    target = { id: item.getAttribute('data-post-id'), audience: item.getAttribute('data-audience') === '4' ? '4' : '0', circles: tokens };
    window.AudienceField.set(field, target.audience, target.circles);
    say('');
    $('#audienceModal').modal('show');
  });

  function send(fields) {
    say('');
    save.disabled = true;
    post('/api/post/' + encodeURIComponent(target.id) + '/audience', fields).then(function (res) {
      save.disabled = false;
      // the server asks first (an empty circle, copies taken down): the answer carries the sentence
      if (res.status === 409 && res.data.confirm && !fields.confirm) {
        if (window.confirm(res.data.error + ' Save anyway?')) { fields.confirm = '1'; send(fields); }
        return;
      }
      if (!res.ok) { say(res.data.error || 'Could not change that.', true); return; }
      window.location.reload();
    }).catch(function () { save.disabled = false; say('Could not change that. Try again.', true); });
  }

  save.addEventListener('click', function () {
    if (!target || !field) { say('Choose who can see this.', true); return; }
    var chosen = window.AudienceField.read(field);
    if (chosen.audience === '4' && !chosen.circles.length) { say('Choose at least one circle, or make it public.', true); return; }
    if (same(chosen, target)) { $('#audienceModal').modal('hide'); return; }
    var fields = { audience: chosen.audience, circles: chosen.circles };
    if (chosen.audience === '0' && target.audience !== '0') {
      if (!window.confirm('Make this post public? Everyone will be able to see it.')) return;
      fields.confirm = '1';
    }
    send(fields);
  });
})();
