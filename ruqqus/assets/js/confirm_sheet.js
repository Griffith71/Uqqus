/*
 * The page's one "are you sure?" sheet, and the block flow that uses it (partials/confirm_sheet.html).
 *
 * Blocking used to be the browser's own confirm() on a profile, nothing at all from a post or comment menu (one tap), and a comment menu
 * that threw after the toast. Now every Block asks first in a frosted bottom sheet (a centred card on a desktop), says what happened with
 * an Undo, and updates the page in place. The same sheet replaces the other native confirm()s a member meets (a permanent erase, deleting
 * a story, a highlight, a live update, a bookmark folder).
 *
 *   confirmSheet({ title, text, ok, icon })   -> Promise<boolean>   (true only for the red button; Escape, Cancel and a tap outside are false)
 *   blockUser(name, done)                     asks, POSTs /settings/block, then done(xhr) and a toast with Undo (POST /settings/unblock)
 *   commentBlocked(id, name)                  swaps one comment for the placeholder the server draws after a reload
 *
 * Every text goes in as text (textContent), never as markup. Where the sheet isn't in the page (a frame, an error page) confirmSheet falls
 * back to the browser's own confirm(), so an action is never lost.
 */
(function () {
  'use strict';

  var NAME = /^[A-Za-z0-9_]{3,25}$/;
  var pending = null;

  function sheet() { return document.getElementById('confirmSheet'); }

  function settle(value) {
    var done = pending;
    pending = null;
    if (done) done(value);
  }

  // One question at a time, in the order asked: a second one waits for the first to close (it used to be answered "no" without ever
  // showing when it came while the sheet was still closing, and two dialogs at once cannot stack in Bootstrap 4)
  var queue = Promise.resolve();

  window.confirmSheet = function (options) {
    options = options || {};
    var el = sheet();
    var plain = [options.title, options.text].filter(Boolean).join('\n\n');
    if (!el || !window.jQuery || !window.jQuery.fn.modal) return Promise.resolve(window.confirm(plain));
    var run = function () { return ask(el, options); };
    queue = queue.then(run, run);
    return queue;
  };

  function ask(el, options) {
    settle(false);
    el.querySelector('#confirm-sheet-title').textContent = options.title || '';
    el.querySelector('#confirm-sheet-text').textContent = options.text || '';
    el.querySelector('#confirm-sheet-text').hidden = !options.text;
    el.querySelector('#confirm-sheet-ok').textContent = options.ok || tr('Continue');
    var icon = el.querySelector('#confirm-sheet-icon');
    icon.className = 'fas text-danger fa-stack-1x ' + (options.icon || 'fa-user-slash');
    return new Promise(function (resolve) {
      pending = resolve;
      var $el = window.jQuery(el);
      var opener = document.activeElement;            // where the keyboard was: it goes back there, as it did from the browser's own confirm()
      // the story viewer is a full-screen layer of the site's own at z-index 2000, above Bootstrap's dialogs: a sheet asked from inside it
      // is lifted over it for as long as it is open
      document.body.classList.toggle('confirm-over-overlay', !!document.querySelector('.story-viewer:not([hidden])'));
      $el.off('.confirmsheet');
      // the least destructive button has the focus (an alertdialog's rule), so Enter never confirms by accident
      $el.one('shown.bs.modal.confirmsheet', function () {
        var cancel = el.querySelector('.btn-secondary');
        if (cancel) cancel.focus();
      });
      $el.one('hidden.bs.modal.confirmsheet', function () {
        document.body.classList.remove('confirm-over-overlay');
        settle(false);
        if (opener && opener !== document.body && document.contains(opener) && opener.focus) opener.focus();
      });
      el.querySelector('#confirm-sheet-ok').onclick = function () { settle(true); $el.modal('hide'); };
      // Bootstrap 4 can't stack two dialogs (the first one closing takes the page's scroll lock and its own backdrop with it): a sheet
      // that is still open (a post's actions sheet, which the button just tapped is about to close) finishes closing first
      var other = document.querySelector('.modal.show:not(#confirmSheet)');
      if (other) window.jQuery(other).one('hidden.bs.modal', function () { $el.modal('show'); }).modal('hide');
      else $el.modal('show');
    });
  }

  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    Array.prototype.forEach.call(document.querySelectorAll('form[data-confirmed]'), function (form) { form.removeAttribute('data-confirmed'); });
  });

  // a form that must be confirmed before it is sent: onsubmit="return confirmForm(event, { title, text, ok })"
  window.confirmForm = function (event, options) {
    var form = event.target;
    if (form.getAttribute('data-confirmed')) return true;
    event.preventDefault();
    window.confirmSheet(options).then(function (yes) {
      if (!yes) return;
      form.setAttribute('data-confirmed', '1');
      form.submit();
    });
    return false;
  };

  // a toast that offers to take the action back: shown for a few seconds, never stacked on an older one
  function undoToast(message, onUndo) {
    var box = document.getElementById('toast-undo');
    if (!box || !window.jQuery) return;
    document.getElementById('toast-undo-text').textContent = message;
    var button = document.getElementById('toast-undo-button');
    button.onclick = function () { window.jQuery(box).toast('hide'); onUndo(); };
    window.jQuery(box).toast('dispose');
    window.jQuery(box).toast('show');
  }

  function say(message, ok) {
    var id = ok ? 'toast-post-success' : 'toast-post-error';
    var text = document.getElementById(id + '-text');
    if (!text || !window.jQuery) return;
    text.innerText = message;
    window.jQuery('#' + id).toast('dispose');
    window.jQuery('#' + id).toast('show');
  }

  function unblock(name, done) {
    post_toast('/settings/unblock?username=' + encodeURIComponent(name), function (xhr) { if (done) done(xhr); });
  }

  // options.toast === false: the caller reloads the page (the profile), which would take the toast with it; the page it lands on, "You are
  // blocking @x", has the Unblock button that is its Undo
  window.blockUser = function (name, done, options) {
    if (!NAME.test(name || '')) return;
    window.confirmSheet({
      title: tr('Block @%(name)s?', { name: name }),
      text: tr('They won\'t be able to follow you, message you or comment on your posts, and you won\'t see their posts. You can unblock them any time.'),
      ok: tr('Block'),
      icon: 'fa-user-slash'
    }).then(function (yes) {
      if (!yes) return;
      var url = '/settings/block?username=' + encodeURIComponent(name);
      post_toast(url, function (xhr) {
        if (done) done(xhr);
        if (options && options.toast === false) return;
        undoToast(tr('Blocked @%(name)s.', { name: name }), function () {
          unblock(name, function () { window.location.reload(); });
        });
      });
    });
  };

  // the comment a member has just blocked its author from: the text goes, the line the server writes after a reload takes its place
  window.commentBlocked = function (id, name) {
    var main = document.getElementById('comment-' + id + '-only');
    if (!main || !NAME.test(name || '')) return;
    while (main.firstChild) main.removeChild(main.firstChild);
    var line = document.createElement('div');
    line.className = 'user-info';
    line.textContent = '[' + tr('You are blocking @%(name)s', { name: name }) + ']';
    var undo = document.createElement('a');
    undo.href = 'javascript:void(0)';
    undo.className = 'ml-1';
    undo.textContent = tr('Unblock');
    undo.addEventListener('click', function () { unblock(name, function () { window.location.reload(); }); });
    line.appendChild(undo);
    main.appendChild(line);
  };
})();
