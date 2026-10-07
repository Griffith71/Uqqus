/*
 * A continuous feed for post lists (home, /all, guild, profile, search, ...), as on x.com.
 *
 * The server still pages 25 posts at a time and every page already renders a Prev/Next
 * control. This script takes that control away (whether or not there is a next page),
 * reads the "Next" link of the current page, fetches that page's normal HTML, lifts the
 * post cards out of its post list and appends them when the visitor nears the bottom.
 * At the end of the feed it says so instead of showing buttons. With JavaScript off the
 * Prev/Next control is untouched and keeps working.
 *
 * Newer posts come in from the top:
 *   - touch: pull down at the very top of the page (the browser's own pull-to-reload is
 *     switched off for the page, see html.feed-live in the stylesheets) and the first
 *     page is fetched again; the cards that are not on the page yet are put above it;
 *   - everywhere: about every two minutes, while the tab is visible, the first page is
 *     fetched in the background; if it holds posts that are not on the page, a
 *     "Show N new posts" pill appears at the top of the feed.
 * Nothing is refreshed when the page was opened at ?page=N (N > 1).
 *
 * Added cards are wired up by bindPostCards() in all_js.js.
 */
(function () {
  'use strict';

  var container = document.getElementById('posts') || document.querySelector('.posts');
  if (!container || !('IntersectionObserver' in window)) return;

  var pagination = document.querySelector('ul.pagination');
  if (!pagination) return;                                 // not a paged feed

  var control = document.querySelector('nav[aria-label="Page navigation"]') || pagination;
  var nextUrl = nextLink(document);
  var startPage = parseInt(new URL(window.location.href).searchParams.get('page'), 10) || 1;
  var POST_ID = /^post-[0-9a-z]+$/;
  var PULL_AT = 60;                                        // px of pull (after resistance) that refreshes
  var PULL_MAX = 110;
  var CHECK_MS = window.FEED_POLL_MS || 120000;            // FEED_POLL_MS: a hook for tests
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // JavaScript is on: the page buttons are replaced by scrolling, and the browser's own
  // pull-to-reload gives way to the one below
  control.hidden = true;
  document.documentElement.classList.add('feed-live');

  var sentinel = document.createElement('div');
  sentinel.id = 'infinite-sentinel';
  sentinel.className = 'text-center text-muted text-small py-3';
  sentinel.setAttribute('role', 'status');
  sentinel.setAttribute('aria-live', 'polite');
  container.parentNode.insertBefore(sentinel, container.nextSibling);

  var loading = false;
  var refreshing = false;
  var observer = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) loadMore();
  }, { rootMargin: '0px 0px 800px 0px' });

  if (nextUrl) observer.observe(sentinel); else finish();

  // the href of the enabled "Next" link of a page (in the live document or a fetched one)
  function nextLink(doc) {
    var links = doc.querySelectorAll('ul.pagination a.page-link');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (a.textContent.trim() !== 'Next') continue;
      if (a.closest('li.page-item.disabled')) return null;
      return a.getAttribute('href');
    }
    return null;
  }

  // fetch a feed page and hand back its cards (the children of its post list)
  function fetchPage(url) {
    return fetch(new URL(url, window.location.href).href, { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var source = doc.getElementById('posts') || doc.querySelector('.posts');
        if (!source) throw new Error('no post list');
        return { doc: doc, cards: Array.prototype.slice.call(source.children) };
      });
  }

  // the cards that are not on this page yet, held in a wrapper so only they are wired up
  function take(cards) {
    var added = document.createElement('div');
    cards.forEach(function (card) {
      // the page repeats shared markup (e.g. the delete-post modal): skip anything already here
      if (card.id && document.getElementById(card.id)) return;
      added.appendChild(document.adoptNode(card));
    });
    return added;
  }

  function postCards(cards) {
    return cards.filter(function (card) { return POST_ID.test(card.id || ''); });
  }

  function freshCards(cards) {
    return postCards(cards).filter(function (card) { return !document.getElementById(card.id); });
  }

  function finish() {
    observer.disconnect();
    sentinel.textContent = '';
    if (!postCards(Array.prototype.slice.call(container.children)).length) return;
    sentinel.appendChild(document.createTextNode('You\u2019re all caught up. '));
    var top = document.createElement('button');
    top.type = 'button';
    top.className = 'btn btn-link btn-sm p-0 align-baseline';
    top.textContent = 'Back to top';
    top.addEventListener('click', function () { window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' }); });
    sentinel.appendChild(top);
  }

  function showError() {
    sentinel.textContent = 'Could not load more posts. ';
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-link btn-sm p-0 align-baseline';
    retry.textContent = 'Try again';
    retry.addEventListener('click', loadMore);
    sentinel.appendChild(retry);
  }

  // --- older posts, at the bottom ----------------------------------------------------------

  function loadMore() {
    if (loading || !nextUrl) return;
    loading = true;
    sentinel.textContent = 'Loading\u2026';

    fetchPage(nextUrl)
      .then(function (page) {
        var added = take(page.cards);
        if (typeof bindPostCards === 'function') bindPostCards(added);
        while (added.firstChild) container.appendChild(added.firstChild);
        if (window.observeFeedVideos) window.observeFeedVideos();

        nextUrl = nextLink(page.doc);
        sentinel.textContent = '';
        loading = false;
        if (!nextUrl) {
          finish();
        } else {
          // still near the bottom (a short page): look again
          observer.unobserve(sentinel);
          observer.observe(sentinel);
        }
      })
      .catch(function () {
        loading = false;
        showError();
      });
  }

  // --- newer posts, at the top -------------------------------------------------------------

  function firstPageUrl() {
    var url = new URL(window.location.href);
    url.searchParams.delete('page');
    return url.href;
  }

  // put the cards that are not on the page above the first card; how many posts that was
  function prepend(cards) {
    var added = take(cards);
    var count = postCards(Array.prototype.slice.call(added.children)).length;
    if (!count) return 0;
    if (typeof bindPostCards === 'function') bindPostCards(added);
    var first = container.firstChild;
    while (added.firstChild) container.insertBefore(added.firstChild, first);
    if (window.observeFeedVideos) window.observeFeedVideos();
    return count;
  }

  function refresh() {
    if (refreshing) return Promise.resolve(0);
    refreshing = true;
    return fetchPage(firstPageUrl()).then(function (page) {
      var count = prepend(page.cards);
      dropPill();
      refreshing = false;
      return count;
    }, function (error) {
      refreshing = false;
      throw error;
    });
  }

  function say(count) {
    return count === 1 ? '1 new post' : count + ' new posts';
  }

  // the pill: "Show N new posts", at the top of the feed, for anyone (a mouse has nothing to pull)
  var pill = null;
  var pillCards = null;
  var lastCheck = Date.now();

  function dropPill() {
    if (pill && pill.parentNode) pill.parentNode.removeChild(pill);
    pill = null;
    pillCards = null;
  }

  function showPill(cards, count) {
    pillCards = cards;
    pill = document.createElement('div');
    pill.className = 'feed-pill-wrap';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'feed-pill';
    button.textContent = 'Show ' + say(count);
    button.addEventListener('click', function () {
      var cards = pillCards;
      dropPill();
      prepend(cards);
      window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
    });
    pill.appendChild(button);
    container.parentNode.insertBefore(pill, container);
  }

  function checkNew() {
    if (startPage > 1 || document.hidden || pill || refreshing || loading) return;
    lastCheck = Date.now();
    fetchPage(firstPageUrl()).then(function (page) {
      var count = freshCards(page.cards).length;
      if (count && !pill) showPill(page.cards, count);
    }).catch(function () { /* the next check tries again */ });
  }

  if (startPage === 1) {
    setInterval(checkNew, CHECK_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && Date.now() - lastCheck > CHECK_MS) checkNew();
    });
  }

  // pull to refresh (touch): only from the very top, and never over a form, a sheet or a dropdown
  var indicator = null;
  var startY = null;
  var pulling = false;
  var pulled = 0;

  function makeIndicator() {
    indicator = document.createElement('div');
    indicator.id = 'feed-pull';
    indicator.setAttribute('role', 'status');
    indicator.setAttribute('aria-live', 'polite');
    var icon = document.createElement('i');
    icon.className = 'fas fa-arrow-down';
    var label = document.createElement('span');
    label.className = 'ml-2';
    indicator.appendChild(icon);
    indicator.appendChild(label);
    document.body.appendChild(indicator);
  }

  function place(distance, text, busy, dragging) {
    if (!indicator) makeIndicator();
    indicator.classList.toggle('dragging', !!dragging);   // follows the finger without lagging
    var rect = container.getBoundingClientRect();
    indicator.style.left = (rect.left + rect.width / 2) + 'px';
    indicator.style.transform = 'translate(-50%, ' + (distance - 48) + 'px)';
    indicator.style.opacity = String(Math.max(0, Math.min(1, distance / PULL_AT)));
    indicator.classList.toggle('ready', distance >= PULL_AT);
    indicator.classList.toggle('busy', !!busy);
    var icon = indicator.firstChild;
    icon.className = busy ? 'fas fa-spinner fa-spin' : 'fas fa-arrow-down';
    indicator.lastChild.textContent = text || (distance >= PULL_AT ? 'Release to refresh' : 'Pull to refresh');
  }

  function putAway(delay) {
    if (indicator) indicator.classList.remove('dragging');
    window.setTimeout(function () {
      if (indicator) { indicator.style.opacity = '0'; indicator.style.transform = 'translate(-50%, -48px)'; indicator.classList.remove('busy', 'ready'); }
    }, delay || 0);
  }

  document.addEventListener('touchstart', function (event) {
    startY = null;
    pulling = false;
    if (startPage > 1 || refreshing || window.scrollY > 0 || event.touches.length !== 1) return;
    if (document.body.classList.contains('modal-open')) return;
    var target = event.target;
    if (target && target.closest && target.closest('input, textarea, select, [contenteditable], .dropdown-menu.show, #side-panel')) return;
    startY = event.touches[0].clientY;
  }, { passive: true });

  document.addEventListener('touchmove', function (event) {
    if (startY === null) return;
    var drag = event.touches[0].clientY - startY;
    if (drag <= 0 || window.scrollY > 0) {
      if (pulling) { pulling = false; putAway(0); }
      if (window.scrollY > 0) startY = null;
      return;
    }
    pulling = true;
    pulled = Math.min(PULL_MAX, drag * 0.5);               // resistance, as on a phone
    if (event.cancelable) event.preventDefault();          // the page does not scroll or reload by itself
    place(pulled, null, false, true);
  }, { passive: false });

  function release() {
    var go = pulling && pulled >= PULL_AT;
    startY = null;
    pulling = false;
    if (!go) { putAway(0); return; }
    place(PULL_AT, 'Refreshing\u2026', true);
    refresh().then(function (count) {
      place(PULL_AT, count ? say(count) : 'You\u2019re all caught up', false);
      if (indicator) indicator.firstChild.className = 'fas fa-check';
      putAway(1400);
    }, function () {
      place(PULL_AT, 'Could not refresh', false);
      putAway(1800);
    });
  }

  document.addEventListener('touchend', release, { passive: true });
  document.addEventListener('touchcancel', function () { startY = null; pulling = false; putAway(0); }, { passive: true });
})();
