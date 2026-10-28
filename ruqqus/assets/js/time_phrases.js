/*
 * Time helpers for the "What's happening" box and page (templates/partials/happening_rows.html, assets/js/happening.js,
 * assets/js/happening_page.js). Exposed as window.TimePhrases; nothing else is global.
 *
 * Two kinds of time (helpers/site_events.py): a CIVIL date (`data-date`, a holiday: the same calendar day for everyone,
 * never moved) and an INSTANT (`data-utc`, a kick-off or an equinox: shown in the reader's own zone, and on the reader's
 * own day). The zone is the browser's and is never sent anywhere.
 *
 * The words of a countdown are the server's (helpers/happening_box.py until_phrase) so the text does not jump when the
 * script takes over: "now", "in N minutes", "in N hours", "in N days".
 */
(function () {
  'use strict';

  var DAY_MS = 86400000;

  function until(seconds) {
    seconds = Math.floor(seconds);
    if (seconds < 60) return tr('now');
    if (seconds < 120 * 60) return trn('in %(num)s minute', 'in %(num)s minutes', Math.floor(seconds / 60));
    if (seconds < 48 * 3600) return trn('in %(num)s hour', 'in %(num)s hours', Math.floor(seconds / 3600));
    return trn('in %(num)s day', 'in %(num)s days', Math.floor(seconds / 86400));
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  // the reader's calendar day of an instant (Unix seconds), as YYYY-MM-DD
  function dayKey(utc) {
    var d = new Date(utc * 1000);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function todayKey() { return dayKey(Date.now() / 1000); }

  // the UTC day number a YYYY-MM-DD names (to compare days without a time zone getting in the way)
  function dayNumber(key) {
    var p = key.split('-');
    return Math.floor(Date.UTC(+p[0], +p[1] - 1, +p[2]) / DAY_MS);
  }

  // the time of an instant in the reader's zone, "3:30 PM"
  function timeText(utc) {
    return new Date(utc * 1000).toLocaleTimeString(I18N.intl, { hour: 'numeric', minute: '2-digit' });
  }

  // the zone's name, "Europe/London"
  function zone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { return ''; }
  }

  // whether a row (data-date / data-end-date, or data-utc / data-end-utc) falls on the reader's day `key`
  function onDay(row, key) {
    var date = row.getAttribute('data-date');
    if (date) {
      var end = row.getAttribute('data-end-date') || date;
      return date <= key && key <= end;
    }
    var start = Number(row.getAttribute('data-utc'));
    if (!start) return false;
    var last = Number(row.getAttribute('data-end-utc')) || start;
    return dayKey(start) <= key && key <= dayKey(last > start ? last - 1 : last);
  }

  // an instant's clock time in the reader's zone, in place of the server's UTC text
  function localizeTimes(root) {
    root.querySelectorAll('time.happening-at[data-utc]').forEach(function (el) {
      var at = new Date(Number(el.getAttribute('data-utc')) * 1000);
      // data-full: the page's list shows the day as well as the time ("Sun, Nov 8, 3:30 PM")
      el.textContent = el.hasAttribute('data-full')
        ? at.toLocaleString(I18N.intl, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
        : timeText(Number(el.getAttribute('data-utc')));
      el.title = at.toLocaleString(I18N.intl);
    });
  }

  // keep every countdown in `root` current
  function tickUntil(root) {
    var now = Date.now() / 1000;
    root.querySelectorAll('time.happening-until[data-until-utc]').forEach(function (el) {
      var left = Number(el.getAttribute('data-until-utc')) - now;
      el.textContent = until(left);
    });
  }

  // show the rows of `list` that fall on the reader's today (at most data-today-rows, default 3), the rest stay hidden,
  // and the list's empty line only when none does. Returns how many are shown.
  function applyToday(list) {
    if (!list) return 0;
    var key = todayKey();
    var max = Number(list.getAttribute('data-today-rows')) || 3;
    var shown = 0;
    list.querySelectorAll('li.happening-row').forEach(function (row) {
      var show = shown < max && onDay(row, key);
      row.hidden = !show;
      row.classList.toggle('is-first', show && shown === 0);      // the first one SHOWN has no rule above it
      if (show) shown++;
    });
    var empty = list.querySelector('[data-today-empty]');
    if (empty) empty.hidden = shown > 0;
    // the sidebar box's Today is not drawn at all while nothing falls on the reader's own day (the page's column keeps its line)
    var section = list.closest('[data-section="today"]');
    if (section) section.hidden = shown === 0;
    return shown;
  }

  // milliseconds until the reader's next midnight (DST-safe: the clock is asked, not 24 hours added)
  function untilMidnight() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime() - now.getTime();
  }

  window.TimePhrases = {
    until: until, dayKey: dayKey, todayKey: todayKey, dayNumber: dayNumber, timeText: timeText, zone: zone,
    onDay: onDay, localizeTimes: localizeTimes, tickUntil: tickUntil, applyToday: applyToday, untilMidnight: untilMidnight
  };
})();
