/*
 * "Add to curation" (routes/curations.py `curations_mine`, `post_add_to_curation`; helpers/curation_members.py).
 *
 * One delegated click handler for every `[data-curation-post]` menu item. It opens the sheet
 * (partials/curation_picker_modal.html), asks which of the member's curations already hold the post's author
 * and guild, and adds on a tap. The author is offered only when the server names one (never for an anonymous
 * post). Every name is drawn with textContent.
 */
(function () {
  'use strict';

  var modal = document.getElementById('curationPickerModal');
  if (!modal) return;

  var list = document.getElementById('curation-picker-list');
  var status = document.getElementById('curation-picker-status');
  var createLink = document.getElementById('curation-picker-create');
  var authorRow = document.getElementById('curation-picker-author-row');
  var guildRow = document.getElementById('curation-picker-guild-row');
  var authorInput = document.getElementById('curation-picker-author');
  var guildInput = document.getElementById('curation-picker-guild');
  var authorLabel = document.getElementById('curation-picker-author-label');
  var guildLabel = document.getElementById('curation-picker-guild-label');
  var postId = null;
  var info = null;
  var token = 0;                       // a late answer for a sheet that was closed and reopened is ignored

  function say(text, bad) {
    status.textContent = text || '';
    status.className = 'text-small mt-2' + (bad ? ' text-danger' : ' text-muted');
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function what() { return guildInput.checked ? 'guild' : 'author'; }

  function draw() {
    list.textContent = '';
    if (!info) return;
    if (!info.curations.length) {
      list.appendChild(make('p', 'text-muted mb-0', 'You have no curations yet.'));
      return;
    }
    var kind = what();
    info.curations.forEach(function (c) {
      var has = kind === 'author' ? c.has_author : c.has_guild;
      var blocked = kind === 'guild' && !c.guild_ok && !has;
      var row = make('button', 'btn btn-block text-left curation-picker-row' + (has ? ' btn-secondary' : ' btn-light'));
      row.type = 'button';
      row.disabled = has || blocked;
      row.appendChild(make('span', 'font-weight-bold', c.name));
      row.appendChild(make('span', 'text-small ml-2 ' + (has ? 'text-success' : 'text-muted'),
        has ? 'Added' : (blocked ? 'Private guilds need a private curation' : (c.private ? 'Private' : 'Public'))));
      row.addEventListener('click', function () { add(c, row); });
      list.appendChild(row);
    });
  }

  function add(curation, row) {
    var kind = what();
    row.disabled = true;
    say('Adding...');
    var body = new URLSearchParams();
    body.set('formkey', formkey());
    body.set('curation', curation.slug);
    body.set('what', kind);
    fetch('/api/post/' + encodeURIComponent(postId) + '/add_to_curation', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (!res.ok) { row.disabled = false; say(res.data.error || 'Could not add it.', true); return; }
        if (kind === 'author') curation.has_author = true; else curation.has_guild = true;
        say(res.data.message || 'Added.');
        draw();
      })
      .catch(function () { row.disabled = false; say('Could not add it. Try again.', true); });
  }

  function open(id) {
    postId = id;
    info = null;
    var mine = ++token;
    list.textContent = '';
    say('Loading...');
    authorRow.classList.add('d-none');
    guildRow.classList.add('d-none');
    createLink.classList.add('d-none');
    $(modal).modal('show');
    fetch('/api/curations/mine?post=' + encodeURIComponent(id), { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (mine !== token) return;
        if (!res.ok) { say(res.data.error || 'Could not load your curations.', true); return; }
        info = res.data;
        if (info.author) {
          authorRow.classList.remove('d-none');
          authorLabel.textContent = 'The author, @' + info.author.username;
        }
        if (info.guild) {
          guildRow.classList.remove('d-none');
          guildLabel.textContent = 'The guild, +' + info.guild.name;
        }
        if (info.author) authorInput.checked = true; else guildInput.checked = true;
        if (typeof info.create_url === 'string' && info.create_url.charAt(0) === '/') {
          createLink.href = info.create_url;
          createLink.classList.remove('d-none');
        }
        say('');
        draw();
      })
      .catch(function () { if (mine === token) say('Could not load your curations. Try again.', true); });
  }

  authorInput.addEventListener('change', draw);
  guildInput.addEventListener('change', draw);
  $(modal).on('hidden.bs.modal', function () { token++; });

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('[data-curation-post]') : null;
    if (!item) return;
    event.preventDefault();
    var id = item.getAttribute('data-curation-post');
    if (/^[0-9a-z]{1,10}$/.test(id)) open(id);
  });
})();
