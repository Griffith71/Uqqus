/*
 * Who to follow (templates/partials/suggestion_row.html, who_to_follow_box.html).
 *
 * - The right sidebar has an empty #who-to-follow placeholder. It is filled after
 *   the page loads from its data-src, so no other page waits for the queries.
 * - The Follow / Join buttons post to their data-url (the same follow and join
 *   routes the profile and guild pages use) and turn into a done state.
 */
(function () {
  'use strict';

  var slot = document.getElementById('who-to-follow');
  if (slot && slot.dataset.src) {
    fetch(slot.dataset.src, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (html) { slot.innerHTML = html; })
      .catch(function () { /* the box is a nicety */ });
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('.wtf-action') : null;
    if (!button || button.disabled) return;
    button.disabled = true;
    var done = button.getAttribute('data-done') || 'Done';
    var label = button.textContent;
    button.textContent = '…';
    post(button.getAttribute('data-url'), function () {
      button.textContent = done;
      button.classList.remove('btn-primary');
      button.classList.add('btn-secondary');
    }, 'Could not do that. Try again.');
    // post() alerts on a network error; give the button back if nothing changed
    setTimeout(function () {
      if (button.textContent === '…') { button.disabled = false; button.textContent = label; }
    }, 8000);
  });
})();
