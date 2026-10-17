/*
 * The guildmasters' Flair page (templates/guild/flair.html, routes/guild_flair.py): add, save, move and delete flairs.
 * Every change is a POST with the form key; the page reloads to show the guild's list as the server keeps it.
 */
(function () {
  var root = document.getElementById('guild-flair');
  if (!root) return;
  var base = root.getAttribute('data-base');
  var errorBox = document.getElementById('flair-error');
  function send(url, fields) {
    var body = new URLSearchParams(fields || {});
    body.set('formkey', formkey());
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (r.status === 204) return {};
        return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || 'That didn’t work.'); return d; });
      });
  }
  function fail(e) { errorBox.textContent = e.message; errorBox.hidden = false; }
  function reload() { window.location.reload(); }
  document.getElementById('flair-add').addEventListener('submit', function () {
    send(base + '/add', { name: document.getElementById('flair-add-name').value, colour: document.getElementById('flair-add-colour').value }).then(reload).catch(fail);
  });
  document.getElementById('flair-list').addEventListener('click', function (event) {
    var b = event.target.closest('button');
    if (!b) return;
    if (b.dataset.flairSave) {
      var id = b.dataset.flairSave;
      send(base + '/' + id + '/edit', { name: document.getElementById('flair-name-' + id).value, colour: document.getElementById('flair-colour-' + id).value }).then(reload).catch(fail);
    } else if (b.dataset.flairMove) {
      send(base + '/' + b.dataset.flairMove + '/move', { step: b.dataset.step }).then(reload).catch(fail);
    } else if (b.dataset.flairDelete) {
      if (!window.confirm('Delete the flair ' + b.dataset.name + '? Posts that have it lose it; no post is deleted.')) return;
      send(base + '/' + b.dataset.flairDelete + '/delete').then(reload).catch(fail);
    }
  });
})();
