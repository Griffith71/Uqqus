/*
 * Infinite scroll for post feeds (home, /all, guild, profile, search, ...).
 *
 * The server still pages 25 posts at a time and every page already renders a
 * Prev/Next control. This script reads the "Next" link of the current page,
 * fetches that page's normal HTML, lifts the post cards out of its post list
 * and appends them, then repeats when the visitor nears the bottom. With
 * JavaScript off the Prev/Next control is untouched and keeps working.
 *
 * Appended cards are wired up by bindPostCards() in all_js.js.
 */
(function () {
  'use strict';

  var container = document.getElementById('posts') || document.querySelector('.posts');
  if (!container || !('IntersectionObserver' in window)) return;

  var nav = document.querySelector('nav[aria-label="Page navigation"]');
  var nextUrl = nextLink(document);
  if (!nextUrl) return;

  // JavaScript is on: the page buttons are replaced by scrolling
  if (nav) nav.hidden = true;

  var sentinel = document.createElement('div');
  sentinel.id = 'infinite-sentinel';
  sentinel.className = 'text-center text-muted text-small py-3';
  sentinel.setAttribute('role', 'status');
  sentinel.setAttribute('aria-live', 'polite');
  container.parentNode.insertBefore(sentinel, container.nextSibling);

  var loading = false;
  var observer = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) loadMore();
  }, { rootMargin: '0px 0px 800px 0px' });
  observer.observe(sentinel);

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

  function showError() {
    sentinel.textContent = 'Could not load more posts. ';
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn btn-link btn-sm p-0 align-baseline';
    retry.textContent = 'Try again';
    retry.addEventListener('click', loadMore);
    sentinel.appendChild(retry);
  }

  function loadMore() {
    if (loading || !nextUrl) return;
    loading = true;
    sentinel.textContent = 'Loading…';

    fetch(new URL(nextUrl, window.location.href).href, { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var source = doc.getElementById('posts') || doc.querySelector('.posts');
        if (!source) throw new Error('no post list');

        // hold the new cards in a wrapper so only they are wired up
        var added = document.createElement('div');
        Array.prototype.slice.call(source.children).forEach(function (card) {
          // the page repeats shared markup (e.g. the delete-post modal): skip anything already here
          if (card.id && document.getElementById(card.id)) return;
          added.appendChild(document.adoptNode(card));
        });

        if (typeof bindPostCards === 'function') bindPostCards(added);
        while (added.firstChild) container.appendChild(added.firstChild);
        if (window.observeFeedVideos) window.observeFeedVideos();

        nextUrl = nextLink(doc);
        sentinel.textContent = '';
        loading = false;
        if (!nextUrl) {
          observer.disconnect();
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
})();
