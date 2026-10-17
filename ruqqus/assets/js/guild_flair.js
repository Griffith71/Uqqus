/*
 * Guild flair in the browser (helpers/guild_flair.py, routes/guild_flair.py):
 * - the Forward dialog (forward_post_modal.html): once a guild is named, its flairs fill a picker; a guild that
 *   requires one says so (the server refuses a forward without it either way);
 * - "Set flair" on a guild's copy of a post (partials/flair_modal.html, the post menus' [data-set-flair]).
 * Names come from the server and go in as text (option labels), never as HTML.
 */
(function () {
  'use strict';

  function flairsOf(guild) {
    return fetch('/api/guild/' + encodeURIComponent(guild) + '/flairs', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function fill(select, data, current) {
    select.textContent = '';
    var none = document.createElement('option');
    none.value = '';
    none.textContent = data.require_flair ? 'Pick a flair' : 'No flair';
    select.appendChild(none);
    data.flairs.forEach(function (f) {
      var option = document.createElement('option');
      option.value = String(f.id);
      option.textContent = f.name;
      if (current && String(current) === String(f.id)) option.selected = true;
      select.appendChild(option);
    });
  }

  // --- the Forward dialogs (a post's and a comment's) ----------------------------------------------------------
  function picker(inputId, fieldId, selectId, requiredId) {
    var guildInput = document.getElementById(inputId);
    var field = document.getElementById(fieldId);
    if (!guildInput || !field) return;
    var select = document.getElementById(selectId);
    var required = document.getElementById(requiredId);
    var timer = null;
    var asked = '';
    guildInput.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var name = guildInput.value.trim().replace(/^\+/, '');
        if (name === asked) return;
        asked = name;
        if (!name) { field.hidden = true; return; }
        flairsOf(name).then(function (data) {
          if (asked !== name) return;                  // a newer name was typed meanwhile
          if (!data || !data.flairs.length) { field.hidden = true; return; }
          fill(select, data);
          required.hidden = !data.require_flair;
          field.hidden = false;
        });
      }, 350);
    });
  }
  picker('forward-guild-name', 'forward-flair-field', 'forward-flair', 'forward-flair-required');
  picker('forward-comment-guild-name', 'forward-comment-flair-field', 'forward-comment-flair', 'forward-comment-flair-required');

  // --- Set flair on a post ---------------------------------------------------------------------------------------
  var modal = document.getElementById('setFlairModal');
  if (!modal) return;
  var setSelect = document.getElementById('set-flair-select');
  var errorBox = document.getElementById('set-flair-error');
  var saveButton = document.getElementById('set-flair-save');
  var postId = null;

  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-set-flair]');
    if (!opener) return;
    event.preventDefault();
    postId = opener.getAttribute('data-set-flair');
    var guild = opener.getAttribute('data-guild');
    document.getElementById('set-flair-guild').textContent = '+' + guild;
    errorBox.hidden = true;
    setSelect.textContent = '';
    flairsOf(guild).then(function (data) {
      if (!data) { errorBox.textContent = 'Could not load the flairs.'; errorBox.hidden = false; return; }
      fill(setSelect, data);
      if (!data.flairs.length) { errorBox.textContent = '+' + guild + ' has no flairs yet.'; errorBox.hidden = false; }
    });
    $('#setFlairModal').modal('show');
  });

  saveButton.addEventListener('click', function () {
    if (!postId) return;
    var body = new URLSearchParams({ flair: setSelect.value, formkey: typeof formkey === 'function' ? formkey() : '' });
    saveButton.disabled = true;
    fetch('/api/post/' + postId + '/flair', { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        saveButton.disabled = false;
        if (!res.ok) { errorBox.textContent = res.data.error || 'That didn’t work.'; errorBox.hidden = false; return; }
        window.location.reload();
      })
      .catch(function () { saveButton.disabled = false; errorBox.textContent = 'That didn’t work.'; errorBox.hidden = false; });
  });
})();
