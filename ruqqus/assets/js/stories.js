/*
 * Stories on a profile (partials/story_bits.html, partials/story_sheets.html, routes/stories.py).
 * - the ring round an avatar and a Highlight open the full-screen viewer;
 * - the "+" on one's own avatar opens the sheet that makes a story;
 * - "New" under the bio opens the sheet that makes a Highlight from one's own stories.
 * Everything a member wrote reaches the page through textContent or a validated attribute, never as HTML.
 * The server decides who may watch what; this script only draws what it was sent and tells it what was seen.
 */
(function () {
  'use strict';

  var viewer = document.getElementById('story-viewer');
  if (!viewer) return;

  var CARD_MS = 5000;
  var HOLD_MS = 250;
  var BACKGROUND = /^[a-z]{3,12}$/;
  var YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;

  var els = {
    bars: document.getElementById('story-bars'),
    user: document.getElementById('story-user'),
    avatar: document.getElementById('story-avatar'),
    name: document.getElementById('story-name'),
    meta: document.getElementById('story-meta'),
    close: document.getElementById('story-close'),
    card: document.getElementById('story-card'),
    prev: document.getElementById('story-prev'),
    next: document.getElementById('story-next'),
    views: document.getElementById('story-views'),
    actions: document.getElementById('story-actions'),
    stage: viewer.querySelector('.story-stage')
  };

  var state = null;          // { data, kind: 'user' | 'highlight', index, seen: {id: true} }
  var raf = 0;
  var cardStart = 0;         // when the running card began, shifted forward by every pause
  var pausedAt = 0;
  var holdTimer = 0;
  var held = false;
  var lastFocus = null;
  var note = null;

  // ------------------------------------------------------------------ requests

  function formkeyValue() { return typeof formkey === 'function' ? formkey() : ''; }

  function getJSON(path) {
    return fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; }, function () { return { ok: false, status: r.status, data: {} }; }); });
  }

  function send(path, fields) {
    var body = new URLSearchParams();
    body.set('formkey', formkeyValue());
    Object.keys(fields || {}).forEach(function (key) {
      var value = fields[key];
      if (Array.isArray(value)) value.forEach(function (one) { body.append(key, one); });
      else body.set(key, value);
    });
    return fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', Accept: 'application/json' },
      body: body
    }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, status: r.status, data: data }; }, function () { return { ok: r.ok, status: r.status, data: {} }; }); });
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------------ the viewer

  function say(text) {
    if (!note) { note = element('div', 'story-note'); note.setAttribute('role', 'status'); els.stage.appendChild(note); }
    note.textContent = text || '';
    note.hidden = !text;
  }

  function current() { return state ? state.data.stories[state.index] : null; }

  function buildBars() {
    els.bars.textContent = '';
    state.data.stories.forEach(function () {
      var bar = element('span', 'story-bar');
      bar.appendChild(element('span', 'story-bar-fill'));
      els.bars.appendChild(bar);
    });
  }

  function paintBars(fraction) {
    var bars = els.bars.children;
    for (var i = 0; i < bars.length; i++) {
      var width = i < state.index ? 1 : (i === state.index ? fraction : 0);
      bars[i].firstChild.style.transform = 'scaleX(' + width + ')';
    }
  }

  function drawCard(story) {
    els.card.textContent = '';
    els.card.className = 'story-card';
    els.stage.classList.toggle('story-stage-video', story.kind === 'video');   // leaves the player its controls
    if (story.kind === 'text') {
      var background = BACKGROUND.test(story.background || '') ? story.background : 'ocean';
      els.card.classList.add('story-card-text', 'story-bg-' + background);
      els.card.appendChild(element('p', 'story-text', story.text));
    } else if (story.kind === 'image') {
      if (story.picture && story.picture.indexOf('/media/') === 0) {
        var picture = element('img', 'story-picture');
        picture.alt = '';
        picture.src = story.picture;
        els.card.appendChild(picture);
      } else {
        els.card.appendChild(element('p', 'story-text', 'This picture is no longer available.'));
      }
      if (story.text) els.card.appendChild(element('p', 'story-caption', story.text));
    } else if (story.kind === 'video' && YOUTUBE_ID.test(story.video || '')) {
      var frame = element('iframe', 'story-frame');
      frame.src = 'https://www.youtube.com/embed/' + story.video + '?autoplay=1&rel=0&playsinline=1';
      frame.title = 'Video';
      frame.allow = 'autoplay; encrypted-media; picture-in-picture';
      frame.setAttribute('allowfullscreen', '');
      els.card.appendChild(frame);
    }
  }

  function drawFooter(story) {
    els.actions.textContent = '';
    els.views.textContent = '';
    if (state.data.mine) {
      if (typeof story.views === 'number') els.views.textContent = story.views + (story.views === 1 ? ' view' : ' views');
      els.actions.appendChild(button('Delete', 'story-action', function () { removeStory(story); }));
      if (state.kind === 'highlight') {
        els.actions.appendChild(button('Rename', 'story-action', renameHighlight));
        els.actions.appendChild(button('Delete highlight', 'story-action', deleteHighlight));
      }
    } else {
      els.actions.appendChild(button('Report', 'story-action', function () { reportStory(story); }));
    }
  }

  function button(label, className, onClick) {
    var node = element('button', className, label);
    node.type = 'button';
    node.addEventListener('click', function (event) { event.stopPropagation(); onClick(); });
    return node;
  }

  function show(index) {
    var stories = state.data.stories;
    if (index < 0) index = 0;
    if (index >= stories.length) { closeViewer(); return; }
    state.index = index;
    var story = stories[index];
    say('');
    drawCard(story);
    drawFooter(story);
    var meta = story.audience && story.audience !== 'Public' ? story.audience + ' · ' : '';
    els.meta.textContent = meta + (story.left || '');
    paintBars(0);
    markSeen(story);
    cancelAnimationFrame(raf);
    cardStart = performance.now();
    pausedAt = 0;
    if (story.kind !== 'video') raf = requestAnimationFrame(tick);
    else paintBars(1);
  }

  function tick(now) {
    if (!state) return;
    if (pausedAt) { raf = requestAnimationFrame(tick); return; }
    var fraction = Math.min(1, (now - cardStart) / CARD_MS);
    paintBars(fraction);
    if (fraction >= 1) { show(state.index + 1); return; }
    raf = requestAnimationFrame(tick);
  }

  function pause() { if (!pausedAt) pausedAt = performance.now(); }

  function resume() {
    if (!pausedAt) return;
    cardStart += performance.now() - pausedAt;
    pausedAt = 0;
  }

  function markSeen(story) {
    if (state.data.mine || state.seen[story.id] || !story.live) return;
    state.seen[story.id] = true;
    send('/api/stories/' + story.id + '/view');
  }

  function openViewer(data, kind) {
    if (!data.stories || !data.stories.length) return false;
    var start = 0;
    if (kind === 'user') {
      for (var i = 0; i < data.stories.length; i++) { if (!data.stories[i].seen) { start = i; break; } }
    }
    state = { data: data, kind: kind, index: start, seen: {} };
    data.stories.forEach(function (story) { if (story.seen) state.seen[story.id] = true; });
    lastFocus = document.activeElement;
    els.avatar.src = data.user.avatar;
    els.name.textContent = '@' + data.user.username;
    els.user.href = data.user.permalink;
    if (kind === 'highlight') els.name.textContent = data.title + ' · @' + data.user.username;
    buildBars();
    viewer.hidden = false;
    document.body.classList.add('story-open');
    show(start);
    els.close.focus();
    return true;
  }

  function closeViewer() {
    var closing = state;
    cancelAnimationFrame(raf);
    state = null;
    viewer.hidden = true;
    els.card.textContent = '';
    document.body.classList.remove('story-open');
    if (closing && closing.kind === 'user' && !closing.data.mine) recolour(closing);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  // After watching, the ring goes grey once every live story has been seen.
  function recolour(closing) {
    var all = closing.data.stories.every(function (s) { return s.seen || closing.seen[s.id]; });
    if (!all) return;
    var rings = document.querySelectorAll('.story-ring[data-story-owner="' + closing.data.user.username.replace(/"/g, '') + '"]');
    Array.prototype.forEach.call(rings, function (ring) {
      ring.classList.remove('story-ring-unseen');
      ring.classList.add('story-ring-seen');
    });
  }

  function step(delta) {
    if (!state) return;
    if (state.index + delta < 0) { show(0); return; }
    show(state.index + delta);
  }

  // ------------------------------------------------------------------ the viewer's actions

  function removeStory(story) {
    if (!window.confirm('Delete this story? It leaves every Highlight too.')) return;
    send('/api/stories/' + story.id + '/delete').then(function (res) {
      if (!res.ok) { say(res.data.error || 'Could not delete that.'); return; }
      var stories = state.data.stories;
      stories.splice(state.index, 1);
      if (!stories.length) { closeViewer(); window.location.reload(); return; }
      buildBars();
      show(Math.min(state.index, stories.length - 1));
    }).catch(function () { say('Could not delete that. Try again.'); });
  }

  function reportStory(story) {
    var reason = window.prompt('Why are you reporting this story? (optional)', '');
    if (reason === null) return;
    pause();
    send('/api/stories/' + story.id + '/report', { reason: reason }).then(function (res) {
      say(res.ok ? (res.data.message || 'Thanks.') : (res.data.error || 'Could not report that.'));
    }).catch(function () { say('Could not report that. Try again.'); });
  }

  function renameHighlight() {
    var title = window.prompt('Name this highlight', state.data.title);
    if (title === null) return;
    send('/api/highlights/' + state.data.id + '/rename', { title: title }).then(function (res) {
      if (!res.ok) { say(res.data.error || 'Could not rename that.'); return; }
      window.location.reload();
    }).catch(function () { say('Could not rename that. Try again.'); });
  }

  function deleteHighlight() {
    if (!window.confirm('Delete this highlight? Its stories stay in your archive.')) return;
    send('/api/highlights/' + state.data.id + '/delete').then(function (res) {
      if (!res.ok) { say(res.data.error || 'Could not delete that.'); return; }
      window.location.reload();
    }).catch(function () { say('Could not delete that. Try again.'); });
  }

  // ------------------------------------------------------------------ opening one

  function openUser(username) {
    getJSON('/api/stories/' + encodeURIComponent(username)).then(function (res) {
      if (!res.ok || !openViewer(res.data, 'user')) window.alert('There is nothing to watch right now.');
    }).catch(function () { window.alert('Could not load that. Try again.'); });
  }

  function openHighlight(id) {
    getJSON('/api/highlights/' + encodeURIComponent(id)).then(function (res) {
      if (!res.ok || !openViewer(res.data, 'highlight')) window.alert('There is nothing to watch right now.');
    }).catch(function () { window.alert('Could not load that. Try again.'); });
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target.closest) return;
    var add = target.closest('[data-story-add]');
    if (add) { event.preventDefault(); event.stopPropagation(); openComposer(); return; }
    var ring = target.closest('[data-story-user]');
    if (ring) { event.preventDefault(); openUser(ring.getAttribute('data-story-user')); return; }
    var highlight = target.closest('[data-highlight]');
    if (highlight) { event.preventDefault(); openHighlight(highlight.getAttribute('data-highlight')); return; }
    var fresh = target.closest('[data-highlight-new]');
    if (fresh) { event.preventDefault(); openHighlightSheet(); }
  }, true);

  document.addEventListener('keydown', function (event) {
    if (!viewer.hidden) {
      if (event.key === 'Tab') { keepFocusInside(event); return; }
      if (event.key === 'Escape') { event.preventDefault(); closeViewer(); }
      else if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
      else if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
      return;
    }
    var ring = event.target.closest ? event.target.closest('[data-story-user]') : null;
    if (ring && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openUser(ring.getAttribute('data-story-user')); }
  });

  // Tab goes round the viewer's own controls, never out to the page behind it.
  function keepFocusInside(event) {
    var controls = Array.prototype.filter.call(viewer.querySelectorAll('button, a[href]'), function (node) { return !node.disabled && node.offsetParent !== null; });
    if (!controls.length) return;
    var first = controls[0];
    var last = controls[controls.length - 1];
    var inside = viewer.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || !inside)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !inside)) { event.preventDefault(); first.focus(); }
  }

  els.close.addEventListener('click', closeViewer);
  viewer.addEventListener('click', function (event) { if (event.target === viewer) closeViewer(); });

  // Tap the left or right side to go back or forward; hold anywhere on the card to pause.
  [els.prev, els.next].forEach(function (zone) {
    var delta = zone === els.next ? 1 : -1;
    zone.addEventListener('pointerdown', function () {
      held = false;
      clearTimeout(holdTimer);
      holdTimer = setTimeout(function () { held = true; pause(); }, HOLD_MS);
    });
    function release() {
      clearTimeout(holdTimer);
      if (!held) return;
      resume();
      setTimeout(function () { held = false; }, 60);    // the click that ends a hold is not a tap
    }
    zone.addEventListener('pointerup', release);
    zone.addEventListener('pointercancel', release);
    zone.addEventListener('pointerleave', release);
    zone.addEventListener('click', function (event) {
      event.stopPropagation();
      if (held) return;
      step(delta);
    });
  });

  // ------------------------------------------------------------------ the sheet that makes a story

  var composer = document.getElementById('storyComposer');
  var form = document.getElementById('story-form');

  function field(id) { return document.getElementById(id); }
  function checked(name) { var picked = form.querySelector('input[name="' + name + '"]:checked'); return picked ? picked.value : ''; }
  function sayComposer(text, isError) {
    var status = field('story-status');
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  function showPane() {
    var kind = checked('kind');
    ['text', 'image', 'video'].forEach(function (name) { field('story-pane-' + name).hidden = name !== kind; });
    // a video is only ever public
    var restricted = kind === 'video';
    var publicChoice = field('story-audience-0');
    if (restricted) publicChoice.checked = true;
    ['story-audience-1', 'story-audience-2'].forEach(function (id) { field(id).disabled = restricted; });
    sayComposer('');
    if (kind === 'image' && window.MediaUpload) {
      window.MediaUpload.status().then(function (s) {
        if (!s.offered) sayComposer('Picture stories are not available on this site yet.', true);
      });
    }
  }

  function resetComposer() {
    ['story-text', 'story-caption', 'story-video'].forEach(function (id) { field(id).value = ''; });
    field('story-media').value = '';
    field('story-file').value = '';
    var left = field('story-picture-box').querySelector('.media-upload-status');
    if (left) left.textContent = '';
    field('story-count').textContent = '0';
    var text = form.querySelector('input[name="kind"][value="text"]');
    if (text) text.checked = true;
    Array.prototype.forEach.call(form.querySelectorAll('#story-kinds .btn'), function (label) {
      label.classList.toggle('active', !!label.querySelector('input:checked'));
    });
    field('story-audience-0').checked = true;
    showPane();
  }

  function openComposer() {
    resetComposer();
    if (window.jQuery) window.jQuery('#storyComposer').modal('show');
  }

  if (composer && form) {
    form.addEventListener('change', function (event) {
      if (event.target.name === 'kind') showPane();
    });
    field('story-text').addEventListener('input', function () { field('story-count').textContent = String(this.value.length); });

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var kind = checked('kind');
      var fields = { kind: kind, audience: checked('audience') || '0' };
      if (kind === 'text') { fields.text = field('story-text').value; fields.background = checked('background'); }
      if (kind === 'image') {
        fields.text = field('story-caption').value;
        fields.media = field('story-media').value;
        if (!fields.media) { sayComposer('Choose a picture and wait for it to finish uploading.', true); return; }
      }
      if (kind === 'video') fields.video = field('story-video').value;
      var share = field('story-share');
      share.disabled = true;
      sayComposer('Sharing…');
      send('/api/stories', fields).then(function (res) {
        share.disabled = false;
        if (!res.ok) { sayComposer(res.data.error || 'Could not share that.', true); return; }
        window.location.reload();
      }).catch(function () { share.disabled = false; sayComposer('Could not share that. Try again.', true); });
    });
  }

  // ------------------------------------------------------------------ the sheet that makes a Highlight

  var highlightModal = document.getElementById('highlightModal');
  var highlightForm = document.getElementById('highlight-form');

  function sayHighlight(text, isError) {
    var status = field('highlight-status');
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  function openHighlightSheet() {
    if (!highlightModal) return;
    field('highlight-name').value = '';
    sayHighlight('');
    var list = field('highlight-list');
    list.textContent = '';
    list.appendChild(element('div', 'text-small text-muted', 'Loading your stories…'));
    if (window.jQuery) window.jQuery('#highlightModal').modal('show');
    getJSON('/api/stories/archive').then(function (res) {
      list.textContent = '';
      var stories = res.ok ? res.data.stories : [];
      if (!stories.length) { list.appendChild(element('div', 'text-small text-muted', 'You have no stories yet. Share one first.')); return; }
      stories.forEach(function (story) {
        var row = element('label', 'highlight-pick');
        var box = element('input');
        box.type = 'checkbox';
        box.name = 'story';
        box.value = String(story.id);
        row.appendChild(box);
        var label = story.kind === 'text' ? story.text : (story.kind === 'image' ? (story.text || 'Picture') : 'Video');
        if (label.length > 60) label = label.slice(0, 57) + '…';
        row.appendChild(element('span', 'highlight-pick-text', label));
        row.appendChild(element('span', 'highlight-pick-date', new Date(story.created * 1000).toLocaleDateString()));
        list.appendChild(row);
      });
    }).catch(function () {
      list.textContent = '';
      list.appendChild(element('div', 'text-small text-danger', 'Could not load your stories.'));
    });
  }

  if (highlightForm) {
    highlightForm.addEventListener('submit', function (event) {
      event.preventDefault();
      var ids = Array.prototype.map.call(highlightForm.querySelectorAll('input[name="story"]:checked'), function (box) { return box.value; });
      var save = field('highlight-save');
      save.disabled = true;
      send('/api/highlights', { title: field('highlight-name').value, story: ids }).then(function (res) {
        save.disabled = false;
        if (!res.ok) { sayHighlight(res.data.error || 'Could not save that.', true); return; }
        window.location.reload();
      }).catch(function () { save.disabled = false; sayHighlight('Could not save that. Try again.', true); });
    });
  }
})();
