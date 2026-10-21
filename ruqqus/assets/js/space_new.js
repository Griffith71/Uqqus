/*
 * Starting a Space (templates/space_new.html). The kind (board, live thread, call) shows the fields that fit it
 * (`data-space-for`); the guild chips write one hidden `forward_guilds` field per guild (assets/js/where_to_post.js
 * searches guilds and presses Add); the form goes to POST /api/spaces (routes/spaces.py `space_create`), then to the
 * Space's own address it answers with. Text only: no HTML is written.
 */
(function () {
  'use strict';

  var form = document.getElementById('space-form');
  if (!form) return;
  var input = document.getElementById('space-guild-input');
  var add = document.getElementById('space-guild-add');
  var chips = document.getElementById('space-guild-chips');
  var fields = document.getElementById('space-guild-inputs');
  var errorBox = document.getElementById('space-error');
  var submit = document.getElementById('space-submit');
  var when = form.querySelector('[data-space-when]');
  var guilds = [];
  var MAX = 20;

  function render() {
    chips.textContent = '';
    fields.textContent = '';
    guilds.forEach(function (name, i) {
      var chip = document.createElement('span');
      chip.className = 'badge badge-pill badge-primary ic-chip mr-1';
      chip.textContent = '+' + name;
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'ic-chip-remove';
      remove.setAttribute('aria-label', 'Do not post to +' + name);
      remove.textContent = '×';
      remove.addEventListener('click', function () { guilds.splice(i, 1); render(); });
      chip.appendChild(remove);
      chips.appendChild(chip);
      var field = document.createElement('input');
      field.type = 'hidden';
      field.name = 'forward_guilds';
      field.value = name;
      fields.appendChild(field);
    });
  }

  function addGuild() {
    var name = input.value.trim().replace(/^\+/, '');
    input.value = '';
    if (!name || guilds.some(function (g) { return g.toLowerCase() === name.toLowerCase(); })) return;
    if (guilds.length >= MAX) return;
    guilds.push(name);
    render();
  }

  add.addEventListener('click', addGuild);
  input.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addGuild();
  });
  // a Space for circles is only on the profile (where_to_post.js says so on the form)
  form.addEventListener('where:circles', function () { guilds = []; render(); });

  function kind() {
    var picked = form.querySelector('input[name="space_kind"]:checked');
    return picked ? picked.value : 'board';
  }

  // the fields that fit the chosen kind: a length for a board or a live thread, video for a call
  function showKind() {
    var k = kind();
    Array.prototype.forEach.call(form.querySelectorAll('[data-space-for]'), function (node) {
      var fits = node.getAttribute('data-space-for').split(' ').indexOf(k) !== -1;
      node.hidden = !fits;
      Array.prototype.forEach.call(node.querySelectorAll('input, select'), function (field) { field.disabled = !fits; });
    });
  }
  Array.prototype.forEach.call(form.querySelectorAll('input[name="space_kind"]'), function (radio) {
    radio.addEventListener('change', showKind);
  });
  showKind();

  function label() {
    submit.textContent = when && when.value ? 'Schedule the Space' : 'Start the Space';
  }
  if (when) { when.addEventListener('input', label); when.addEventListener('change', label); }

  function fail(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    submit.disabled = false;
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (!form.querySelector('#space-title').value.trim()) { fail('Give the Space a title.'); return; }
    errorBox.hidden = true;
    submit.disabled = true;
    fetch('/api/spaces', { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (response.redirected) { window.location.href = response.url; return null; }
        return response.json().then(function (body) { return { ok: response.ok, body: body }; });
      })
      .then(function (result) {
        if (!result) return;
        var url = result.body && result.body.url;
        if (!result.ok || typeof url !== 'string' || url.charAt(0) !== '/') { fail((result.body && result.body.error) || 'The Space could not be made.'); return; }
        window.location.href = url;
      })
      .catch(function () { fail('The Space could not be made. Check your connection.'); });
  });
})();
