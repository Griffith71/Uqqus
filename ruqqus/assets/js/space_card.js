/*
 * The Space card under a post (templates/partials/space_card.html): a scheduled start shown in the reader's own time
 * zone, and "Remind me", which asks POST /api/spaces/<id>/remind (routes/spaces.py). Clicks are taken on the
 * document, so cards the continuous feed adds work too; their times are formatted by bindPostCards (all_js.js).
 */
(function () {
  function formatSpaceTimes(root) {
    (root || document).querySelectorAll('[data-space-time]').forEach(function (el) {
      var ts = Number(el.getAttribute('data-space-time'));
      if (!ts || el.dataset.formatted) return;
      var when = new Date(ts * 1000);
      var today = new Date();
      var sameDay = when.toDateString() === today.toDateString();
      el.textContent = (sameDay ? 'Today, ' : when.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }) + ', ')
        + when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      el.dataset.formatted = '1';
    });
  }
  window.formatSpaceTimes = formatSpaceTimes;
  formatSpaceTimes(document);

  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('[data-space-remind]');
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();                 // the card's stretched link must not open the post
    var on = button.getAttribute('aria-pressed') !== 'true';
    var body = new URLSearchParams();
    body.set('formkey', typeof formkey === 'function' ? formkey() : '');
    body.set('on', on ? '1' : '0');
    button.disabled = true;
    fetch('/api/spaces/' + encodeURIComponent(button.getAttribute('data-space-remind')) + '/remind', {
      method: 'POST', credentials: 'same-origin', body: body, headers: { Accept: 'application/json' }
    }).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
      if (!data) return;
      button.setAttribute('aria-pressed', data.reminded ? 'true' : 'false');
      button.textContent = data.reminded ? 'Reminder set' : 'Remind me';
      button.classList.toggle('btn-secondary', !!data.reminded);
      button.classList.toggle('btn-outline-primary', !data.reminded);
    }).catch(function () {}).then(function () { button.disabled = false; });
  });
})();
