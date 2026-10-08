/*
 * Voting in a poll in place (templates/partials/poll.html, routes/polls.py).
 *
 * Each option before you vote is a submit button of a plain form (it works without script). This
 * script takes the click, sends the vote, and swaps the poll for the one the server draws after
 * counting it. It listens on the document, so cards added by scrolling work too. The poll belongs to
 * the primary post: the same vote is counted whichever copy of the post it is made from.
 */
(function () {
  'use strict';

  function toast(error, text) {
    var id = error ? 'toast-post-error' : 'toast-post-success';
    var box = document.getElementById(id + '-text');
    if (!box || !window.$) return;
    box.textContent = text;
    $('#' + id).toast('dispose');
    $('#' + id).toast('show');
  }

  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('.poll .poll-option[type="submit"]') : null;
    if (!button) return;

    var poll = button.closest('.poll');
    var form = button.closest('form');
    if (!poll || !form) return;

    event.preventDefault();
    event.stopPropagation();                 // a card is a link as a whole: voting is not opening the post
    var buttons = poll.querySelectorAll('.poll-option');
    Array.prototype.forEach.call(buttons, function (b) { b.disabled = true; });

    var body = new URLSearchParams();
    body.set('formkey', form.querySelector('input[name="formkey"]').value);
    body.set('option', button.value);

    fetch(poll.getAttribute('data-poll-url'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (response) {
      return response.json().then(function (data) { return { ok: response.ok, data: data }; });
    }).then(function (res) {
      if (!res.ok) {
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
        toast(true, res.data.error || 'Could not count your vote.');
        return;
      }
      // the server's own markup (templates/partials/poll.html, labels escaped there); parsed, never run
      var fresh = new DOMParser().parseFromString(res.data.html, 'text/html').querySelector('.poll');
      if (fresh && poll.parentNode) poll.parentNode.replaceChild(document.importNode(fresh, true), poll);
    }).catch(function () {
      Array.prototype.forEach.call(buttons, function (b) { b.disabled = false; });
      toast(true, 'Could not count your vote. Try again.');
    });
  });
})();
