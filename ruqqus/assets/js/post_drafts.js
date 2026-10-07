/*
 * Save draft, Schedule and the Drafts list on Create a post.
 *
 * A draft is the composer's title, link, text, guilds and options, saved on the
 * server (POST /api/drafts). Scheduling saves it with a publish time; the
 * scheduler (scripts/publish_scheduled.py) posts it then. Times are shown and
 * picked in the browser's time zone and sent as UTC epoch seconds.
 * Uploaded images are not kept in drafts.
 */
(function () {
  'use strict';

  var form = document.getElementById('submitform');
  var saveButton = document.getElementById('save-draft');
  if (!form || !saveButton) return;

  var MIN_LEAD_MINUTES = 6;      // the server needs 5; leave room for the click
  var MAX_LEAD_DAYS = 365;

  var el = function (id) { return document.getElementById(id); };
  var idField = el('draft-id');
  var statusNode = el('draft-status');
  var panel = el('schedule-panel');
  var scheduleInput = el('schedule-at');
  var scheduleError = el('schedule-error');
  var statusTimer = null;

  // ------------------------------------------------------------------ helpers

  function formatTime(epoch) {
    return new Date(epoch * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  }

  function say(message, isError) {
    statusNode.textContent = message || '';
    statusNode.classList.toggle('text-danger', !!isError);
    statusNode.classList.toggle('text-muted', !isError);
    clearTimeout(statusTimer);
    if (message) statusTimer = setTimeout(function () { statusNode.textContent = ''; }, 7000);
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  // YYYY-MM-DDTHH:mm in local time, what <input type="datetime-local"> wants
  function localValue(date) {
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) +
      'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function request(url, body, method) {
    return fetch(url, { method: method || 'POST', body: body, credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); });
  }

  function collect(publishUtc) {
    var data = new FormData();
    data.append('formkey', formkey());
    data.append('title', el('post-title').value);
    data.append('url', el('post-URL').value);
    var body = el('post-body');
    data.append('body', body ? body.value : '');
    Array.prototype.forEach.call(form.querySelectorAll('#forward_guild_inputs input[name="forward_guilds"]'), function (i) {
      data.append('forward_guilds', i.value);
    });
    var permission = form.querySelector('input[name="comment_permission"]:checked');
    if (permission) data.append('comment_permission', permission.value);
    ['paid_partnership', 'made_with_ai', 'anonymous'].forEach(function (name) {
      if (form.querySelector('input[type="checkbox"][name="' + name + '"]:checked')) data.append(name, 'true');
    });
    var coauthors = form.querySelector('input[name="coauthors"]');
    if (coauthors && coauthors.value.trim()) data.append('coauthors', coauthors.value.trim());
    var sensitive = el('sensitiveCheck');
    if (sensitive && sensitive.checked) data.append('sensitive', 'true');
    if (idField.value) data.append('draft_id', idField.value);
    if (publishUtc) data.append('publish_utc', String(publishUtc));
    return data;
  }

  // ------------------------------------------------------------------ saving

  function save(publishUtc) {
    return request('/api/drafts', collect(publishUtc)).then(function (res) {
      if (!res.ok) { say(res.data.error || 'Could not save the draft.', true); return null; }
      idField.value = res.data.id;
      // a reload (or the back button) reopens this draft
      try { history.replaceState(null, '', '/submit?draft=' + res.data.id); } catch (err) { /* not important */ }
      refreshCount();
      return res.data;
    }).catch(function () { say('Could not save the draft. Check your connection.', true); return null; });
  }

  function setBanner(kind, text) {
    var banner = el('draft-banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'draft-banner';
      form.querySelector('.body').insertBefore(banner, form.querySelector('.body').firstChild);
    }
    banner.className = 'alert ' + (kind === 'scheduled' ? 'alert-info' : 'alert-light border') + ' text-small py-2';
    banner.textContent = text;
  }

  saveButton.addEventListener('click', function () {
    var file = el('file-upload');
    var media = el('post-media');      // a picture already uploaded to linked storage
    var hasFile = (file && file.files && file.files.length) || (media && media.value);
    save(null).then(function (data) {
      if (!data) return;
      say(hasFile ? 'Draft saved. Images are not kept in drafts, so add it again later.' : 'Draft saved.');
      setBanner('draft', 'Editing a draft. Post it now, or schedule it.');
    });
  });

  // ---------------------------------------------------------------- scheduling

  function closePanel() {
    panel.classList.add('d-none');
    el('schedule-open').setAttribute('aria-expanded', 'false');
  }

  el('schedule-open').addEventListener('click', function () {
    if (!panel.classList.contains('d-none')) { closePanel(); return; }
    var now = new Date();
    var min = new Date(now.getTime() + MIN_LEAD_MINUTES * 60000);
    var max = new Date(now.getTime() + MAX_LEAD_DAYS * 86400000);
    scheduleInput.min = localValue(min);
    scheduleInput.max = localValue(max);
    if (!scheduleInput.value) {
      var suggestion = new Date(now.getTime() + 3600000);
      suggestion.setMinutes(0, 0, 0);
      suggestion.setHours(suggestion.getHours() + 1);
      scheduleInput.value = localValue(suggestion);
    }
    var zone = '';
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (err) { /* older browsers */ }
    el('schedule-hint').textContent = zone ? 'Your time zone: ' + zone : 'In your local time.';
    scheduleError.textContent = '';
    panel.classList.remove('d-none');
    el('schedule-open').setAttribute('aria-expanded', 'true');
    scheduleInput.focus();
  });

  el('schedule-cancel').addEventListener('click', closePanel);
  panel.addEventListener('keydown', function (event) { if (event.key === 'Escape') closePanel(); });

  el('schedule-confirm').addEventListener('click', function () {
    var when = new Date(scheduleInput.value);
    if (!scheduleInput.value || isNaN(when.getTime())) { scheduleError.textContent = 'Pick a date and time.'; return; }
    var epoch = Math.floor(when.getTime() / 1000);
    if (!el('post-title').value.trim()) { scheduleError.textContent = 'A scheduled post needs a title.'; return; }
    scheduleError.textContent = '';
    save(epoch).then(function (data) {
      if (!data) {
        scheduleError.textContent = statusNode.textContent;
        return;
      }
      closePanel();
      var label = 'Scheduled for ' + formatTime(data.publish_utc) + '.';
      say(label);
      setBanner('scheduled', label + ' Saving a draft unschedules it; Schedule changes the time.');
    });
  });

  // ------------------------------------------------------------------- drafts

  var countBadge = el('drafts-count');

  function refreshCount() {
    fetch('/api/drafts', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        countBadge.textContent = String(data.drafts.length);
        countBadge.classList.toggle('d-none', !data.drafts.length);
      })
      .catch(function () { /* the badge is a nicety */ });
  }

  function row(draft, list) {
    var r = document.createElement('div');
    r.className = 'draft-row';

    var main = document.createElement('div');
    main.className = 'draft-main';
    var title = document.createElement('div');
    title.className = 'draft-title';
    title.textContent = draft.title || 'Untitled';
    var meta = document.createElement('div');
    meta.className = 'draft-meta text-small text-muted';
    if (draft.status === 'scheduled' || draft.status === 'publishing') {
      meta.textContent = (draft.status === 'publishing' ? 'Publishing now' : 'Scheduled for ' + formatTime(draft.publish_utc));
    } else if (draft.status === 'failed') {
      meta.textContent = 'Could not be published: ' + (draft.error || 'unknown reason');
      meta.classList.replace('text-muted', 'text-danger');
    } else {
      meta.textContent = 'Draft, saved ' + formatTime(draft.updated_utc);
    }
    main.appendChild(title);
    main.appendChild(meta);
    r.appendChild(main);

    var actions = document.createElement('div');
    actions.className = 'draft-actions';
    if (draft.status !== 'publishing') {
      var open = document.createElement('a');
      open.className = 'btn btn-sm btn-secondary';
      open.href = '/submit?draft=' + draft.id;
      open.textContent = 'Open';
      actions.appendChild(open);
      if (draft.status === 'scheduled') {
        var un = document.createElement('button');
        un.type = 'button';
        un.className = 'btn btn-sm btn-link text-muted';
        un.textContent = 'Unschedule';
        un.addEventListener('click', function () {
          var data = new FormData(); data.append('formkey', formkey());
          request('/api/drafts/' + draft.id + '/unschedule', data).then(function () { load(list); refreshCount(); });
        });
        actions.appendChild(un);
      }
      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn btn-sm btn-link text-danger';
      del.textContent = 'Delete';
      del.addEventListener('click', function () {
        var data = new FormData(); data.append('formkey', formkey());
        request('/api/drafts/' + draft.id + '/delete', data).then(function (res) {
          if (!res.ok) return;
          if (String(draft.id) === idField.value) {
            idField.value = '';
            try { history.replaceState(null, '', '/submit'); } catch (err) { /* not important */ }
            var banner = el('draft-banner');
            if (banner) banner.parentNode.removeChild(banner);
          }
          load(list);
          refreshCount();
        });
      });
      actions.appendChild(del);
    }
    r.appendChild(actions);
    return r;
  }

  function load(list) {
    fetch('/api/drafts', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        list.textContent = '';
        if (!data.drafts.length) {
          var none = document.createElement('p');
          none.className = 'text-muted mb-0';
          none.textContent = 'Nothing saved yet. Save draft keeps what you have written here, and Schedule posts it later.';
          list.appendChild(none);
          return;
        }
        data.drafts.forEach(function (d) { list.appendChild(row(d, list)); });
      })
      .catch(function () { list.textContent = 'Could not load your drafts.'; });
  }

  $('#draftsModal').on('show.bs.modal', function () { load(el('drafts-list')); });

  // times the server drew are in UTC; show them in the viewer's time zone
  Array.prototype.forEach.call(document.querySelectorAll('.js-local-time[data-utc]'), function (n) {
    n.textContent = formatTime(Number(n.getAttribute('data-utc')));
  });

  refreshCount();
  if (typeof checkForRequired === 'function') checkForRequired();   // a reopened draft already has a title
})();
