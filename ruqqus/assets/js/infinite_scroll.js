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
 * A reader who chose pages (html data-feed-paging="pages") gets none of this: see the early return below.
 *
 * Added cards are wired up by bindPostCards() in all_js.js.
 */
(function () {
  'use strict';

  var container = document.getElementById('posts') || document.querySelector('.posts');
  if (!container || !('IntersectionObserver' in window)) return;

  var pagination = document.querySelector('ul.pagination');
  if (!pagination) return;                                 // not a paged feed

  var control = pagination.closest('nav') || pagination;   // never found by its label: it is translated

  // The reader chose pages (Settings > Content > Layout > Feed: the feed_paging cookie, named on <html> by
  // partials/theme.html): the page buttons stay, the browser's own pull-to-reload stays, and nothing is fetched in
  // the background for newer posts. A feed that is only one page has two disabled buttons and nothing to turn to,
  // so that pager is put away.
  if (document.documentElement.dataset.feedPaging === 'pages') {
    if (!pagination.querySelector('a.page-link')) control.hidden = true;
    return;
  }

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

  // the href of the enabled next-page link of a page (in the live document or a fetched one): found by rel="next",
  // never by its label, which is in the reader's language
  function nextLink(doc) {
    var a = doc.querySelector('ul.pagination a.page-link[rel~="next"]');
    if (!a || a.closest('li.page-item.disabled')) return null;
    return a.getAttribute('href');
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
    sentinel.removeAttribute('aria-busy');
    if (!postCards(Array.prototype.slice.call(container.children)).length) return;
    sentinel.appendChild(document.createTextNode(tr('You\u2019re all caught up.') + ' '));
    var top = document.createElement('button');
    top.type = 'button';
    top.className = 'btn btn-link btn-sm p-0 align-baseline';
    top.textContent = tr('Back to top');
    top.addEventListener('click', function () { window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' }); });
    sentinel.appendChild(top);
  }

  function showError() {
    sentinel.textContent = tr('Could not load more posts.') + ' ';
    sentinel.removeAttribute('aria-busy');
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-link btn-sm p-0 align-baseline';
    retry.textContent = tr('Try again');
    retry.addEventListener('click', loadMore);
    sentinel.appendChild(retry);
  }

  // while the next page loads: two cards' shapes where the posts will be (all_js .skeleton-card), read as "Loading"
  function showSkeletons() {
    sentinel.textContent = '';
    sentinel.setAttribute('aria-busy', 'true');
    var label = document.createElement('span');
    label.className = 'sr-only';
    label.textContent = tr('Loading\u2026');
    sentinel.appendChild(label);
    for (var i = 0; i < 2; i++) {
      var card = document.createElement('div');
      card.className = 'skeleton-card text-left';
      card.setAttribute('aria-hidden', 'true');
      ['is-meta', 'is-title', '', 'is-short'].forEach(function (kind) {
        var line = document.createElement('span');
        line.className = 'skeleton-line' + (kind ? ' ' + kind : '');
        card.appendChild(line);
      });
      sentinel.appendChild(card);
    }
  }

  // --- older posts, at the bottom ----------------------------------------------------------

  function loadMore() {
    if (loading || !nextUrl) return;
    loading = true;
    showSkeletons();

    fetchPage(nextUrl)
      .then(function (page) {
        var added = take(page.cards);
        if (typeof bindPostCards === 'function') bindPostCards(added);
        while (added.firstChild) container.appendChild(added.firstChild);
        if (window.observeFeedVideos) window.observeFeedVideos();

        nextUrl = nextLink(page.doc);
        sentinel.textContent = '';
        sentinel.removeAttribute('aria-busy');
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
    return trn('%(num)s new post', '%(num)s new posts', count);
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
    button.textContent = trn('Show %(num)s new post', 'Show %(num)s new posts', count);
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
    indicator.lastChild.textContent = text || (distance >= PULL_AT ? tr('Release to refresh') : tr('Pull to refresh'));
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
    place(PULL_AT, tr('Refreshing\u2026'), true);
    refresh().then(function (count) {
      place(PULL_AT, count ? say(count) : tr('You\u2019re all caught up'), false);
      if (indicator) indicator.firstChild.className = 'fas fa-check';
      putAway(1400);
    }, function () {
      place(PULL_AT, tr('Could not refresh'), false);
      putAway(1800);
    });
  }

  document.addEventListener('touchend', release, { passive: true });
  document.addEventListener('touchcancel', function () { startY = null; pulling = false; putAway(0); }, { passive: true });
})();
