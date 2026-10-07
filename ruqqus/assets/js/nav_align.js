/*
 * Lines the navbar up with the page's columns (desktop, >= 992px):
 *   - the logo with the left sidebar's content,
 *   - the account section (the icon row) with the right sidebar's content,
 *   - the search box centered over the feed column.
 *
 * assets/style/main.scss has the values for the normal layout (a centered block of
 * sidebar + feed + sidebar). This script replaces them with the real column edges, so
 * the alignment also holds when the left sidebar is folded to its rail or a side panel
 * (assets/js/side_panels.js) takes the right sidebar's place. It only sets four CSS
 * variables on <html>; below 992px it removes them.
 *
 * A side with no column (chat, settings) keeps the default block edge.
 */
(function () {
  'use strict';

  var nav = document.getElementById('navbar');
  if (!nav) return;

  var root = document.documentElement;
  var wide = window.matchMedia('(min-width: 992px)');
  var BLOCK = 1326;       // sidebar 300 + feed 726 + sidebar 300
  var GUTTER = 15;        // the padding of a column: where its content starts
  var SEARCH_MAX = 500;
  var SEARCH_GAP = 16;    // kept clear between the search box and the icons
  var VARS = ['--nav-inset-left', '--nav-inset-right', '--nav-search-left', '--nav-search-width'];

  function shown(el) { return !!el && el.getClientRects().length > 0; }

  function clear() {
    VARS.forEach(function (name) { root.style.removeProperty(name); });
  }

  function align() {
    if (!wide.matches) { clear(); return; }

    var viewport = root.clientWidth;
    var blockWidth = Math.min(BLOCK, viewport);
    var left = (viewport - blockWidth) / 2;      // the default block
    var right = left + blockWidth;

    var leftColumn = document.getElementById('sidebar-left');
    if (shown(leftColumn)) left = leftColumn.getBoundingClientRect().left;

    var panel = document.getElementById('side-panel');
    var rightColumn = panel && !panel.hidden && shown(panel)
      ? panel
      : document.querySelector('#main-content-row > .sidebar:not(#sidebar-left)');
    if (shown(rightColumn)) right = rightColumn.getBoundingClientRect().right;

    root.style.setProperty('--nav-inset-left', (left + GUTTER) + 'px');
    root.style.setProperty('--nav-inset-right', (viewport - (right - GUTTER)) + 'px');

    // The search box: centered over the feed, never touching the logo or the icons.
    // Measuring here sees the insets just set (reading layout flushes it).
    var wrap = document.getElementById('navbar-search-wrap');
    var form = document.getElementById('searchform');
    if (!shown(wrap) || !form) return;

    root.style.removeProperty('--nav-search-left');
    root.style.removeProperty('--nav-search-width');
    var space = wrap.getBoundingClientRect();
    var main = document.getElementById('main-content-col');
    var feed = shown(main) ? main.getBoundingClientRect() : null;
    var centre = feed ? (feed.left + feed.right) / 2 : viewport / 2;

    var width = Math.max(0, Math.min(SEARCH_MAX, space.width - SEARCH_GAP));
    var start = Math.min(Math.max(centre - width / 2, space.left), space.right - SEARCH_GAP - width);
    root.style.setProperty('--nav-search-width', width + 'px');
    root.style.setProperty('--nav-search-left', Math.max(0, start - space.left) + 'px');
  }

  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; align(); });
  }

  // a sidebar can animate as it folds, a panel as it opens: look again once it has settled
  function scheduleSettled() {
    schedule();
    setTimeout(schedule, 300);
  }

  window.addEventListener('resize', schedule);
  (wide.addEventListener ? wide.addEventListener.bind(wide, 'change') : wide.addListener.bind(wide))(align);

  if (window.MutationObserver) {
    var observer = new MutationObserver(scheduleSettled);
    ['sidebar-left', 'main-content-row', 'side-panel'].forEach(function (id) {
      var node = document.getElementById(id);
      if (node) observer.observe(node, { attributes: true, attributeFilter: ['class', 'hidden', 'style'] });
    });
  }

  align();
  setTimeout(schedule, 250);                       // after fonts and late layout
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
})();
