/*
 * The Create post card (templates/submit.html, `.composer-card`).
 *
 * Two small jobs, nothing that touches the form's fields:
 *   - the audience pill ("Public" / "Subscribers" / "Close Friends") shows the checked radio of
 *     `name="audience"` and closes itself after a choice, on Escape and on a click elsewhere;
 *   - the poll button in the attach row opens "More options" and starts the poll panel
 *     (assets/js/poll_composer.js owns the panel and its fields). The feed composer and the
 *     Create post panel (partials/inline_composer.html) use the same ids.
 * The radios, the poll fields and every other control stay in the form and are sent as before.
 */
(function () {
  'use strict';

  var pill = document.getElementById('composer-audience');
  var label = document.getElementById('composer-audience-label');
  var icon = document.getElementById('composer-audience-icon');

  var NAMES = { '0': 'Public', '1': 'Subscribers', '2': 'Close Friends' };

  function showAudience(value) {
    if (!label || !NAMES[value]) return;
    label.textContent = NAMES[value];
    if (icon) icon.className = 'fas ' + (value === '0' ? 'fa-globe-americas' : 'fa-user-friends');
  }

  if (pill) {
    pill.addEventListener('change', function (event) {
      var input = event.target;
      if (!input || input.name !== 'audience') return;
      showAudience(input.value);
      window.setTimeout(function () { pill.open = false; }, 120);
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
    var checked = pill.querySelector('input[name="audience"]:checked');
    if (checked) showAudience(checked.value);
  }

  var pollButton = document.getElementById('composer-poll-btn');
  if (pollButton) {
    pollButton.addEventListener('click', function () {
      var more = document.querySelector('.composer-more');
      if (more) more.open = true;
      // the feed composer and the Create post panel fold their options behind a button instead
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
