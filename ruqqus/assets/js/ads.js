/*
 * Ads (helpers/ads.py, routes/ads.py). Loaded only on a page that drew an ad box (or on the privacy page).
 *
 * - Asks AdSense to fill every box the reader can see, once each (a hidden one, the sidebar's on a phone, waits until it shows).
 * - Puts an in-feed ad after every 8th post card of the feed, from <template id="feed-ad"> (home.html), and no more than
 *   6 on one page however long it scrolls. It runs again for the cards the continuous feed adds (bindPostCards in all_js.js
 *   calls window.placeFeedAds), and counts the cards since the last ad, so ads stay 8 cards apart wherever cards are added.
 * - Stops and hides the page's ads when a card comes in marked data-ad-safe="0" (routes/ads.py ad_safe_mark): Google judges the
 *   whole page, and the server only judged the cards it drew.
 * - Folds away a box AdSense left empty.
 * - "Privacy and cookie settings" ([data-ad-consent]) loads the tag if this page hasn't (the privacy page, for a viewer with no
 *   ads) and reopens Google's consent banner once its API is ready; with no banner the link opens the privacy page.
 *
 * Does nothing when partials/ad_script.html held the tag back on this page.
 */
(function () {
  if (window.adsHeld) return;
  var EVERY = 8;
  var MAX_FEED = 6;
  var CONSENT_WAIT_MS = 5000;
  var POST_ID = /^post-[0-9a-z]+$/;
  var stopped = false;

  function fill(slot) {
    var ins = slot.querySelector('ins.adsbygoogle');
    if (stopped || !ins || ins.getAttribute('data-filled-by') === 'ads-js') return;
    if (!slot.getClientRects().length) return;      // hidden (the sidebar on a phone): AdSense can't fill a box with no size
    ins.setAttribute('data-filled-by', 'ads-js');
    // AdSense marks a box it could not fill with data-ad-status="unfilled"
    if ('MutationObserver' in window) {
      new MutationObserver(function () {
        if (ins.getAttribute('data-ad-status') === 'unfilled') slot.classList.add('ad-slot-empty');
      }).observe(ins, { attributes: true, attributeFilter: ['data-ad-status'] });
    }
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {}
  }

  function fillVisible() {
    document.querySelectorAll('.ad-slot').forEach(fill);
  }

  function stop() {
    stopped = true;
    document.querySelectorAll('.ad-slot').forEach(function (slot) { slot.classList.add('ad-slot-empty'); });
  }

  function placeFeedAds() {
    if (stopped) return;
    if (document.querySelector('[data-ad-safe="0"]')) return stop();
    var template = document.getElementById('feed-ad');
    var feed = document.getElementById('posts');
    if (!template || !feed || !template.content.firstElementChild) return;
    var run = 0;
    var placed = feed.querySelectorAll('.ad-slot-feed').length;
    Array.prototype.slice.call(feed.children).forEach(function (child) {
      if (child.classList.contains('ad-slot')) { run = 0; return; }
      if (!POST_ID.test(child.id || '')) return;
      run += 1;
      var next = child.nextElementSibling;
      if (run >= EVERY && placed < MAX_FEED && !(next && next.classList.contains('ad-slot'))) {
        var slot = template.content.firstElementChild.cloneNode(true);
        child.parentNode.insertBefore(slot, next);
        fill(slot);
        placed += 1;
        run = 0;
      }
    });
  }

  window.placeFeedAds = placeFeedAds;
  fillVisible();
  placeFeedAds();
  window.addEventListener('resize', fillVisible);

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('[data-ad-consent]');
    if (!link || !window.loadAdTag || !window.loadAdTag()) return;      // the tag can't load here: the link opens the privacy page
    event.preventDefault();
    var fc = window.googlefc = window.googlefc || {};
    fc.callbackQueue = fc.callbackQueue || [];
    var answered = false;
    fc.callbackQueue.push({ CONSENT_API_READY: function () { answered = true; fc.showRevocationMessage(); } });
    setTimeout(function () { if (!answered) location.href = link.href; }, CONSENT_WAIT_MS);     // Google's message isn't on this page
  });
})();
