/*
 * "Who can comment" in the menu of one's own post (partials/comment_permission_modal.html, routes/post_commenting.py).
 * The menu item carries the post's id, its current setting and whether the post is anonymous; the sheet shows four
 * choices and saves one. "Accounts you follow" is not offered on an anonymous post (the server refuses it as well).
 * Text only, no HTML.
 */
(function () {
  'use strict';

  var modal = document.getElementById('commentPermissionModal');
  if (!modal) return;

  var save = document.getElementById('comment-permission-save');
  var status = document.getElementById('comment-permission-status');
  var anonymousNote = document.getElementById('comment-permission-anonymous-note');
  var followed = document.getElementById('comment-permission-option-1');
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
    var picked = modal.querySelector('input[name="comment-permission-choice"]:checked');
    return picked ? picked.value : null;
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('.comment-permission-item') : null;
    if (!item) return;
    event.preventDefault();
    target = {
      id: item.getAttribute('data-post-id'),
      mode: item.getAttribute('data-mode') || '0',
      anonymous: item.getAttribute('data-anonymous') === '1'
    };
    var radio = document.getElementById('comment-permission-option-' + target.mode);
    if (radio) radio.checked = true;
    followed.disabled = target.anonymous;
    anonymousNote.classList.toggle('d-none', !target.anonymous);
    say('');
    $('#commentPermissionModal').modal('show');
  });

  save.addEventListener('click', function () {
    var value = chosen();
    if (!target || value === null) { say('Choose who can comment.', true); return; }
    if (value === target.mode) { $('#commentPermissionModal').modal('hide'); return; }
    say('Saving\u2026');
    save.disabled = true;
    post('/api/post/' + encodeURIComponent(target.id) + '/comment_permission', { comment_permission: value }).then(function (res) {
      save.disabled = false;
      if (!res.ok) { say(res.data.error || 'Could not change that.', true); return; }
      window.location.reload();
    }).catch(function () { save.disabled = false; say('Could not change that. Try again.', true); });
  });
})();
