/*
 * The composer at the top of a feed (templates/partials/inline_composer.html).
 *
 * It posts to /api/vue/submit, the route the Create a post page uses, so every
 * posting rule, throttle and ban applies the same way. A post is made on your
 * own profile and forwarded to the guilds in "Forward to guilds" (one hidden
 * `forward_guilds` field each; on a guild page that guild is there from the start).
 * On success the new post's card is fetched (/inpage/post_card/<id>) and added to
 * the top of the feed.
 */
(function () {
  'use strict';

  var form = document.getElementById('inline-composer');
  if (!form) return;

  var TITLE_MAX = 280;
  var el = function (id) { return document.getElementById(id); };
  var title = el('ic-title');
  var more = el('ic-more');
  var bar = el('ic-bar');
  var options = el('ic-options');
  var optionsToggle = el('ic-options-toggle');
  var count = el('ic-count');
  var post = el('ic-post');
  var errorBox = el('ic-error');
  var forwardInput = el('ic-forward-input');
  var forwardChips = el('ic-forward-chips');
  var forwardChipList = el('ic-forward-chip-list');
  var forwardFields = el('ic-forward-inputs');
  var presetGuild = form.getAttribute('data-guild');      // the guild whose page this is
  var forwards = presetGuild ? [presetGuild] : [];        // the guilds the post is forwarded to
  var FORWARD_MAX = 20;                                   // helpers/post_drafts.py FORWARD_GUILDS_MAX
  var file = el('ic-file');
  var posted = el('ic-posted');
  var busy = false;

  function autosize() {
    title.style.height = 'auto';
    title.style.height = title.scrollHeight + 'px';
  }

  function open() {
    form.classList.add('is-open');
    more.hidden = false;
    bar.hidden = false;
  }

  function update() {
    var length = title.value.length;
    var left = TITLE_MAX - length;
    count.textContent = length ? String(left) : '';
    count.classList.toggle('text-danger', left < 0);
    post.disabled = busy || !title.value.trim() || left < 0;
  }

  function fail(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  title.addEventListener('focus', open);
  title.addEventListener('input', function () { autosize(); update(); });
  title.addEventListener('keydown', function (event) {
    // the title is one line: Enter does not add a break; Ctrl/Cmd+Enter posts
    if (event.key !== 'Enter') return;
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) form.requestSubmit ? form.requestSubmit() : submit();
  });

  optionsToggle.addEventListener('click', function () {
    var show = options.hidden;
    options.hidden = !show;
    optionsToggle.setAttribute('aria-expanded', String(show));
  });

  // --- Forward to guilds: the same chips and fields as on Create a post --------------
  function hasGuild(name) {
    return forwards.some(function (x) { return x.toLowerCase() === name.toLowerCase(); });
  }

  function renderForwards() {
    forwardChipList.textContent = '';
    forwardFields.textContent = '';
    forwards.forEach(function (name, i) {
      var chip = document.createElement('span');
      chip.className = 'badge badge-pill badge-primary ic-chip mr-1';
      chip.textContent = '+' + name;
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'ic-chip-remove';
      remove.setAttribute('aria-label', 'Do not forward to +' + name);
      remove.innerHTML = '&times;';
      remove.addEventListener('click', function () {
        forwards.splice(i, 1);
        renderForwards();
      });
      chip.appendChild(remove);
      forwardChipList.appendChild(chip);

      var field = document.createElement('input');
      field.type = 'hidden';
      field.name = 'forward_guilds';
      field.value = name;
      forwardFields.appendChild(field);
    });
    forwardChips.hidden = !forwards.length;
  }

  function addForward() {
    var name = forwardInput.value.trim().replace(/^\+/, '');
    forwardInput.value = '';
    if (!name || hasGuild(name)) return;
    if (forwards.length >= FORWARD_MAX) { fail('Forward to at most ' + FORWARD_MAX + ' guilds.'); return; }
    errorBox.hidden = true;
    forwards.push(name);
    renderForwards();
  }

  el('ic-forward-add').addEventListener('click', addForward);
  forwardInput.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();             // Enter adds the guild; it does not post
    addForward();
  });

  // with linked storage, assets/js/media_upload.js uploads the picture and says so itself
  if (file && !file.hasAttribute('data-media-main')) {
    file.addEventListener('change', function () {
      el('ic-file-name').textContent = file.files.length ? file.files[0].name : '';
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    submit();
  });

  function reset() {
    form.reset();
    var keepOpen = form.getAttribute('data-open') === 'true';
    forwards = presetGuild ? [presetGuild] : [];
    renderForwards();
    if (file) el('ic-file-name').textContent = '';
    // a hidden field keeps a value set by script through form.reset(): clear the picture by hand
    var media = el('ic-media');
    if (media) media.value = '';
    var mediaNote = form.querySelector('.media-upload-status');
    if (mediaNote) mediaNote.textContent = '';
    options.hidden = true;
    optionsToggle.setAttribute('aria-expanded', 'false');
    if (!keepOpen) {
      form.classList.remove('is-open');
      more.hidden = true;
      bar.hidden = true;
    }
    autosize();
    update();
  }

  // The new post's card, ready to add to the feed. On a guild page it is the
  // copy forwarded to that guild, which is what the guild's feed lists (unless
  // the guild was taken off the list, then it is the post on your profile).
  function addCard(id) {
    if (!document.querySelector('.posts')) return Promise.resolve();   // no feed here (the side panel)
    var url = '/inpage/post_card/' + encodeURIComponent(id);
    if (presetGuild && hasGuild(presetGuild)) url += '?guild=' + encodeURIComponent(presetGuild);
    return fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
      .then(function (html) {
        var container = document.getElementById('posts') || document.querySelector('.posts');
        if (!container) return;
        var added = document.createElement('div');
        added.innerHTML = html;
        // the card's own modals repeat ids the page already has: keep only the card
        Array.prototype.slice.call(added.children).forEach(function (child) {
          if (!child.id || document.getElementById(child.id)) added.removeChild(child);
        });
        // an empty feed shows an "nothing here" card: the new post replaces it
        if (!container.querySelector('[id^="post-"]')) container.textContent = '';
        if (typeof bindPostCards === 'function') bindPostCards(added);
        var first = container.firstChild;
        while (added.firstChild) container.insertBefore(added.firstChild, first);
      });
  }

  function submit() {
    if (busy || post.disabled) return;
    busy = true;
    errorBox.hidden = true;
    posted.hidden = true;
    post.textContent = 'Posting…';
    update();

    var data = new FormData(form);
    var sent = fetch('/api/vue/submit', { method: 'POST', body: data, credentials: 'same-origin' });

    sent.then(function (response) {
      // Some outcomes are a redirect rather than JSON: a duplicate post (to the
      // existing one), a ban notice, accepting the terms. Show the person that page.
      if (response.redirected) { window.location.href = response.url; return null; }
      return response.json().then(function (body) { return { ok: response.ok, body: body }; });
    }).then(function (result) {
      if (!result) return;
      if (!result.ok || !result.body || !result.body.id) {
        fail((result.body && result.body.error) || 'Could not post. Try again, or use the full editor.');
        return;
      }
      return addCard(result.body.id).then(function () {
        reset();
        // no feed on this page (the side panel): point at the new post instead
        if (!document.querySelector('.posts')) {
          el('ic-posted-link').href = result.body.permalink;
          posted.hidden = false;
        }
      }, function () {
        // posted, but the card could not be fetched: the feed just needs a reload to show it
        reset();
        fail('Posted. Reload the page to see it.');
      });
    }).catch(function () {
      fail('Could not post. Check your connection and try again.');
    }).then(function () {
      busy = false;
      post.textContent = 'Post';
      update();
    });
  }

  if (form.getAttribute('data-open') === 'true') open();
  renderForwards();
  autosize();
  update();
})();
