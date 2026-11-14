// History > Manage (routes/history_mass_pages.py): counts what is ticked, keeps the button off until there is something to change, and asks once, in the
// site's own sheet, before anything irreversible goes. The server checks the count, the password and everything else again.
(function () {
  var form = document.getElementById('history-mass');
  if (!form) return;
  var boxes = form.querySelectorAll('.history-mass-box');
  var selected = document.getElementById('history-mass-selected');
  var button = document.getElementById('history-mass-submit');
  var count = document.getElementById('history-mass-count');
  var max = parseInt(form.dataset.max, 10) || 100;
  var ticked = function () { return Array.prototype.filter.call(boxes, function (b) { return b.checked; }).length; };
  var update = function () {
    var n = ticked();
    if (selected) selected.textContent = trn('%(num)s chosen', '%(num)s chosen', n, {num: n});
    if (button) button.disabled = n === 0 || n > max;
  };
  Array.prototype.forEach.call(boxes, function (b) { b.addEventListener('change', update); });
  update();
  var asking = false;
  form.addEventListener('submit', function (event) {
    var n = ticked();
    if (n === 0 || n > max) { event.preventDefault(); return; }
    var action = form.querySelector('input[name="action"]:checked');
    if (!action || asking) { if (!action) event.preventDefault(); return; }
    if (count.value === String(n)) return;      // asked already
    event.preventDefault();
    var deleting = action.value === 'delete';
    var ask = window.confirmSheet ? confirmSheet({
      title: deleting ? form.dataset.confirmDelete : form.dataset.confirmAnonymise,
      text: trn('%(num)s item will change. This can\'t be undone.', '%(num)s items will change. This can\'t be undone.', n, {num: n}),
      ok: deleting ? tr('Delete') : tr('Remove my name'),
      icon: deleting ? 'fa-trash-alt' : 'fa-user-secret'
    }) : Promise.resolve(confirm(trn('%(num)s item will change. This can\'t be undone.', '%(num)s items will change. This can\'t be undone.', n, {num: n})));
    asking = true;
    ask.then(function (yes) {
      asking = false;
      if (!yes) return;
      count.value = String(n);
      form.submit();
    });
  });
})();
