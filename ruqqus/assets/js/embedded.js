/*
 * Runs on pages shown inside a side panel frame (?embed=1, assets/js/side_panels.js).
 *
 * templates/default.html makes every link open in the full window (<base target="_top">).
 * Tab bars and Prev/Next controls move around inside the panel, so they stay in it.
 */
(function () {
  'use strict';

  document.addEventListener('click', function (event) {
    var link = event.target.closest ? event.target.closest('a[href]') : null;
    if (!link || !link.closest('.settings-nav, .pagination')) return;
    var url = new URL(link.getAttribute('href'), window.location.href);
    url.searchParams.set('embed', '1');
    link.setAttribute('href', url.pathname + url.search);
    link.target = '_self';
  }, true);

  // the notifications page has just marked its items read: tell the page holding the panel what is left
  if (window.parent !== window && window.location.pathname.indexOf('/notifications') === 0) {
    var badge = document.querySelector('#navbar a[href^="/notifications"] .badge-count');
    window.parent.postMessage({ type: 'ruqqus-notifications', unread: badge ? parseInt(badge.textContent, 10) || 0 : 0 }, window.location.origin);
  }
})();
