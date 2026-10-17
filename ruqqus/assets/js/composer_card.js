/*
 * The Create post card (templates/submit.html, `.composer-card`) and every "Who can see this?" field
 * (partials/post_options.html `audience_field`: the composers, the "Who can see this" sheet, the story sheet).
 *
 * Three small jobs, nothing that sends a form:
 *   - an audience field keeps its radio (`name="audience"`: 0 Public, 4 Circles) and its ticks (`name="circles"`) in
 *     step: ticking a circle chooses Circles, choosing Circles with nothing ticked ticks Friends. `window.AudienceField`
 *     reads and sets a field for the scripts that send one themselves (circle_audience.js, stories.js);
 *   - the audience pill shows what is chosen ("Public", "Friends", "Friends +1", "3 circles") and closes after Public,
 *     on Escape and on a click elsewhere (ticking circles keeps it open);
 *   - the poll button in the attach row opens "More options" and starts the poll panel
 *     (assets/js/poll_composer.js owns the panel and its fields). The feed composer
 *     (partials/inline_composer.html) uses the same ids.
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
    labelText.textContent = label(field);
    if (icon) icon.className = 'fas ' + (read(field).audience === '4' ? 'fa-user-friends' : 'fa-globe-americas');
  }

  if (pill) {
    pill.addEventListener('change', function (event) {
      var input = event.target;
      if (!input || (input.name !== 'audience' && input.name !== 'circles')) return;
      showAudience();
      if (input.name === 'audience' && input.value === '0') window.setTimeout(function () { pill.open = false; }, 120);
    });
    document.addEventListener('click', function (event) {
      if (pill.open && !pill.contains(event.target)) pill.open = false;
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && pill.open) {
        pill.open = false;
        var summary = pill.querySelector('summary');
        if (summary) summary.focus();
      }
    });
    showAudience();
  }

  var pollButton = document.getElementById('composer-poll-btn');
  if (pollButton) {
    pollButton.addEventListener('click', function () {
      var more = document.querySelector('.composer-more');
      if (more) more.open = true;
      // the feed composer folds its options behind a button instead
      var folded = document.getElementById('ic-options');
      var foldedToggle = document.getElementById('ic-options-toggle');
      if (folded && folded.hidden && foldedToggle) foldedToggle.click();
      var open = document.querySelector('.poll-composer .poll-open');
      var first = document.querySelector('.poll-composer input[name="poll_option"]');
      if (open && !open.hidden) open.click();
      var target = document.querySelector('.poll-composer');
      if (target && target.scrollIntoView) target.scrollIntoView({ block: 'center' });
      first = document.querySelector('.poll-composer input[name="poll_option"]');
      if (first && !first.disabled) first.focus();
    });
  }
})();
