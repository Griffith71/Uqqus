/*
 * "Where to post" in the composers (partials/post_options.html `where_profile`, routes/search.py `guild_search`).
 *
 * The composer has My profile (a tick box, on by default) and the guilds chosen in its own chips (hidden
 * `forward_guilds` fields, written by each composer's script). This script:
 *   - searches guilds as you type in the composer's guild input and lists them under it; choosing one fills the input
 *     and presses the composer's own Add button, so the chip is made the way it always was;
 *   - shows "Who can comment" (`[data-where-comment]`) only while My profile is on: those comments are the profile's;
 *   - says where the post goes (`[data-where-line]` and the summary beside the switch);
 *   - for a post made for Circles (audience Circles), which is only ever on the profile, keeps the switch on and turns
 *     the guild input off, and tells the composer (event `where:circles` on its form) so it can drop the guilds.
 * Only text is written; nothing here makes HTML from the server's answer.
 */
(function () {
  'use strict';

  Array.prototype.forEach.call(document.querySelectorAll('[data-where]'), function (root) {
    var form = root.closest('form') || document;
    var profile = root.querySelector('.where-profile-input');
    var input = document.getElementById(root.getAttribute('data-input'));
    var add = document.getElementById(root.getAttribute('data-add'));
    var note = root.querySelector('.where-note');
    var summary = root.querySelector('[data-where-summary]');
    if (!profile || !input) return;

    var list = document.createElement('ul');
    list.className = 'where-suggest';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Guilds');
    list.hidden = true;
    var anchor = input.closest('.input-group') || input;
    anchor.parentNode.insertBefore(list, anchor.nextSibling);

    var timer = null;
    var seq = 0;
    var forCircles = false;

    function chosen() {
      return Array.prototype.map.call(form.querySelectorAll('input[name="forward_guilds"]'), function (i) { return i.value.toLowerCase(); });
    }

    function render(guilds) {
      var already = chosen();
      list.textContent = '';
      guilds.forEach(function (guild) {
        if (already.indexOf(guild.name.toLowerCase()) !== -1) return;
        var item = document.createElement('li');
        var button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'option');
        var name = document.createElement('strong');
        name.textContent = '+' + guild.name;
        var members = document.createElement('span');
        members.className = 'text-muted text-small';
        members.textContent = ' ' + guild.members + ' member' + (guild.members === 1 ? '' : 's');
        button.appendChild(name);
        button.appendChild(members);
        button.addEventListener('click', function () {
          input.value = guild.name;
          if (add) add.click();
          list.hidden = true;
          input.focus();
        });
        item.appendChild(button);
        list.appendChild(item);
      });
      list.hidden = !list.firstChild;
    }

    function search() {
      if (forCircles) { list.hidden = true; return; }
      var mine = ++seq;
      fetch('/api/guild_search?q=' + encodeURIComponent(input.value.trim()), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { guilds: [] }; })
        .then(function (data) { if (mine === seq) render(data.guilds || []); })
        .catch(function () { if (mine === seq) list.hidden = true; });
    }

    input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 150); });
    input.addEventListener('focus', search);
    input.addEventListener('blur', function () { setTimeout(function () { list.hidden = true; }, 150); });
    list.addEventListener('mousedown', function (event) { event.preventDefault(); });     // a click must land before the blur hides it

    // only a real change is written: sync() runs on every change under the form, so a write that changes nothing
    // would make the next run
    function say(el, text) {
      if (el && el.textContent !== text) el.textContent = text;
    }

    function audienceIsCircles() {
      var picked = form.querySelector('input[name="audience"]:checked');
      return !!picked && picked.value === '4';
    }

    function sync() {
      var names = Array.prototype.map.call(form.querySelectorAll('input[name="forward_guilds"]'), function (i) { return i.value; });
      var on = audienceIsCircles();
      if (on !== forCircles) {
        forCircles = on;
        if (on) form.dispatchEvent(new CustomEvent('where:circles', { bubbles: true }));
      }
      if (on) profile.checked = true;
      profile.disabled = on;
      input.disabled = on;
      if (add) add.disabled = on;
      if (on) list.hidden = true;

      Array.prototype.forEach.call(document.querySelectorAll('[data-where-comment]'), function (box) { box.hidden = !profile.checked; });

      var places = (profile.checked ? ['your profile'] : []).concat(names.map(function (n) { return '+' + n; }));
      var line = places.length ? 'Posting to ' + places.join(', ') : 'Choose where to post';
      Array.prototype.forEach.call(document.querySelectorAll('[data-where-line]'), function (el) { say(el, line); });
      say(summary, names.length ? '+ ' + names.length + ' guild' + (names.length === 1 ? '' : 's') : '');

      var message = '';
      if (on) message = 'A post for Circles is only on your profile.';
      else if (!profile.checked && !names.length) message = 'Choose your profile or at least one guild.';
      else if (!profile.checked) message = 'It is still listed on your profile, but its comments are in the guilds.';
      say(note, message);
      note.hidden = !message;
    }

    profile.addEventListener('change', sync);
    form.addEventListener('change', function () { setTimeout(sync, 0); }, true);
    if (window.MutationObserver) new MutationObserver(sync).observe(form === document ? document.body : form, { childList: true, subtree: true });
    sync();
  });
})();
