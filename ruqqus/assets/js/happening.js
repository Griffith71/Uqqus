/*
 * The "What's happening" box (templates/partials/happening_box.html, routes/happening_pages.py /inpage/happening).
 *
 * The right sidebar has an empty #happening-box placeholder (templates/partials/late_boxes.html). It is filled after the
 * page loads from its data-src, so no other page waits for the queries, and then kept current:
 *   - asks again every `data-next` seconds (the server says: 300 idle, 60 while something is live) with `?rev=`, the
 *     signature of what this tab has; "204 No Content" means nothing changed. Never faster, never while the tab is hidden
 *     or the sidebar is (a side panel, a phone, an embedded frame), and it STOPS (no retry) for 15 minutes after a 429 or
 *     a 5xx: thirty 429s in a minute earn an address a one-hour ban (routes/errors.py).
 *   - one poller per browser: tabs share what one of them fetched through a BroadcastChannel (nothing is stored), so ten
 *     tabs ask about as often as one.
 *   - the rows are swapped only where they changed, never while the pointer or the keyboard is inside the box, and a
 *     failed poll keeps what is shown (marked stale) instead of emptying it. Each list reserves three rows in CSS, so
 *     a row arriving or leaving moves nothing below the box; Live and Today are not drawn at all while they are empty
 *     (the owner, 2026-10-27), and Trending always is.
 *   - Today: the server sends every reader the same window around UTC today; the rows that fall on the READER'S today are
 *     shown here (assets/js/time_phrases.js), re-chosen at the reader's midnight. The time zone never leaves the browser.
 *   - a match changing state (Live, Final) is spoken once, in a visually hidden status line, at most every 30 seconds.
 */
