/*
 * The Create post card (templates/submit.html, `.composer-card`) and every "Who can see this?" field
 * (partials/post_options.html `audience_field`: the composers, the "Who can see this" sheet, the story sheet).
 *
 * Small jobs, nothing that sends a form:
 *   - an audience field keeps its radio (`name="audience"`: 0 Public, 4 Circles) and its ticks (`name="circles"`) in
 *     step: ticking a circle chooses Circles, choosing Circles with nothing ticked ticks Friends. `window.AudienceField`
 *     reads and sets a field for the scripts that send one themselves (circle_audience.js, stories.js);
 *   - the audience pill shows what is chosen ("Public", "Friends", "Friends +1", "3 circles", or "Anonymous") and closes
 *     after Public, on Escape and on a click elsewhere (ticking circles keeps it open). "Post anonymously" sits in its
 *     menu and is switched off while Circles is chosen (a post for circles can't be anonymous: circles.post_refusal);
 *   - every composer pill (the audience, and "Where to post": composer-where) closes on Escape and a click elsewhere;
 *   - the poll button in the blue row starts the poll panel right under it (assets/js/poll_composer.js owns the panel
 *     and its fields);
 *   - the blue row's video and sound buttons (`data-post-media`) appear once the uploader for them is there
 *     (media_upload.js registers it with post_editor.js) and use it for the post; the body toolbar's Image uses the
 *     old `body_image` upload when this site has no linked storage.
 * Both composers (templates/submit.html, partials/inline_composer.html) use the same ids.
 */
