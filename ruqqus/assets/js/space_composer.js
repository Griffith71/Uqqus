/*
 * A Space's start time (templates/space_new.html, helpers/spaces.py): the time the member picks is in their own time
 * zone, so it is sent as a Unix time (`space_start`), which the server checks. Empty starts it now (a call: when its
 * host opens it). A box with a `[data-space-toggle]` tick box shows its options only while it is ticked.
 */
(function () {
  function sync(box) {
    var toggle = box.querySelector('[data-space-toggle]');
    var on = !toggle || toggle.checked;
    var options = box.querySelector('.space-options');
    if (options && toggle) options.hidden = !on;
    var when = box.querySelector('[data-space-when]');
    var start = box.querySelector('input[name="space_start"]');
    if (when && start) {
      var ts = when.value ? Math.floor(new Date(when.value).getTime() / 1000) : 0;
      start.value = on && ts > 0 ? String(ts) : '';
    }
  }

  document.addEventListener('change', function (event) {
    var box = event.target.closest && event.target.closest('[data-space-composer]');
    if (box) sync(box);
  });
  document.addEventListener('input', function (event) {
    var box = event.target.closest && event.target.closest('[data-space-composer]');
    if (box && event.target.matches('[data-space-when]')) sync(box);
  });
  document.querySelectorAll('[data-space-composer]').forEach(function (box) {
    var when = box.querySelector('[data-space-when]');
    if (when) {
      // nothing earlier than now (the browser's own picker honours it; the server checks again)
      var now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
      when.min = now.toISOString().slice(0, 16);
    }
    sync(box);
  });
})();