(function () {
  'use strict';

  var slot = document.getElementById('happening-box');
  if (!slot || !slot.dataset.src) return;
  if (document.body.classList.contains('embedded')) return;

  var T = window.TimePhrases;
  var BACKOFF_MS = 15 * 60 * 1000;
  var ANNOUNCE_GAP_MS = 30000;
  var FETCH_TIMEOUT_MS = 15000;
  var SECTIONS = ['trending', 'live', 'today'];

  var loaded = false;
  var controller = null;
  var timer = null, midnight = null, ticker = null;
  var lastFetch = 0, blockedUntil = 0, lastAnnounce = 0;
  var pending = null;                // markup waiting for the pointer to leave the box
  var served = {};                   // each section's markup as the server last sent it (to swap only what changed)
  var channel = ('BroadcastChannel' in window) ? new BroadcastChannel('happening-box') : null;

  function box() { return slot.querySelector('.happening-box'); }
  function shown() { return slot.getClientRects().length > 0 && !document.hidden; }
  function interval() {
    var b = box();
    var n = b ? Number(b.getAttribute('data-next')) : 0;
    return (n > 0 ? n : 300) * 1000;
  }
  function hovering() { return slot.matches(':hover') || slot.contains(document.activeElement); }

  function section(root, name) { return root.querySelector('[data-section="' + name + '"]'); }

  function rememberServed(root) {
    SECTIONS.forEach(function (name) {
      var s = section(root, name);
      if (s) served[name] = s.innerHTML;
    });
  }

  // what the next page reserves. Never from a box nobody can see (a phone, a side panel): that would remember 0
  function storeHeight() {
    if (slot.getClientRects().length === 0) return;
    var height = Math.round(slot.getBoundingClientRect().height);
    if (height > 0) { try { localStorage.setItem('late_box_h:' + slot.id, String(height)); } catch (e) {} }
  }

  // markup that is the box (a CDN's error page or a login wall is not)
  function isBox(html) { return typeof html === 'string' && html.indexOf('happening-box') !== -1; }

  function markStale(on) {
    var b = box();
    if (b) b.classList.toggle('is-stale', !!on);
  }

  // --- what the script does to the markup the server sent ----------------------------------------------------
  function enhance() {
    var b = box();
    if (!b) return;
    T.localizeTimes(b);
    T.tickUntil(b);
    T.applyToday(b.querySelector('[data-today-list]'));
    storeHeight();
    scheduleMidnight();
    if (!ticker) ticker = setInterval(function () { if (shown() && box()) T.tickUntil(box()); }, 30000);
  }

  function statusMap(root) {
    var out = {};
    root.querySelectorAll('[data-section="live"] .happening-row').forEach(function (row) {
      var title = row.querySelector('.happening-title');
      out[row.getAttribute('data-key')] = {
        live: row.classList.contains('is-live'), final: row.classList.contains('is-final'),
        text: title ? title.textContent : ''
      };
    });
    return out;
  }

  function announce(before, after) {
    if (Date.now() - lastAnnounce < ANNOUNCE_GAP_MS) return;
    var line = slot.querySelector('[data-happening-status]');
    if (!line) return;
    Object.keys(after).some(function (key) {
      var was = before[key], now = after[key];
      if (!was) return false;
      if (!was.final && now.final) { line.textContent = now.text + ': ' + tr('Final'); }
      else if (!was.live && now.live) { line.textContent = now.text + ': ' + tr('Live'); }
      else return false;
      lastAnnounce = Date.now();
      return true;
    });
  }

  // --- putting new markup in -----------------------------------------------------------------------------------
  function apply(html) {
    if (!isBox(html)) {
      if (!loaded) { fill_late_box(slot, ''); slot.style.minHeight = '0'; } else { markStale(true); }
      return;
    }
    if (!loaded) {
      fill_late_box(slot, html);                   // all_js.js: puts it in, fades it in, remembers the height
      loaded = true;
      rememberServed(slot);
      enhance();
      return;
    }
    if (hovering()) { pending = html; return; }
    swap(html);
  }

  function swap(html) {
    pending = null;
    var holder = document.createElement('template');
    holder.innerHTML = html.trim();
    var next = holder.content.querySelector('.happening-box');
    var current = box();
    if (!next || !current) return;
    var before = statusMap(current);
    SECTIONS.forEach(function (name) {
      var a = section(current, name), b = section(next, name);
      if (a && b && served[name] !== b.innerHTML) {
        a.innerHTML = b.innerHTML;
        a.hidden = b.hidden;                       // Live and Today are not drawn while empty (Today's is then decided by applyToday)
        served[name] = b.innerHTML;
      }
    });
    ['data-rev', 'data-next', 'data-day'].forEach(function (attr) { current.setAttribute(attr, next.getAttribute(attr)); });
    markStale(false);
    announce(before, statusMap(current));
    enhance();
  }

  function flush() { if (pending !== null && !hovering()) swap(pending); }
  slot.addEventListener('mouseleave', flush);
  slot.addEventListener('focusout', function () { setTimeout(flush, 0); });

  // --- asking -------------------------------------------------------------------------------------------------
  function share(html) {                           // html: new markup; undefined: "nothing changed" (a 204)
    if (channel) { try { channel.postMessage(html === undefined ? { same: true } : { html: html }); } catch (e) {} }
  }
  if (channel) {
    channel.onmessage = function (event) {
      var data = event.data;
      if (!data || !loaded) return;
      if (typeof data.html !== 'string' && !data.same) return;
      lastFetch = Date.now();                       // another tab just asked: this one need not
      if (data.same) markStale(false); else apply(data.html);
      schedule();
    };
  }

  function request(force) {
    if (controller || !shown()) return;
    if (Date.now() < blockedUntil) { schedule(); return; }           // still waiting out a 429: try again when it is over
    if (!force && loaded && Date.now() - lastFetch < interval() / 2) { schedule(); return; }
    var b = box();
    var rev = b && loaded && !force ? b.getAttribute('data-rev') : '';
    var url = slot.dataset.src + (rev ? (slot.dataset.src.indexOf('?') < 0 ? '?' : '&') + 'rev=' + encodeURIComponent(rev) : '');
    var mine = ('AbortController' in window) ? new AbortController() : null;
    controller = mine || { abort: function () {} };
    var timedOut = false;
    var cutoff = setTimeout(function () { timedOut = true; if (mine) mine.abort(); }, FETCH_TIMEOUT_MS);
    var options = { credentials: 'same-origin' };
    if (mine) options.signal = mine.signal;
    fetch(url, options)
      .then(function (r) {
        if (r.status === 204) return { same: true };
        if (r.status === 429 || r.status >= 500) throw { backoff: true };
        if (!r.ok) throw { gone: true };            // 404: the layer is switched off
        return r.text().then(function (html) { return { html: html }; });
      })
      .then(function (res) {
        clearTimeout(cutoff);
        controller = null;
        lastFetch = Date.now();
        if (res.html !== undefined) { apply(res.html); share(res.html); }
        else { markStale(false); enhance(); share(undefined); }
        schedule();
      })
      .catch(function (e) {
        clearTimeout(cutoff);
        controller = null;
        if (e && e.name === 'AbortError' && !timedOut) return;
        if (e && e.backoff) blockedUntil = Date.now() + BACKOFF_MS;
        if (!loaded) { fill_late_box(slot, ''); slot.style.minHeight = '0'; return; }   // the box is a nicety: nothing, not an error
        markStale(true);
        if (!(e && e.gone)) schedule();
      });
  }

  function schedule() {
    clearTimeout(timer);
    if (!shown()) return;
    // after a 429 or a 5xx, wait out the whole block (and a little more, spread) before asking again
    var wait = Date.now() < blockedUntil
      ? blockedUntil - Date.now() + 1000 + Math.random() * 5000
      : interval() + 300 + Math.random() * 400;
    timer = setTimeout(function () { request(false); }, wait);
  }

  // --- the reader's day, and coming and going -------------------------------------------------------------------
  function dayChanged() {
    var b = box();
    if (!b) return;
    T.applyToday(b.querySelector('[data-today-list]'));
    storeHeight();
    var utcToday = new Date().toISOString().slice(0, 10);
    if (Math.abs(T.dayNumber(utcToday) - T.dayNumber(b.getAttribute('data-day') || utcToday)) >= 1) request(true);
    scheduleMidnight();
  }

  function scheduleMidnight() {
    clearTimeout(midnight);
    midnight = setTimeout(dayChanged, T.untilMidnight() + 1000);
  }

  function onShow() {
    if (!shown()) { clearTimeout(timer); return; }
    if (!loaded) { request(true); return; }
    var b = box();
    if (b) { T.applyToday(b.querySelector('[data-today-list]')); T.tickUntil(b); }
    if (Date.now() - lastFetch >= interval()) request(false); else schedule();
  }

  document.addEventListener('visibilitychange', onShow);
  window.addEventListener('pageshow', onShow);
  if (window.matchMedia) {
    var wide = window.matchMedia('(min-width: 992px)');
    if (wide.addEventListener) wide.addEventListener('change', onShow); else if (wide.addListener) wide.addListener(onShow);
  }
  var row = document.getElementById('main-content-row');
  if (row && 'MutationObserver' in window) {                  // a side panel opening or closing hides or shows the sidebar
    var settle = null;
    new MutationObserver(function () { clearTimeout(settle); settle = setTimeout(onShow, 120); })
      .observe(row, { attributes: true, attributeFilter: ['class'] });
  }

  onShow();
})();
