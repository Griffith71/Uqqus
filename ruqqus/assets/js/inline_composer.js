/*
 * The composer at the top of a feed (templates/partials/inline_composer.html).
 *
 * It posts to /api/vue/submit, the route the Create a post page uses, so every
 * posting rule, throttle and ban applies the same way. A post is made on your
 * own profile and forwarded to the guilds in "Forward to guilds" (one hidden
 * `forward_guilds` field each; on a guild page that guild is there from the start).
 * On success the new post's card is fetched (/inpage/post_card/<id>) and added to
 * the top of the feed.
 *
 * What the full editor has is here too, in a compact form: the audience pill and the poll
 * button (assets/js/composer_card.js), the formatting toolbar and Write / Preview
 * (assets/js/post_editor.js, on the `.post-toolbar` in the markup), GIF and emoji (the
 * site's own modals; the buttons say which box they write into), and Save draft /
 * Schedule / Drafts (assets/js/post_drafts.js reads this form by field name). A picture
 * for the text is the `body_image` field the post route already reads.
 */
(function () {
  'use strict';

  var form = document.getElementById('inline-composer');
  if (!form) return;

  var TITLE_MAX = 280;
  var el = function (id) { return document.getElementById(id); };
  var title = el('ic-title');
  var more = el('ic-more');
  var head = el('ic-head');
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
  var circleGuild = form.getAttribute('data-circle-guild'); // a Circle guild: the post is made straight in it, not forwarded
  var presetGuild = circleGuild ? null : form.getAttribute('data-guild'); // the guild whose page this is (to forward to)
  var forwards = presetGuild ? [presetGuild] : [];        // the guilds the post is forwarded to
  var FORWARD_MAX = 20;                                   // helpers/post_drafts.py FORWARD_GUILDS_MAX
  var file = el('ic-file');
  var bodyImage = el('ic-body-image');
  var linkButton = el('ic-link-btn');
  var posted = el('ic-posted');
  var busy = false;

  function autosize() {
    title.style.height = 'auto';
    title.style.height = title.scrollHeight + 'px';
  }

  function open() {
    form.classList.add('is-open');
    head.hidden = false;
    more.hidden = false;
    bar.hidden = false;
  }

  function showOptions(show) {
    options.hidden = !show;
    optionsToggle.setAttribute('aria-expanded', String(show));
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
    showOptions(options.hidden);
  });

  // the link tool: the Link field lives in More options
  linkButton.addEventListener('click', function () {
    showOptions(true);
    el('ic-url').focus();
  });

  // a link pasted into the title on its own goes in the Link field instead (as on X, where a pasted post link becomes
  // the quoted post), so the title keeps only the member's own words; pastedLink is in all_js.js
  title.addEventListener('paste', function (event) {
    var link = typeof window.pastedLink === 'function' ? window.pastedLink(event) : null;
    if (!link || el('ic-url').value) return;
    event.preventDefault();
    el('ic-url').value = link;
    showOptions(true);
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

  // a picture for the text: sent as `body_image`, the field the post route already reads
  if (bodyImage) {
    bodyImage.addEventListener('change', function () {
      el('ic-file-name').textContent = bodyImage.files.length ? bodyImage.files[0].name : '';
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
    el('ic-file-name').textContent = '';
    // a hidden field keeps the value script gave it through form.reset(): the draft just posted is used up
    var draftId = el('draft-id');
    if (draftId) draftId.value = '';
    // a draft's banner and status belong to what was just posted
    var banner = el('draft-banner');
    if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    var draftStatus = el('draft-status');
    if (draftStatus) draftStatus.textContent = '';
    // a hidden field keeps a value set by script through form.reset(): clear the picture by hand
    var media = el('ic-media');
    if (media) media.value = '';
    var mediaNote = form.querySelector('.media-upload-status');
    if (mediaNote) mediaNote.textContent = '';
    showOptions(false);
    // the audience pill shows the field again (form.reset() changes the radios without a change event)
    var audience = form.querySelector('input[name="audience"]:checked');
    if (audience) audience.dispatchEvent(new Event('change', { bubbles: true }));
    // back to Write if the text was being previewed
    var write = form.querySelector('.post-toolbar .post-tb-tab');
    if (write && !write.classList.contains('active')) write.click();
    if (!keepOpen) {
      form.classList.remove('is-open');
      head.hidden = true;
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
    if (circleGuild) {
      // made in the guild itself: nothing that would take it elsewhere or change who sees it travels with it
      ['audience', 'circles', 'anonymous', 'coauthors', 'forward_guilds'].forEach(function (name) { data.delete(name); });
      data.set('circle_guild', circleGuild);
    }
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
