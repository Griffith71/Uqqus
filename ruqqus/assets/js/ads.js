/*
 * Ads (helpers/ads.py, routes/ads.py). Loaded only on a page that drew an ad box (or on the privacy page).
 *
 * - Asks AdSense to fill every box on the page, once each.
 * - Puts an in-feed ad after every 8th post card of the feed, from <template id="feed-ad"> (home.html). It runs
 *   again for the cards the continuous feed adds (bindPostCards in all_js.js calls window.placeFeedAds), and
 *   counts the cards since the last ad, so ads stay 8 cards apart wherever cards are added.
 * - Folds away a box AdSense left empty.
 * - "Privacy and cookie settings" ([data-ad-consent]) reopens Google's consent banner when it is on this page.
 */
(function () {
  var EVERY = 8;
  var POST_ID = /^post-[0-9a-z]+$/;

  function fill(slot) {
    var ins = slot.querySelector('ins.adsbygoogle');
    if (!ins || ins.getAttribute('data-filled-by') === 'ads-js') return;
    ins.setAttribute('data-filled-by', 'ads-js');
    // AdSense marks a box it could not fill with data-ad-status="unfilled"
    if ('MutationObserver' in window) {
      new MutationObserver(function () {
        if (ins.getAttribute('data-ad-status') === 'unfilled') slot.classList.add('ad-slot-empty');
      }).observe(ins, { attributes: true, attributeFilter: ['data-ad-status'] });
    }
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {}
  }

  function placeFeedAds() {
    var template = document.getElementById('feed-ad');
    var feed = document.getElementById('posts');
    if (!template || !feed || !template.content.firstElementChild) return;
    var run = 0;
    Array.prototype.slice.call(feed.children).forEach(function (child) {
      if (child.classList.contains('ad-slot')) { run = 0; return; }
      if (!POST_ID.test(child.id || '')) return;
      run += 1;
      var next = child.nextElementSibling;
      if (run >= EVERY && !(next && next.classList.contains('ad-slot'))) {
        var slot = template.content.firstElementChild.cloneNode(true);
        child.parentNode.insertBefore(slot, next);
        fill(slot);
        run = 0;
      }
    });
  }

  window.placeFeedAds = placeFeedAds;
  document.querySelectorAll('.ad-slot').forEach(fill);
  placeFeedAds();

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('[data-ad-consent]');
    var fc = window.googlefc;
    if (!link || !fc || !fc.callbackQueue) return;      // no banner on this page: the link opens the privacy page
    event.preventDefault();
    fc.callbackQueue.push(function () { fc.showRevocationMessage(); });
  });
})();
