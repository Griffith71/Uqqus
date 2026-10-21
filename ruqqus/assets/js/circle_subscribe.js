// The subscribe sheet (templates/partials/circle_subscribe_modal.html): any [data-circle-open="<id>"] opens it on
// that circle's panel; a sheet with several paid circles switches between them with its tabs. Subscribing and
// cancelling post to the existing routes with the form key (post_toast) and reload the page, so the header and the
// gate redraw from the server. Text only: nothing here writes HTML.
(function () {
  var sheet = document.getElementById('circleSubscribeModal');
  if (!sheet) return;
  // the profile header is its own stacking context: a sheet drawn inside it would sit under the backdrop
  if (sheet.parentNode !== document.body) document.body.appendChild(sheet);

  function show(id) {
    var panels = sheet.querySelectorAll('[data-circle-panel]');
    var found = false;
    panels.forEach(function (panel) {
      var on = panel.getAttribute('data-circle-panel') === String(id);
      panel.hidden = !on;
      if (on) found = true;
    });
    if (!found && panels.length) panels[0].hidden = false;
    sheet.querySelectorAll('[data-circle-choice]').forEach(function (tab) {
      var on = tab.getAttribute('data-circle-choice') === String(id);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.classList.toggle('is-active', on);
    });
  }

  function say(button, text) {
    var panel = button.closest('[data-circle-panel]');
    var line = panel && panel.querySelector('.circle-sub-message');
    if (line) line.textContent = text;
  }

  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-circle-open]');
    if (opener) {
      event.preventDefault();
      show(opener.getAttribute('data-circle-open'));
      $(sheet).modal('show');
      return;
    }
    if (!sheet.contains(event.target)) return;

    var tab = event.target.closest('[data-circle-choice]');
    if (tab) {
      show(tab.getAttribute('data-circle-choice'));
      return;
    }
    var join = event.target.closest('[data-circle-subscribe]');
    if (join && !join.disabled) {
      join.disabled = true;
      say(join, 'Subscribing…');
      post_toast('/api/circles/' + encodeURIComponent(join.getAttribute('data-circle-subscribe')) + '/subscribe',
        function () { window.location.reload(); },
        function (data) { join.disabled = false; say(join, (data && data.error) || 'That didn\'t work. Try again.'); });
      return;
    }
    var cancel = event.target.closest('[data-circle-cancel]');
    if (cancel && !cancel.disabled) {
      if (cancel.getAttribute('data-armed') !== '1') {
        // ask once, in place: the first press only says what will happen
        cancel.setAttribute('data-armed', '1');
        cancel.textContent = 'Yes, cancel it';
        say(cancel, 'It won\'t renew. You keep access until the day you paid to.');
        return;
      }
      cancel.disabled = true;
      say(cancel, 'Cancelling…');
      post_toast('/api/circles/' + encodeURIComponent(cancel.getAttribute('data-circle-cancel')) + '/cancel',
        function () { window.location.reload(); },
        function (data) { cancel.disabled = false; say(cancel, (data && data.error) || 'That didn\'t work. Try again.'); });
    }
  });
})();
