/*
 * "Host a Space" in the composers (partials/post_options.html space_field, helpers/spaces.py): ticking it shows the
 * start time and replay options; the time the member picks is in their own time zone, so it is sent as a Unix time
 * (`space_start`), which the server checks. Empty starts the Space when the host opens it. Works for every composer
 * on the page, the feed composer included.
 */
(function () {
  function sync(box) {
    var on = box.querySelector('[data-space-toggle]').checked;
    var options = box.querySelector('.space-options');
    if (options) options.hidden = !on;
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
