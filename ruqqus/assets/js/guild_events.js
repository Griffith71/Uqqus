/*
 * Guild events (templates/guild/events.html, event.html, event_edit.html, partials/guild_next_event.html;
 * routes/guild_events.py):
 *   - every <time data-utc> is written again in the reader's own time zone (the server's text is UTC);
 *   - on the calendar each event moves to the reader's own day, and the note says the zone;
 *   - Going / Interested / Not going answer without a reload and show the counts the server sends back (never who);
 *   - the event form sends its datetime-local fields as Unix times (starts_utc, ends_utc).
 * Text only: everything is set with textContent.
 */
(function () {
  if (window.__guildEvents) return;
  window.__guildEvents = true;

  var zone = '';
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { zone = ''; }
  var lang = document.documentElement.lang || undefined;

  function fmt(utc, options) {
    try { return new Date(utc * 1000).toLocaleString(lang, options); } catch (e) { return ''; }
  }
  function sameDay(a, b) {
    var x = new Date(a * 1000), y = new Date(b * 1000);
    return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate();
  }
  function dayKey(utc) {
    var d = new Date(utc * 1000);
    var m = String(d.getMonth() + 1), day = String(d.getDate());
    return d.getFullYear() + '-' + (m.length < 2 ? '0' + m : m) + '-' + (day.length < 2 ? '0' + day : day);
  }

  var LONG = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' };
  var SHORT = { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' };
  var TIME = { hour: 'numeric', minute: '2-digit' };

  function localTimes(root) {
    root.querySelectorAll('time[data-utc]').forEach(function (el) {
      var start = parseInt(el.getAttribute('data-utc'), 10);
      var end = parseInt(el.getAttribute('data-end'), 10);
      if (!start) return;
      var text = fmt(start, el.hasAttribute('data-long') ? LONG : SHORT);
      if (end && el.hasAttribute('data-long')) text += ' – ' + fmt(end, sameDay(start, end) ? TIME : LONG);
      else if (end && sameDay(start, end)) text += ' – ' + fmt(end, TIME);
      if (text) el.textContent = text;
      if (zone) el.title = zone;
    });
    root.querySelectorAll('[data-day-badge][data-utc]').forEach(function (el) {
      var utc = parseInt(el.getAttribute('data-utc'), 10);
      if (utc) el.textContent = fmt(utc, { day: 'numeric', month: 'short' });
    });
  }

  function placeOnCalendar() {
    var grid = document.querySelector('[data-calendar]');
    if (!grid) return;
    grid.querySelectorAll('[data-calendar-event]').forEach(function (chip) {
      var utc = parseInt(chip.getAttribute('data-utc'), 10);
      var cell = grid.querySelector('[data-day="' + dayKey(utc) + '"]');
      if (cell) {
        cell.appendChild(chip);
        chip.title = fmt(utc, TIME);
      } else {
        chip.remove();       // its day in this zone is outside the grid shown
      }
    });
    var note = document.querySelector('[data-zone-note]');
    if (note && zone) note.textContent = 'Days and times are in your time zone (' + zone + ').';
  }

  function rsvp() {
    var page = document.querySelector('[data-rsvp]');
    if (!page) return;
    var box = page.querySelector('.event-rsvp [role=alert]');
    page.addEventListener('click', function (event) {
      var button = event.target.closest('[data-rsvp-status]');
      if (!button) return;
      var body = new URLSearchParams({ status: button.getAttribute('data-rsvp-status'), formkey: typeof formkey === 'function' ? formkey() : '' });
      page.querySelectorAll('[data-rsvp-status]').forEach(function (b) { b.disabled = true; });
      fetch(page.getAttribute('data-rsvp'), { method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || 'That didn’t work.'); return d; }); })
        .then(function (d) {
          if (box) box.hidden = true;
          page.querySelector('[data-count="going"]').textContent = d.going;
          page.querySelector('[data-count="interested"]').textContent = d.interested;
          page.querySelectorAll('[data-rsvp-status]').forEach(function (b) {
            var status = b.getAttribute('data-rsvp-status');
            if (status === 'none') { b.hidden = !d.mine; return; }
            var on = d.mine === status;
            b.classList.toggle('btn-primary', on);
            b.classList.toggle('btn-secondary', !on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
          });
        })
        .catch(function (e) { if (box) { box.textContent = e.message; box.hidden = false; } })
        .then(function () { page.querySelectorAll('[data-rsvp-status]').forEach(function (b) { b.disabled = false; }); });
    });
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function toLocalInput(utc) {
    var d = new Date(utc * 1000);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function fromLocalInput(value) {
    if (!value) return '';
    var t = new Date(value).getTime();   // a datetime-local value is read in the browser's own zone
    return isNaN(t) ? '' : String(Math.round(t / 1000));
  }

  function eventForm() {
    var form = document.querySelector('[data-event-form]');
    if (!form) return;
    var start = form.querySelector('#event-start'), end = form.querySelector('#event-end');
    [start, end].forEach(function (input) {
      var utc = parseInt(input.getAttribute('data-utc'), 10);
      if (utc) input.value = toLocalInput(utc);
    });
    function sync() {
      form.querySelector('[name=starts_utc]').value = fromLocalInput(start.value);
      form.querySelector('[name=ends_utc]').value = fromLocalInput(end.value);
    }
    start.addEventListener('change', sync);
    end.addEventListener('change', sync);
    // runs before guild_pages.js reads the form (capture phase)
    form.addEventListener('submit', sync, true);
    var note = form.querySelector('[data-zone-note]');
    if (note && zone) note.textContent = 'Times are in your time zone (' + zone + '). ' + note.textContent.replace(/^Times are in your time zone\. /, '');
  }

  function run() {
    localTimes(document);
    placeOnCalendar();
    rsvp();
    eventForm();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
