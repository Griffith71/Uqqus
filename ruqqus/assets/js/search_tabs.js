/*
 * The search results page's tab bar (templates/partials/search_tabs.html). Five labels do not always fit a phone, and every
 * tab is a page load that starts the bar at its beginning, so the active tab is brought into view when the page opens: at
 * once, never animated (nothing moves on load), and only the bar scrolls, never the page.
 */
(function () {
  'use strict';

  var bar = document.querySelector('[data-search-tabs]');
  if (!bar || bar.scrollWidth <= bar.clientWidth) return;
  var active = bar.querySelector('.active');
  if (!active) return;
  var bounds = bar.getBoundingClientRect();
  var tab = active.getBoundingClientRect();
  // the bar's own scroll only (scrollIntoView could scroll the page too); the sign of the offset follows the reading direction
  bar.scrollLeft += (tab.left + tab.width / 2) - (bounds.left + bounds.width / 2);
})();
