/*
 * Trending (templates/partials/trending_box.html).
 *
 * The right sidebar has an empty #trending-box placeholder. It is filled after the
 * page loads from its data-src, so no other page waits for the query. Nothing is
 * fetched where the sidebar is not shown (phones, the side-panel frames): the same
 * list is the /trending page there.
 */
(function () {
  'use strict';

  var slot = document.getElementById('trending-box');
  if (!slot || !slot.dataset.src) return;
  if (document.body.classList.contains('embedded') || slot.offsetParent === null) return;

  fetch(slot.dataset.src, { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.text() : ''; })
    .then(function (html) { slot.innerHTML = html; })
    .catch(function () { /* the box is a nicety */ });
})();
