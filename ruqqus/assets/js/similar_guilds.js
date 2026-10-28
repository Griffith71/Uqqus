/*
 * Similar guilds (templates/partials/similar_guilds_slot.html, similar_guilds_box.html).
 *
 * - A guild's right sidebar has an empty #similar-guilds placeholder. It is filled after the page loads from its
 *   data-src, so no guild or post page waits for the queries, and a guild page a visitor gets from a cache stays the same
 *   for everyone.
 * - Like who_to_follow.js, nothing is fetched where the sidebar is not shown (phones, the side-panel frames). The Join
 *   buttons are handled there too (a delegated .wtf-action click), for every member; a visitor's is a sign-in link.
 * - This script is its own because who_to_follow.js is not loaded for a visitor on a guild page. It runs on
 *   DOMContentLoaded so fill_late_box (all_js.js) exists whatever order the scripts load in.
 */
(function () {
  'use strict';

  function start() {
    var slot = document.getElementById('similar-guilds');
    if (!slot || !slot.dataset.src || document.body.classList.contains('embedded') || slot.offsetParent === null) return;
    fetch(slot.dataset.src, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (html) { fill_late_box(slot, html); })
      .catch(function () { fill_late_box(slot, ''); /* the box is a nicety */ });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
