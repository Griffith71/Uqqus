/*
 * QR sign-in in the browser (CLAUDE.md "QR sign-in", routes/qr_login_pages.py).
 *
 *  - The sign-in page (`#qr-login`): a press asks for a code, then this asks every 3 s what has become of it, while the tab is
 *    showing. It carries no code: the cookie ties it to this browser. It waits 15 s after a 429 and stops after three, since
 *    thirty 429s put an address in time out. Only Confirm finishes.
 *  - The phone (`#qr-approve`): the code is in the address's fragment, which no server sees, and goes from the address bar at once.
 *
 * Words are in the templates, one `.qr-view` each; nothing here draws HTML.
 */
(function () {
  var POLL_MS = window.QR_POLL_MS || 3000;
  var SLOW_MS = window.QR_SLOW_MS || 15000;
  var MAX_429 = 3;

  // never follows a redirect: a sign-in that has ended answers one to the sign-in page, whose 200 would read as success
  function post(path, fields) {
    var body = new URLSearchParams();
    Object.keys(fields || {}).forEach(function (name) { body.set(name, fields[name]); });
    return fetch(path, { method: 'POST', credentials: 'same-origin', body: body, redirect: 'manual' });
  }

  // What a route really said: only its own JSON counts as an answer, and a limiter's page has none.
  function readAnswer(answer) {
    if (answer.type === 'opaqueredirect') return Promise.resolve({ signedOut: true, status: 0, data: null });
    return answer.json().catch(function () { return null; }).then(function (data) {
      return { signedOut: false, status: answer.status, data: data };
    });
  }

  // A button at work stays focused and in the tab order, unlike one that is `disabled`, so the focus can follow the view that comes next.
  function setBusy(button, on) {
    button.setAttribute('aria-disabled', on ? 'true' : 'false');
    button.classList.toggle('disabled', on);
  }

  function isBusy(button) {
    return button.getAttribute('aria-disabled') === 'true';
  }

  function say(line, words) {
    line.textContent = words || '';
    line.hidden = !words;
  }

  // One view at a time, in one place: a view's own words are said once by the live region, since a screen reader may not notice one
  // merely becoming visible, and if the focus was in the view going away it follows to the new one (a hidden button drops it to the page).
  // A view marked `data-qr-focus-view` takes the focus itself and comes to the top of the screen, since what it says first is the next step
  // and it may have been pressed for from well below it.
  function show(panel, name) {
    var active = document.activeElement;
    var lost = active && active.closest && active.closest('.qr-view') && panel.contains(active);
    var shown = null;
    panel.querySelectorAll('.qr-view').forEach(function (view) {
      var here = view.getAttribute('data-qr-view') === name;
      view.classList.toggle('is-current', here);
      if (here) shown = view;
    });
    if (!shown) return;
    if (lost && !shown.contains(active)) {
      var itself = shown.hasAttribute('data-qr-focus-view');
      var target = itself ? shown : shown.querySelector('button, a, input');
      if (!target) {
        target = shown;
      }
      if (target === shown) shown.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: itself });
      if (itself) shown.scrollIntoView({ block: 'start' });
    }
    var sayer = panel.querySelector('.qr-say');
    var words = shown.querySelector('[data-qr-say]');
    if (sayer) sayer.textContent = words ? words.textContent : '';
  }

  function forgetPrekey() {
    // this browser may hold one from a password it typed and got wrong; chat must ask for the right one
    try { sessionStorage.removeItem('uqqus_chat_prekey'); } catch (e) { /* storage blocked: nothing was kept */ }
    try { localStorage.removeItem('uqqus_chat_prekey'); } catch (e) { /* storage blocked: nothing was kept */ }
  }

  // --- the screen that shows the code ---------------------------------------------------------------------------------------
  var desktop = document.getElementById('qr-login');
  if (desktop) {
    var panel = document.getElementById('qr-panel');
    var startButton = document.getElementById('qr-start');
    var backButton = document.getElementById('qr-back');
    var form = document.querySelector('form[data-chat-prekey]');
    var confirmButton = document.getElementById('qr-confirm');
    var confirmError = document.getElementById('qr-confirm-error');
    var timer = null, endsAt = 0, slowUntil = 0, limited = 0, running = false;

    var stop = function () {
      running = false;
      clearTimeout(timer);
    };

    var ended = function (view) {
      stop();
      show(panel, view);
    };

    var next = function (delay) {
      clearTimeout(timer);
      if (running) timer = setTimeout(poll, delay);
    };

    // Past the code's end there is one more question, whatever the answer: a phone may have approved it in its last seconds, and then
    // it has told someone to press Confirm here. An approval outlasts the code (APPROVED_SECONDS).
    var again = function (delay) {
      if (Date.now() >= endsAt) return ended('expired');
      next(delay);
    };

    var poll = function () {
      if (!running) return;
      if (document.hidden) return;                                   // picked up again by visibilitychange
      if (Date.now() < slowUntil) return next(slowUntil - Date.now());
      fetch('/login/qr/status', { credentials: 'same-origin' }).then(function (answer) {
        if (!running) return null;
        if (answer.status === 429) {
          limited += 1;
          if (limited >= MAX_429) return ended('error');
          slowUntil = Date.now() + SLOW_MS;
          return next(SLOW_MS);
        }
        if (!answer.ok) return again(POLL_MS);                       // a passing fault: ask again until the code runs out
        return answer.json().then(function (data) {
          if (!running) return;
          if (data.state === 'waiting') return again(POLL_MS);
          if (data.state === 'approved') {
            stop();
            var heading = document.getElementById('qr-confirm-heading');
            heading.textContent = heading.getAttribute('data-template').replace('__NAME__', '@' + data.name);
            say(confirmError, '');
            show(panel, 'approved');
            heading.focus();                                         // never on Confirm: Enter must not sign anyone in by accident
            return;
          }
          ended(data.state === 'denied' ? 'denied' : 'expired');
        });
      }).catch(function () {
        again(POLL_MS);
      });
    };

    var start = function () {
      stop();
      limited = 0;
      slowUntil = 0;
      form.hidden = true;
      startButton.hidden = true;
      backButton.hidden = false;
      panel.hidden = false;
      backButton.focus();                                            // the button just pressed may have gone
      var image = document.getElementById('qr-image');
      image.removeAttribute('src');
      show(panel, 'waiting');
      post('/login/qr/start').then(function (answer) {
        if (!answer.ok) throw new Error('refused');
        return answer.json();
      }).then(function (data) {
        image.src = data.image;
        endsAt = Date.now() + data.seconds * 1000;
        running = true;
        next(POLL_MS);
      }).catch(function () {
        ended('error');
      });
    };

    // The code goes, and the page is back where it was before the press.
    var backToStart = function () {
      stop();
      post('/login/qr/cancel').catch(function () { /* it runs out by itself */ });
      panel.hidden = true;
      backButton.hidden = true;
      startButton.hidden = false;
      form.hidden = false;
      startButton.focus();
    };

    startButton.addEventListener('click', start);
    panel.querySelectorAll('[data-qr-new]').forEach(function (button) { button.addEventListener('click', start); });
    backButton.addEventListener('click', backToStart);
    document.getElementById('qr-cancel').addEventListener('click', backToStart);

    confirmButton.addEventListener('click', function () {
      if (isBusy(confirmButton)) return;
      setBusy(confirmButton, true);
      say(confirmError, '');
      var fault = function () {
        setBusy(confirmButton, false);
        say(confirmError, desktop.getAttribute('data-fault'));       // the approval still stands for now: Confirm can be pressed again
      };
      post('/login/qr/finish', { redirect: desktop.getAttribute('data-redirect') || '/' }).then(readAnswer).then(function (got) {
        if (got.status === 200 && got.data && got.data.redirect) {
          forgetPrekey();
          location.replace(got.data.redirect);
          return;
        }
        if (got.status === 403) {                                    // signed in on this browser in another tab meanwhile: that is what to see
          location.replace('/');
          return;
        }
        if (got.status === 410) {
          setBusy(confirmButton, false);
          show(panel, got.data && got.data.state === 'denied' ? 'denied' : 'expired');
          return;
        }
        fault();
      }).catch(fault);
    });

    document.addEventListener('visibilitychange', function () {
      if (running && !document.hidden) poll();
    });
  }

  // --- the phone that approves it ---------------------------------------------------------------------------------------------
  var phone = document.getElementById('qr-approve');
  if (phone) {
    var found = /[#&]c=([A-Za-z0-9_-]+)/.exec(location.hash);
    // out of the address bar, the history and anything a later page could read, whatever happens next
    if (location.hash) history.replaceState(null, '', location.pathname);
    // a new code scanned while this page is still open arrives as a change of fragment, with no new page
    window.addEventListener('hashchange', function () { location.reload(); });
    if (phone.getAttribute('data-state') === 'ready') {
      var code = found ? found[1] : '';
      var key = phone.getAttribute('data-formkey');
      var faultWords = phone.getAttribute('data-fault');
      var screen = phone.querySelector('.qr-panel');
      var deny = document.getElementById('qr-deny');
      var approveForm = document.getElementById('qr-approve-form');
      var error = document.getElementById('qr-error');

      var loadFacts = function () {
        show(screen, 'loading');
        post('/login/qr/info', { code: code, formkey: key }).then(readAnswer).then(function (got) {
          if (got.signedOut) return show(screen, 'signed-out');
          if (got.status === 410) return show(screen, 'gone');
          if (got.status !== 200 || !got.data) return show(screen, 'fault');
          document.getElementById('qr-fact-browser').textContent = got.data.browser;
          document.getElementById('qr-fact-place').textContent = got.data.place;
          document.getElementById('qr-fact-when').textContent = got.data.when;
          show(screen, 'ready');
          deny.focus();
        }).catch(function () {
          show(screen, 'fault');
        });
      };

      if (!code) show(screen, 'gone');
      else loadFacts();

      document.getElementById('qr-retry').addEventListener('click', loadFacts);

      // Says nobody signed in only when the site said so: until then the code may well still work, and "Not me" stays to be pressed.
      screen.querySelectorAll('[data-qr-deny]').forEach(function (button) {
        button.addEventListener('click', function () {
          if (isBusy(button)) return;
          setBusy(button, true);
          var line = button.nextElementSibling;
          say(line, '');
          var failed = function () {
            setBusy(button, false);
            say(line, faultWords);
          };
          post('/login/qr/deny', { code: code, formkey: key }).then(readAnswer).then(function (got) {
            if (got.signedOut) return show(screen, 'signed-out');
            if (got.status === 200 && got.data && got.data.ok === true) return show(screen, 'denied');
            if (got.status === 410) return show(screen, 'too-late');
            failed();
          }).catch(failed);
        });
      });

      approveForm.addEventListener('submit', function (event) {
        event.preventDefault();
        var answerField = document.getElementById('qr-answer');
        var button = document.getElementById('qr-approve-button');
        if (isBusy(button)) return;
        setBusy(button, true);
        error.hidden = true;
        var refused = function (words) {
          say(error, words || faultWords);
          answerField.focus();
        };
        post('/login/qr/approve', { code: code, formkey: key, answer: answerField.value }).then(readAnswer).then(function (got) {
          if (got.signedOut) return show(screen, 'signed-out');
          if (got.status === 200 && got.data && got.data.ok === true) return show(screen, 'approved');
          if (got.status === 410) return show(screen, 'gone');
          refused(got.data && got.data.error);
        }).catch(function () {
          refused();
        }).then(function () {
          setBusy(button, false);
          answerField.value = '';
        });
      });
    }
  }
})();
