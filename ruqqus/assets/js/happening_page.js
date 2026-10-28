/*
 * The /happening page (templates/happening.html). The server draws every time in UTC (and the month grid by UTC day);
 * this writes them in the reader's own zone, keeps countdowns ticking, chooses the Overview's Today rows, and puts an
 * instant on the reader's own day in the calendar. A civil date (a holiday) is the same day for everyone and never moves.
 * The zone is the browser's and is never sent anywhere. Without script the page is plain UTC and says so.
 */
(function () {
  'use strict';

  var CHIPS = 3;       // a day shows three chips and counts the rest (helpers/happening_calendar.py CHIPS)

  // the month grid: every chip with a UTC instant (and the spill, the day on each side of the grid) goes to the cell of the
  // reader's day; one whose day is outside the grid goes. A cell then shows three chips, counts the rest, and has a dot on a phone
  // when anything is on it.
  function placeCalendar(page, T, cal) {
    var cells = {};
    cal.querySelectorAll('td[data-day]').forEach(function (cell) { cells[cell.getAttribute('data-day')] = cell; });
    page.querySelectorAll('[data-chip][data-utc]').forEach(function (chip) {
      var target = cells[T.dayKey(Number(chip.getAttribute('data-utc')))];
      if (!target) { chip.remove(); return; }
      if (chip.closest('td') === target) return;
      target.appendChild(chip);
    });
    Object.keys(cells).forEach(function (key) {
      var cell = cells[key];
      var old = cell.querySelector('.cal-more');
      if (old) old.remove();
      var chips = cell.querySelectorAll('[data-chip]');
      chips.forEach(function (chip, i) { chip.hidden = i >= CHIPS; });
      cell.classList.toggle('has-events', chips.length > 0);
      if (chips.length > CHIPS) {
        var more = document.createElement('a');
        more.className = 'cal-more';
        more.href = '#cal-list';
        more.textContent = trn('%(num)s more', '%(num)s more', chips.length - CHIPS);
        cell.appendChild(more);
      }
    });
    var today = T.todayKey();
    cal.querySelectorAll('td.is-today').forEach(function (cell) { cell.classList.remove('is-today'); cell.removeAttribute('aria-current'); });
    if (cells[today]) { cells[today].classList.add('is-today'); cells[today].setAttribute('aria-current', 'date'); }
    var note = page.querySelector('[data-zone-note]');
    var zone = T.zone();
    // Translators: %(zone)s is a time zone name such as Europe/London
    if (note && zone) note.textContent = tr('Days and times are in your time zone (%(zone)s).', { zone: zone });
  }

  function run(page, T) {
    function refresh() {
      T.localizeTimes(page);
      T.tickUntil(page);
      page.querySelectorAll('[data-today-list]').forEach(T.applyToday);
    }
    refresh();
    var cal = page.querySelector('[data-calendar]');
    if (cal) placeCalendar(page, T, cal);
    setInterval(function () { if (!document.hidden) refresh(); }, 30000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  }

  // this script sits in the page's content, above the scripts default.html loads last (time_phrases.js): wait for them
  function start() {
    var page = document.getElementById('happening-page');
    var T = window.TimePhrases;
    if (page && T) run(page, T);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
