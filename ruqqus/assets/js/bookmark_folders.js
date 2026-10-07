/*
 * Bookmark folders (routes/bookmark_folders.py, helpers/bookmark_folders.py).
 *
 * Two small parts:
 *   - "Add to folder" in the menu of a post or comment opens the sheet
 *     (partials/bookmark_folder_modal.html): pick a folder or Unsorted, or make a new one; the item
 *     is bookmarked as it is filed, and its bookmark icon is switched on;
 *   - on the History page's Bookmarked tab the folder row (templates/history.html) makes, renames
 *     and deletes folders.
 * Every name is drawn as text.
 */
(function () {
  'use strict';

  function toast(error, text) {
    var id = error ? 'toast-post-error' : 'toast-post-success';
    var box = document.getElementById(id + '-text');
    if (!box || !window.$) return;
    box.textContent = text;
    $('#' + id).toast('dispose');
    $('#' + id).toast('show');
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
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); });
  }

  // --- the folder row on the Bookmarked tab -------------------------------------------------

  var bar = document.getElementById('bookmark-folders');
  if (bar) {
    var type = bar.getAttribute('data-type') || 'all';
    var current = bar.getAttribute('data-folder-id');
    var currentName = bar.getAttribute('data-folder-name') || '';
    var back = '/history?type=' + encodeURIComponent(type);

    var fresh = document.getElementById('folder-new');
    if (fresh) fresh.addEventListener('click', function () {
      var name = window.prompt('Name your folder');
      if (name === null) return;
      post('/bookmarks/folders', { name: name }).then(function (res) {
        if (!res.ok) { toast(true, res.data.error || 'Could not make the folder.'); return; }
        window.location.href = back + '&folder=' + res.data.id;
      }).catch(function () { toast(true, 'Could not make the folder. Try again.'); });
    });

    var rename = document.getElementById('folder-rename');
    if (rename && current) rename.addEventListener('click', function () {
      var name = window.prompt('Rename this folder', currentName);
      if (name === null) return;
      post('/bookmarks/folders/' + current + '/rename', { name: name }).then(function (res) {
        if (!res.ok) { toast(true, res.data.error || 'Could not rename the folder.'); return; }
        window.location.reload();
      }).catch(function () { toast(true, 'Could not rename the folder. Try again.'); });
    });

    var remove = document.getElementById('folder-delete');
    if (remove && current) remove.addEventListener('click', function () {
      if (!window.confirm('Delete this folder? Its bookmarks are kept, as unsorted.')) return;
      post('/bookmarks/folders/' + current + '/delete').then(function (res) {
        if (!res.ok) { toast(true, res.data.error || 'Could not delete the folder.'); return; }
        window.location.href = back;
      }).catch(function () { toast(true, 'Could not delete the folder. Try again.'); });
    });
  }

  // --- the "Add to folder" sheet ------------------------------------------------------------

  var modal = document.getElementById('bookmarkFolderModal');
  if (!modal) return;

  var list = document.getElementById('bookmark-folder-list');
  var form = document.getElementById('bookmark-folder-form');
  var input = document.getElementById('bookmark-folder-input');
  var button = document.getElementById('bookmark-folder-create');
  var status = document.getElementById('bookmark-folder-status');
  var target = null;

  function say(text, isError) {
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  // the bookmark icons of this item, wherever the page draws them (card, mobile row, comment sheet)
  function showSaved(kind, id) {
    ['', 'mobile-', 'modal-'].forEach(function (part) {
      var off = document.getElementById('bookmark-' + part + id);
      var on = document.getElementById('unbookmark-' + part + id);
      if (off) off.classList.add('d-none');
      if (on) on.classList.remove('d-none');
    });
  }

  function file(folderId) {
    say('');
    post('/bookmarks/move', { kind: target.kind, id: target.id, folder_id: folderId === null ? '' : String(folderId) }).then(function (res) {
      if (!res.ok) { say(res.data.error || 'Could not do that.', true); return; }
      showSaved(target.kind, target.id);
      $('#bookmarkFolderModal').modal('hide');
      toast(false, res.data.message);
      // on a filtered Bookmarked tab the item may no longer belong there
      var row = document.getElementById('bookmark-folders');
      if (row && row.getAttribute('data-folder-kind') !== 'all') window.setTimeout(function () { window.location.reload(); }, 700);
    }).catch(function () { say('Could not do that. Try again.', true); });
  }

  function choice(label, folderId, isCurrent, icon) {
    var item = document.createElement('button');
    item.type = 'button';
    item.className = 'bookmark-folder-row' + (isCurrent ? ' active' : '');
    var mark = document.createElement('i');
    mark.className = 'fas ' + (icon || 'fa-folder') + ' fa-fw mr-2';
    var name = document.createElement('span');
    name.className = 'flex-grow-1 text-left text-truncate';
    name.textContent = label;
    item.appendChild(mark);
    item.appendChild(name);
    if (isCurrent) {
      var check = document.createElement('i');
      check.className = 'fas fa-check ml-2';
      item.appendChild(check);
    }
    item.addEventListener('click', function () { file(folderId); });
    return item;
  }

  function draw(data) {
    list.textContent = '';
    list.appendChild(choice('Unsorted', null, !!data.bookmarked && data.current === null, 'fa-bookmark'));
    data.folders.forEach(function (folder) { list.appendChild(choice(folder.name, folder.id, data.current === folder.id)); });
    form.hidden = data.folders.length >= data.limit;
    if (form.hidden) say('You can have ' + data.limit + ' folders at most.');
  }

  function load() {
    fetch('/bookmarks/folders?kind=' + encodeURIComponent(target.kind) + '&id=' + encodeURIComponent(target.id), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('failed'); return r.json(); })
      .then(draw)
      .catch(function () { list.textContent = ''; say('Could not load your folders. Try again.', true); });
  }

  form.addEventListener('submit', function () {
    var name = input.value.trim();
    if (!name) return;
    button.disabled = true;
    say('');
    post('/bookmarks/folders', { name: name }).then(function (res) {
      button.disabled = false;
      if (!res.ok) { say(res.data.error || 'Could not make the folder.', true); return; }
      input.value = '';
      file(res.data.id);
    }).catch(function () { button.disabled = false; say('Could not make the folder. Try again.', true); });
  });

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('.bookmark-folder-item') : null;
    if (!item) return;
    event.preventDefault();
    target = { kind: item.getAttribute('data-kind'), id: item.getAttribute('data-id') };
    input.value = '';
    say('');
    list.textContent = '';
    form.hidden = false;
    $('#bookmarkFolderModal').modal('show');
    load();
  });
})();
