/*
 * "Show fewer posts like this" (routes/post_feedback.py, helpers/post_feedback.py).
 *
 * One delegated click handler for every `[data-fewer-post]` item, so the cards the continuous feed adds
 * later need no binding. In a feed the card folds into a one-line note with Undo; on a post's own page
 * the toast is the whole answer. Both ask the server, and only a "yes" changes the page.
 */
(function () {
  'use strict';

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function card(id) {
    var node = document.getElementById('post-' + id);
    return node && node.closest('.posts') ? node : null;
  }

  function fold(node, id) {
    if (node.classList.contains('post-fewer')) return;
    var note = make('div', 'post-fewer-note');
    note.setAttribute('role', 'status');
    note.appendChild(make('span', 'post-fewer-text', "We'll show fewer posts like this."));
    var undo = make('button', 'btn btn-link btn-sm post-fewer-undo', 'Undo');
    undo.type = 'button';
    undo.addEventListener('click', function () {
      undo.disabled = true;
      post_toast('/api/post/' + id + '/fewer?undo=1', function () {
        node.classList.remove('post-fewer');
        if (note.parentNode) note.parentNode.removeChild(note);
      }, function () { undo.disabled = false; });
    });
    note.appendChild(undo);
    node.classList.add('post-fewer');
    node.insertBefore(note, node.firstChild);
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('[data-fewer-post]') : null;
    if (!item) return;
    event.preventDefault();
    var id = item.getAttribute('data-fewer-post');
    if (!/^[0-9a-z]{1,10}$/.test(id)) return;
    post_toast('/api/post/' + id + '/fewer', function () {
      var node = card(id);
      if (node) fold(node, id);
    });
  });
})();
