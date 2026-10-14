/*
 * The profile header's "⋯" menu (templates/partials/profile_header.html): copy the profile's link, block (after a
 * confirmation; POST /settings/block, then the page reloads into the "You are blocking" state) and report the
 * account (the sheet partials/report_account_modal.html, POST /api/user/<name>/report). Mute, Close Friends and
 * Add to curation use the site's own handlers (post_toast, curation_picker.js). Messages go in as text.
 */
(function () {
  'use strict';

  var NAME = /^[A-Za-z0-9_]{3,25}$/;

  function toast(message, ok) {
    var id = ok ? 'toast-post-success' : 'toast-post-error';
    var text = document.getElementById(id + '-text');
    if (!text) return;
    text.innerText = message;
    $('#' + id).toast('dispose');
    $('#' + id).toast('show');
  }

  function copy(url) {
    var done = function () { toast('Link copied.', true); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, function () { window.prompt('Copy this link:', url); });
    } else {
      window.prompt('Copy this link:', url);
    }
  }

  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('[data-profile-copy], [data-profile-block], [data-profile-report]') : null;
    if (!item) return;
    event.preventDefault();

    if (item.hasAttribute('data-profile-copy')) {
      copy(new URL(item.getAttribute('data-profile-copy'), window.location.origin).href);
      return;
    }

    var name = item.getAttribute('data-profile-block') || item.getAttribute('data-profile-report');
    if (!NAME.test(name || '')) return;

    if (item.hasAttribute('data-profile-block')) {
      if (!window.confirm('Block @' + name + '? They won\'t be able to follow you, message you or comment on your posts, and you won\'t see their posts. You can unblock them any time.')) return;
      post_toast('/settings/block?username=' + encodeURIComponent(name), function () { window.location.reload(); });
      return;
    }

    var modal = document.getElementById('reportAccountModal');
    if (!modal) return;
    modal.setAttribute('data-name', name);
    document.getElementById('report-account-name').textContent = '@' + name;
    document.getElementById('report-account-reason').value = '';
    document.getElementById('report-account-status').textContent = '';
    document.getElementById('report-account-send').disabled = false;
    $(modal).modal('show');
  });

  var form = document.getElementById('report-account-form');
  if (form) {
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var modal = document.getElementById('reportAccountModal');
      var name = modal.getAttribute('data-name') || '';
      var status = document.getElementById('report-account-status');
      var send = document.getElementById('report-account-send');
      var reason = document.getElementById('report-account-reason').value.trim();
      if (!NAME.test(name)) return;
      if (!reason) { status.textContent = 'Say what is wrong with this account.'; return; }
      send.disabled = true;
      status.textContent = 'Sending...';
      var body = new URLSearchParams();
      body.set('formkey', formkey());
      body.set('reason', reason);
      fetch('/api/user/' + encodeURIComponent(name) + '/report', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body
      }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (!res.ok) { send.disabled = false; status.textContent = res.data.error || 'Could not send the report.'; return; }
          $(modal).modal('hide');
          toast(res.data.message || 'Thanks. An admin will look at it.', true);
        })
        .catch(function () { send.disabled = false; status.textContent = 'Could not send the report. Try again.'; });
    });
  }
})();
