/*
 * The "..." menu of a row on the notifications page (templates/partials/notification_row.html, the controls from
 * helpers/notification_groups.py): unfollow a post, turn an account's or a guild's bell off, unfollow an account, leave a
 * guild, mute an account or a guild. Each item is a pair, "on" (following, bell on, muted ...) and "off"; it carries both
 * labels and both addresses, a click sends the one for the state it is in, and when that works every item for the same thing
 * on the page flips (the row stays, its notifications already came), so it can be undone from the same menu. The routes are
 * the site's own (post_follows, users, boards, muting); a bell route is a toggle, so the state drawn is what keeps it right.
 * Messages go in as text.
 */
(function () {
  'use strict';

  var NAME = /^[A-Za-z0-9_]{1,25}$/;
  var POST = /^[A-Za-z0-9]{1,16}$/;
  var KINDS = {
    'post-follow': POST, 'user-bell': NAME, 'user-follow': NAME, 'user-mute': NAME,
    'guild-bell': NAME, 'guild-member': NAME, 'guild-mute': NAME
  };

  // Undoing is one tap away, so toasts can follow each other fast. Bootstrap 4.3's toast throws when one is disposed while another
  // is on its way out (the pattern the other pages use), so this one only changes the text, shows the toast if it is not already
  // up, and hides the other kind instead of disposing it.
  function toast(message, ok) {
    var id = ok ? 'toast-post-success' : 'toast-post-error';
    var text = document.getElementById(id + '-text');
    if (!text) return;
    text.innerText = message;
    var other = $('#' + (ok ? 'toast-post-error' : 'toast-post-success'));
    if (other.hasClass('show')) other.toast('hide');
    var shown = $('#' + id);
    if (!shown.hasClass('show')) shown.toast('show');
  }

  function send(url, fields) {
    var body = new FormData();
    body.append('formkey', formkey());
    Object.keys(fields).forEach(function (key) { body.append(key, fields[key]); });
    return fetch(url, { method: 'POST', credentials: 'same-origin', body: body }).then(function (r) {
      return r.text().then(function (text) {
        var data = {};
        try { data = JSON.parse(text); } catch (e) { /* a 204, or not JSON */ }
        return { ok: r.ok, data: data };
      });
    });
  }

  // what to say when the route answers with no message (following and unfollowing an account: 204)
  function said(action, target, nowOn) {
    if (action === 'user-follow') {
      return nowOn ? tr('You are following @%(name)s.', { name: target }) : tr('You unfollowed @%(name)s.', { name: target });
    }
    return tr('Done.');
  }

  function put(item, on) {
    item.setAttribute('data-on', on ? '1' : '0');
    var label = item.querySelector('.notif-action-label');
    if (label) label.textContent = item.getAttribute(on ? 'data-label-on' : 'data-label-off') || '';
  }

  // the same thing in any other row's menu moves with it; an account that is no longer followed has no bell to switch
  function sync(action, target, on) {
    var all = document.querySelectorAll('[data-notif-action="' + action + '"]');
    Array.prototype.forEach.call(all, function (item) {
      if (item.getAttribute('data-target') === target) put(item, on);
    });
    if (action === 'user-follow') {
      var bells = document.querySelectorAll('[data-notif-action="user-bell"]');
      Array.prototype.forEach.call(bells, function (bell) {
        if (bell.getAttribute('data-target') !== target) return;
        bell.hidden = !on;
        if (on) put(bell, false);                      // a new follow starts with its bell off
      });
    }
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('[data-notif-action]') : null;
    if (!item) return;
    event.preventDefault();
    if (item.classList.contains('disabled')) return;

    var action = item.getAttribute('data-notif-action');
    var target = item.getAttribute('data-target') || '';
    var rule = KINDS[action];
    if (!rule || !rule.test(target)) return;

    var on = item.getAttribute('data-on') === '1';
    var url = item.getAttribute(on ? 'data-url-on' : 'data-url-off') || '';
    if (url.charAt(0) !== '/' || url.charAt(1) === '/') return;     // only this site's own routes
    var fields = {};
    var field = item.getAttribute('data-field');
    if (field) fields[field] = target;

    item.classList.add('disabled');
    send(url, fields).then(function (res) {
      item.classList.remove('disabled');
      if (!res.ok) {
        toast(res.data.error || tr('Something went wrong. Try again.'), false);
        return;
      }
      sync(action, target, !on);
      toast(res.data.message || said(action, target, !on), true);
    }, function () {
      item.classList.remove('disabled');
      toast(tr('Something went wrong. Try again.'), false);
    });
  });
})();
