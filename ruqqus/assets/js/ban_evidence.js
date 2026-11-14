// The ban form's evidence picker (partials/admin_ban_form.html, routes/log_evidence_pages.py): the account's own items, drawn with textContent only, ticked
// ones sent as `evidence`. Admin pages are English.
(function () {
  var box = document.getElementById('ban-evidence');
  if (!box) return;
  var list = document.getElementById('ban-evidence-list');
  var note = document.getElementById('ban-evidence-note');
  var more = document.getElementById('ban-evidence-more');
  var submit = document.getElementById('user-ban-submit');
  var page = 0, loaded = false, max = 50, loading = false;

  var draw = function (row) {
    var label = document.createElement('label');
    label.className = 'd-block mb-1';
    var tick = document.createElement('input');
    tick.type = 'checkbox'; tick.name = 'evidence'; tick.value = row.token; tick.className = 'mr-2';
    tick.disabled = !row.citable;
    tick.addEventListener('change', function () {
      var chosen = list.querySelectorAll('input[name="evidence"]:checked').length;
      if (chosen > max) { tick.checked = false; note.hidden = false; note.textContent = 'A ban can cite up to ' + max + ' items.'; }
      if (submit) submit.disabled = false;
    });
    label.appendChild(tick);
    var kind = document.createElement('strong');
    kind.textContent = (row.kind === 'post' ? 'Post' : 'Comment') + ' ';
    label.appendChild(kind);
    var words = document.createElement('span');
    words.dir = 'auto';
    words.textContent = row.title || '(no text)';
    label.appendChild(words);
    var tags = [];
    if (row.anonymous) tags.push('anonymous');
    if (row.deleted) tags.push('deleted');
    if (row.created) tags.push(new Date(row.created * 1000).toISOString().slice(0, 10));
    if (!row.citable && row.why) tags.push("can't be cited: " + row.why);
    if (tags.length) {
      var meta = document.createElement('span');
      meta.className = 'text-muted ml-2';
      meta.textContent = '(' + tags.join(', ') + ')';
      label.appendChild(meta);
    }
    list.appendChild(label);
  };

  var load = function () {
    if (loading) return;
    loading = true;
    page += 1;
    fetch(box.dataset.url + '?page=' + page, {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); }).then(function (data) {
      max = data.max || max;
      data.rows.forEach(draw);
      if (page === 1 && !data.rows.length) { list.textContent = 'This account has no items to cite.'; }
      if (data.private) { note.hidden = false; note.textContent = data.private + ' item' + (data.private === 1 ? '' : 's') + ' in private places can\'t be cited and aren\'t listed.'; }
      more.hidden = !data.more;
      loading = false;
    }).catch(function () { list.textContent = 'The items couldn\'t be loaded.'; loading = false; });
  };

  box.addEventListener('toggle', function () { if (box.open && !loaded) { loaded = true; load(); } });
  more.addEventListener('click', load);
})();
