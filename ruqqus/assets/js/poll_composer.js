/*
 * The "Add a poll" panel of the composers (templates/partials/post_options.html poll_field, used by
 * Create a post, the feed composer and the Create post panel).
 *
 * The panel starts closed with all its fields disabled, so a closed panel sends nothing. "Add a poll"
 * opens it with two options (up to four), "+ Add option" shows the next row, the x on a row clears and
 * hides it, "Remove poll" closes it again. A form reset (the feed composer does one after posting)
 * closes every panel in it. The server checks everything again (helpers/polls.py).
 */
(function () {
  'use strict';

  var MAX_ROWS = 4;

  function setup(root) {
    var open = root.querySelector('.poll-open');
    var panel = root.querySelector('.poll-panel');
    var more = root.querySelector('.poll-more');
    var rows = Array.prototype.slice.call(root.querySelectorAll('.poll-row'));
    var hours = root.querySelector('select[name="poll_hours"]');
    if (!open || !panel || !rows.length) return;

    function fields() {
      return Array.prototype.slice.call(root.querySelectorAll('input[name="poll_option"], select[name="poll_hours"]'));
    }

    function visibleRows() {
      return rows.filter(function (row) { return !row.hidden; });
    }

    function sync() {
      more.hidden = visibleRows().length >= MAX_ROWS;
    }

    function show() {
      open.hidden = true;
      panel.hidden = false;
      fields().forEach(function (field) { field.disabled = false; });
      sync();
      var first = rows[0].querySelector('input');
      if (first) first.focus();
    }

    function hide() {
      panel.hidden = true;
      open.hidden = false;
      rows.forEach(function (row, i) {
        row.querySelector('input').value = '';
        row.hidden = i >= 2;
      });
      fields().forEach(function (field) { field.disabled = true; });
      if (hours) {
        var initial = Array.prototype.findIndex.call(hours.options, function (o) { return o.defaultSelected; });
        hours.selectedIndex = initial < 0 ? 0 : initial;
      }
    }

    open.addEventListener('click', show);
    root.querySelector('.poll-remove').addEventListener('click', hide);

    more.addEventListener('click', function () {
      var next = rows.filter(function (row) { return row.hidden; })[0];
      if (!next) return;
      next.hidden = false;
      next.querySelector('input').focus();
      sync();
    });

    rows.forEach(function (row) {
      var remove = row.querySelector('.poll-row-remove');
      if (!remove) return;
      remove.addEventListener('click', function () {
        row.querySelector('input').value = '';
        row.hidden = true;
        sync();
      });
    });

    var form = root.closest('form');
    if (form) form.addEventListener('reset', function () { window.setTimeout(hide, 0); });

    sync();
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-poll-composer]'), setup);
})();
