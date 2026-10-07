/*
 * Co-authors (templates/partials/coauthors_modal.html, routes/coauthors.py).
 *
 * A post's author opens "Co-authors" from the post's menu: the sheet lists who co-authors it and who is
 * still to answer, takes more usernames to invite, and removes a co-author or an invitation. Every name
 * is drawn as text.
 */
(function () {
  'use strict';

  var modal = document.getElementById('coauthorsModal');
  if (!modal) return;

  var list = document.getElementById('coauthors-list');
  var form = document.getElementById('coauthors-form');
  var input = document.getElementById('coauthors-input');
  var button = document.getElementById('coauthors-invite');
  var status = document.getElementById('coauthors-status');
  var postId = null;

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
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); });
  }

  function row(person) {
    var item = document.createElement('div');
    item.className = 'coauthor-row d-flex align-items-center py-2';
    var avatar = document.createElement('img');
    avatar.className = 'rounded-circle mr-2';
    avatar.width = avatar.height = 32;
    avatar.alt = '';
    if (typeof person.profile_url === 'string' && /^(https?:\/\/|\/)/.test(person.profile_url)) avatar.src = person.profile_url;
    var name = document.createElement('div');
    name.className = 'flex-grow-1 text-truncate';
    var strong = document.createElement('span');
    strong.className = 'font-weight-bold';
    strong.textContent = '@' + person.username;
    name.appendChild(strong);
    if (person.status !== 'accepted') {
      var waiting = document.createElement('span');
      waiting.className = 'text-muted text-small ml-2';
      waiting.textContent = 'invited';
      name.appendChild(waiting);
    }
    var remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn btn-sm btn-outline-secondary ml-2';
    remove.textContent = person.status === 'accepted' ? 'Remove' : 'Cancel';
    remove.addEventListener('click', function () {
      remove.disabled = true;
      post('/api/coauthor/' + postId + '/remove', { username: person.username }).then(function (res) {
        if (!res.ok) { remove.disabled = false; say(res.data.error || 'Could not do that.', true); return; }
        say('');
        load();
      }).catch(function () { remove.disabled = false; say('Could not do that. Try again.', true); });
    });
    item.appendChild(avatar);
    item.appendChild(name);
    item.appendChild(remove);
    return item;
  }

  function draw(data) {
    list.textContent = '';
    if (!data.coauthors.length) {
      var none = document.createElement('p');
      none.className = 'text-muted text-small mb-0';
      none.textContent = 'No co-authors yet.';
      list.appendChild(none);
    }
    data.coauthors.forEach(function (person) { list.appendChild(row(person)); });
    form.hidden = !data.can_invite;
    if (!data.can_invite && data.coauthors.length >= data.limit) say('A post can have ' + data.limit + ' co-authors at most.');
  }

  function load() {
    fetch('/api/coauthor/' + postId + '/list', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('failed'); return r.json(); })
      .then(draw)
      .catch(function () { list.textContent = ''; say('Could not load the co-authors. Try again.', true); });
  }

  form.addEventListener('submit', function () {
    var names = input.value.trim();
    if (!names) return;
    button.disabled = true;
    say('');
    post('/api/coauthor/' + postId + '/invite', { usernames: names }).then(function (res) {
      button.disabled = false;
      var data = res.data || {};
      if (data.error) { say(data.error, true); return; }
      var problems = (data.errors || []).join(' ');
      if (data.invited && data.invited.length) {
        input.value = '';
        say('Invited ' + data.invited.map(function (n) { return '@' + n; }).join(', ') + '.' + (problems ? ' ' + problems : ''), !!problems);
      } else {
        say(problems || 'Nobody was invited.', true);
      }
      load();
    }).catch(function () { button.disabled = false; say('Could not invite. Try again.', true); });
  });

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('.coauthors-item') : null;
    if (!item) return;
    event.preventDefault();
    postId = item.getAttribute('data-post-id');
    input.value = '';
    say('');
    list.textContent = '';
    form.hidden = false;
    $('#coauthorsModal').modal('show');
    load();
  });
})();
