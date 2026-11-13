// Settings > Chat > Notifications (routes/push_pages.py): turning push notifications on and off in this browser. The browser
// makes the subscription (its push service's address and two keys) with the site's public key; the site keeps it and sends
// notifications through it (helpers/push_send.py). Everything here is set as text.
(function () {
  var state = document.getElementById('push-state');
  var on = document.getElementById('push-on');
  var off = document.getElementById('push-off');
  var ios = document.getElementById('push-ios');
  if (!state || !on || !off) return;
  var tr = window.tr || function (s) { return s; };
  var formkey = function () { return typeof window.formkey === 'function' ? window.formkey() : ''; };

  function say(text) { state.textContent = text; }
  function show(which) {
    on.hidden = which !== 'on';
    off.hidden = which !== 'off';
  }

  function post(url, fields) {
    var data = new FormData();
    data.append('formkey', formkey());
    Object.keys(fields).forEach(function (k) { data.append(k, fields[k]); });
    return fetch(url, { method: 'POST', body: data, credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || tr('That didn\'t work. Try again.'));
        return body;
      });
    });
  }

  function keyBytes(text) {
    var b = text.replace(/-/g, '+').replace(/_/g, '/');
    b += '==='.slice((b.length + 3) % 4);
    return Uint8Array.from(atob(b), function (c) { return c.charCodeAt(0); });
  }

  function b64u(buffer) {
    var s = '';
    new Uint8Array(buffer).forEach(function (byte) { s += String.fromCharCode(byte); });
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  var supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  var apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var installed = window.matchMedia && window.matchMedia('(display-mode: standalone)').matches;
  if (!supported) {
    say(tr('This browser can\'t show notifications from this site.'));
    if (apple && !installed && ios) ios.hidden = false;
    return;
  }

  var registration = navigator.serviceWorker.register('/sw.js', { scope: '/' });

  function refresh() {
    if (Notification.permission === 'denied') {
      show('');
      say(tr('Notifications are blocked for this site in this browser. Allow them in the browser\'s settings to turn them on.'));
      return;
    }
    registration.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      return fetch('/api/push/key' + (sub ? '?endpoint=' + encodeURIComponent(sub.endpoint) : ''), { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (info) {
          if (sub && info.on) {
            show('off');
            say(tr('Notifications are on in this browser.'));
          } else {
            show('on');
            say(tr('Notifications are off in this browser.'));
          }
        });
    }).catch(function () {
      show('');
      say(tr('Notifications couldn\'t be checked. Reload the page to try again.'));
    });
  }

  on.addEventListener('click', function () {
    on.disabled = true;
    Notification.requestPermission().then(function (answer) {
      if (answer !== 'granted') throw new Error(tr('Notifications weren\'t allowed for this site.'));
      return Promise.all([registration, fetch('/api/push/key', { credentials: 'same-origin' }).then(function (r) { return r.json(); })]);
    }).then(function (both) {
      var reg = both[0];
      return reg.pushManager.getSubscription().then(function (old) { return old ? old.unsubscribe() : null; }).then(function () {
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(both[1].key) });
      });
    }).then(function (sub) {
      return post('/api/push/subscribe', { endpoint: sub.endpoint, p256dh: b64u(sub.getKey('p256dh')), auth: b64u(sub.getKey('auth')) });
    }).then(refresh, function (e) {
      say(e && e.message ? e.message : tr('Notifications couldn\'t be turned on. Try again.'));
    }).then(function () { on.disabled = false; });
  });

  off.addEventListener('click', function () {
    off.disabled = true;
    registration.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      if (!sub) return null;
      return post('/api/push/unsubscribe', { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); });
    }).then(refresh, function (e) { say(e.message); }).then(function () { off.disabled = false; });
  });

  refresh();
})();
