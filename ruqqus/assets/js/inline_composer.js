/*
 * The composer at the top of a feed (templates/partials/inline_composer.html).
 *
 * It posts to /api/vue/submit, the route the Create a post page uses, so every
 * posting rule, throttle and ban applies the same way. A post is made on your
 * own profile; on a guild page the hidden `forward_guilds` field also forwards
 * it to that guild. On success the new post's card is fetched
 * (/inpage/post_card/<id>) and added to the top of the feed.
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
  var forward = el('ic-forward');
  var file = el('ic-file');
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

  var removeForward = el('ic-forward-remove');
  if (removeForward) {
    removeForward.addEventListener('click', function () {
      forward.disabled = true;           // a disabled field is not sent: the post stays on your profile
      el('ic-forward-chip').hidden = true;
    });
  }

  if (file) {
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
    if (forward) { forward.disabled = false; el('ic-forward-chip').hidden = false; }
    if (file) el('ic-file-name').textContent = '';
    options.hidden = true;
    optionsToggle.setAttribute('aria-expanded', 'false');
    form.classList.remove('is-open');
    more.hidden = true;
    bar.hidden = true;
    autosize();
    update();
  }

  // The new post's card, ready to add to the feed. On a guild page it is the
  // copy forwarded to that guild, which is what the guild's feed lists.
  function addCard(id) {
    var url = '/inpage/post_card/' + encodeURIComponent(id);
    if (forward && !forward.disabled) url += '?guild=' + encodeURIComponent(forward.value);
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
      return addCard(result.body.id).then(function () { reset(); }, function () {
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

  autosize();
  update();
})();
