/*
 * A guild's Wiki, Events and Resources pages (templates/guild/*.html; routes/guild_wiki.py, routes/guild_events.py).
 * Every change is a POST with the form key:
 *   <form data-guild-form action="...">     sends its fields; an error shows in the form's [role=alert]
 *   <button data-guild-post="url" data-confirm="...">   asks first when it has data-confirm
 *   <input type="checkbox" data-guild-toggle="url">     sends setting=true|false
 *   <form data-new-wiki-page="/+guild/wiki/">           opens the editor of a new page's address
 * An answer with a `url` goes there; otherwise the page reloads to show what the server keeps (a page that sets
 * window.guildPagesDone redraws itself instead: the shared-draft editor keeps what is being typed). Text only, no HTML.
 */
(function () {
  function key() { return typeof formkey === 'function' ? formkey() : ''; }

  function send(url, body) {
    body = body || new URLSearchParams();
    body.set('formkey', key());
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (r.status === 204) return {};
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error(d.error || 'That didn’t work. Try again.');
          return d;
        });
      });
  }

  function done(d) {
    if (d && d.url) window.location.href = d.url;
    else if (typeof window.guildPagesDone === 'function') window.guildPagesDone(d);   // a page that redraws itself
    else window.location.reload();
  }

  function tell(box, e) {
    if (box) { box.textContent = e.message; box.hidden = false; }
    else if (window.jQuery && document.getElementById('toast-post-error-text')) {
      document.getElementById('toast-post-error-text').textContent = e.message;
      window.jQuery('#toast-post-error').toast('dispose');
      window.jQuery('#toast-post-error').toast('show');
    } else window.alert(e.message);
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (form.hasAttribute('data-new-wiki-page')) {
      event.preventDefault();
      var slug = form.querySelector('input').value.trim().toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
      if (slug) window.location.href = form.getAttribute('data-new-wiki-page') + encodeURIComponent(slug) + '/edit';
      return;
    }
    if (!form.hasAttribute('data-guild-form')) return;
    event.preventDefault();
    var box = form.querySelector('[role=alert]');
    if (box) box.hidden = true;
    var button = form.querySelector('[type=submit]');
    if (button) button.disabled = true;
    var body = new URLSearchParams(new FormData(form));
    send(form.getAttribute('action'), body).then(done).catch(function (e) {
      if (button) button.disabled = false;
      tell(box, e);
    });
  });

  document.addEventListener('click', function (event) {
    var b = event.target.closest('[data-guild-post]');
    if (!b) return;
    event.preventDefault();
    var question = b.getAttribute('data-confirm');
    if (question && !window.confirm(question)) return;
    b.disabled = true;
    send(b.getAttribute('data-guild-post')).then(done).catch(function (e) { b.disabled = false; tell(null, e); });
  });

  document.addEventListener('change', function (event) {
    var box = event.target;
    if (!box.hasAttribute || !box.hasAttribute('data-guild-toggle')) return;
    var body = new URLSearchParams({ setting: box.checked ? 'true' : 'false' });
    send(box.getAttribute('data-guild-toggle'), body).catch(function (e) { box.checked = !box.checked; tell(null, e); });
  });
})();