(function () {
  'use strict';

  function boxes(field) { return field.querySelectorAll('input[name="circles"]'); }

  function read(field) {
    var picked = field.querySelector('input[name="audience"]:checked');
    var audience = picked ? picked.value : '0';
    var circles = [];
    if (audience === '4') {
      Array.prototype.forEach.call(boxes(field), function (box) { if (box.checked) circles.push(box.value); });
    }
    return { audience: audience, circles: circles };
  }

  function label(field) {
    var chosen = read(field);
    if (chosen.audience !== '4') return 'Public';
    if (!chosen.circles.length) return 'Circles';
    var first = field.querySelector('input[name="circles"][value="' + chosen.circles[0] + '"]');
    var name = first ? first.getAttribute('data-circle-name') : 'Circles';
    if (chosen.circles.length === 1) return name;
    if (chosen.circles.length === 2) return name + ' +1';
    return chosen.circles.length + ' circles';
  }

  function sync(field) {
    field.setAttribute('data-mode', read(field).audience === '4' ? '4' : '0');
  }

  function set(field, audience, tokens) {
    var wanted = (tokens || []).map(String);
    var radio = field.querySelector('input[name="audience"][value="' + (audience === '4' ? '4' : '0') + '"]');
    if (radio) radio.checked = true;
    Array.prototype.forEach.call(boxes(field), function (box) { box.checked = wanted.indexOf(box.value) !== -1; });
    sync(field);
  }

  window.AudienceField = { read: read, set: set, label: label };

  document.addEventListener('change', function (event) {
    var input = event.target;
    var field = input && input.closest ? input.closest('[data-audience-field]') : null;
    if (!field) return;
    if (input.name === 'circles' && input.checked) {
      var circlesRadio = field.querySelector('input[name="audience"][value="4"]');
      if (circlesRadio) circlesRadio.checked = true;
    }
    if (input.name === 'audience' && input.value === '4' && !read(field).circles.length) {
      var first = boxes(field)[0];
      if (first) first.checked = true;
    }
    sync(field);
  }, true);     // the capture phase: the field is in step before anything that listens on its way up reads it (the pill)

  var pill = document.getElementById('composer-audience');
  var labelText = document.getElementById('composer-audience-label');
  var icon = document.getElementById('composer-audience-icon');

  function showAudience() {
    var field = pill && pill.querySelector('[data-audience-field]');
    if (!field || !labelText) return;
    var forCircles = read(field).audience === '4';
    var anonymous = pill.querySelector('input[name="anonymous"]');
    // a post for circles can't be anonymous (the server refuses it): the switch is off and can't be turned on
    if (anonymous) {
      if (forCircles) anonymous.checked = false;
      anonymous.disabled = forCircles;
    }
    var hidden = !!(anonymous && anonymous.checked);
    labelText.textContent = hidden ? 'Anonymous' : label(field);
    if (icon) icon.className = 'fas ' + (hidden ? 'fa-user-secret' : (forCircles ? 'fa-user-friends' : 'fa-globe-americas'));
  }

  if (pill) {
    pill.addEventListener('change', function (event) {
      var input = event.target;
      if (!input || (input.name !== 'audience' && input.name !== 'circles' && input.name !== 'anonymous')) return;
      showAudience();
      if (input.name === 'audience' && input.value === '0') window.setTimeout(function () { pill.open = false; }, 120);
    });
    // a form reset (the feed composer after posting) puts the switch back without a change event
    var pillForm = pill.closest('form');
    if (pillForm) pillForm.addEventListener('reset', function () { window.setTimeout(showAudience, 0); });
    showAudience();
  }

  // every composer pill: the audience and "Where to post" close on a click elsewhere and on Escape
  var pills = Array.prototype.slice.call(document.querySelectorAll('details.composer-pill'));
  document.addEventListener('click', function (event) {
    pills.forEach(function (p) { if (p.open && !p.contains(event.target)) p.open = false; });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    pills.forEach(function (p) {
      if (!p.open) return;
      p.open = false;
      var summary = p.querySelector('summary');
      if (summary) summary.focus();
    });
  });

  // the poll sits right under the blue row (no longer under More options): the button opens its panel
  var pollButton = document.getElementById('composer-poll-btn');
  if (pollButton) {
    pollButton.addEventListener('click', function () {
      var scope = pollButton.closest('form') || document;
      var open = scope.querySelector('.poll-composer .poll-open');
      if (open && !open.hidden) open.click();
      var target = scope.querySelector('.poll-composer');
      if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
      var first = scope.querySelector('.poll-composer input[name="poll_option"]');
      if (first && !first.disabled) first.focus();
    });
  }

  // the blue row's video and sound: the uploader media_upload.js registers with the post editor, used for the post (a
  // video goes to the member's own channel and becomes the post's link; a sound goes into the text as its player)
  function showPostMedia(kind) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-post-media="' + kind + '"]'), function (b) { b.hidden = false; });
  }
  document.addEventListener('posteditor:register', function (event) { showPostMedia(event.detail.kind); });
  if (window.PostEditor && window.PostEditor.has) ['video', 'audio'].forEach(function (kind) { if (window.PostEditor.has(kind)) showPostMedia(kind); });
  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-post-media]') : null;
    if (!button || !window.PostEditor || !window.PostEditor.use) return;
    window.PostEditor.use(button.getAttribute('data-post-media'), document.getElementById(button.getAttribute('data-target')));
  });

  // the body toolbar's Image without linked storage: the old picture-in-the-text upload (`body_image`, read by the post
  // route), for members allowed it (the input is only drawn for them)
  function oldBodyImage() {
    if (!window.PostEditor || !document.querySelector('input[type="file"][name="body_image"]')) return;
    window.PostEditor.register('image', function (ctx) {
      var form = ctx.textarea && (ctx.textarea.form || ctx.textarea.closest('form'));
      var input = form ? form.querySelector('input[type="file"][name="body_image"]') : null;
      if (input) input.click();
    });
  }
  if (window.MediaUpload && window.MediaUpload.status) {
    window.MediaUpload.status().then(function (s) { if (!s || !s.offered) oldBodyImage(); }, oldBodyImage);
  } else {
    oldBodyImage();
  }
})();
