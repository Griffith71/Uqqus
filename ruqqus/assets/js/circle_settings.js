/*
 * Settings > Circles (templates/settings_circle.html, settings_circle_one.html, settings_circles.html;
 * routes/circles.py). Two kinds of control, both sent with the form key:
 *   - <form data-circle-form action="..."> posts its fields; an answer with `go` opens that page, otherwise the page
 *     reloads; a refusal is written in the form's [data-circle-status] (text only).
 *   - <button data-circle-post="..."> posts the button's data-field-* values (data-field-username="x" sends
 *     username=x), after data-confirm when it has one; refusals go to the page's #circle-status.
 */
(function () {
  'use strict';

  function say(box, text) {
    if (!box) return;
    box.textContent = text || '';
    box.className = 'text-small mt-2 ' + (text ? 'text-danger' : 'text-muted');
  }

  function send(url, body) {
    body.set('formkey', formkey());
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; }, function () { return { ok: r.ok, data: {} }; });
    });
  }

  function done(res, box) {
    if (!res.ok) { say(box, res.data.error || 'Could not do that. Try again.'); return; }
    if (res.data.go) window.location.href = res.data.go;
    else window.location.reload();
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.matches || !form.matches('form[data-circle-form]')) return;
    event.preventDefault();
    var box = form.querySelector('[data-circle-status]');
    var button = form.querySelector('button[type="submit"]');
    say(box, '');
    if (button) button.disabled = true;
    send(form.getAttribute('action'), new FormData(form)).then(function (res) {
      if (button) button.disabled = false;
      done(res, box);
    }).catch(function () { if (button) button.disabled = false; say(box, 'Could not do that. Try again.'); });
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('button[data-circle-post]') : null;
    if (!button) return;
    event.preventDefault();
    var question = button.getAttribute('data-confirm');
    if (question && !window.confirm(question)) return;
    var body = new FormData();
    Array.prototype.forEach.call(button.attributes, function (attr) {
      if (attr.name.indexOf('data-field-') === 0) body.set(attr.name.slice('data-field-'.length), attr.value);
    });
    var box = document.getElementById('circle-status');
    say(box, '');
    button.disabled = true;
    send(button.getAttribute('data-circle-post'), body).then(function (res) {
      button.disabled = false;
      done(res, box);
    }).catch(function () { button.disabled = false; say(box, 'Could not do that. Try again.'); });
  });
})();
