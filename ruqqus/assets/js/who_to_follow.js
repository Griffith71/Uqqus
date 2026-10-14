/*
 * Who to follow (templates/partials/suggestion_row.html, who_to_follow_box.html).
 *
 * - The right sidebar has an empty #who-to-follow placeholder (#similar-accounts on a
 *   profile). It is filled after the page loads from its data-src, so no other page
 *   waits for the queries.
 * - The Follow / Join buttons post to their data-url (the same follow and join
 *   routes the profile and guild pages use) and turn into a done state.
 */
(function () {
  'use strict';

  // like trending.js: nothing is fetched where the sidebar is not shown (phones, the side-panel frames).
  // A profile has "Similar accounts" (#similar-accounts) in Who to follow's place; the rows and buttons are the same.
  ['who-to-follow', 'similar-accounts'].forEach(function (id) {
    var slot = document.getElementById(id);
    if (!slot || !slot.dataset.src || document.body.classList.contains('embedded') || slot.offsetParent === null) return;
    fetch(slot.dataset.src, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (html) { fill_late_box(slot, html); })
      .catch(function () { fill_late_box(slot, ''); /* the box is a nicety */ });
  });

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
