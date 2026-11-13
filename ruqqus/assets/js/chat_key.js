/*
 * The chat key's password lock, in the browser (audit M-03). The site keeps the account's Matrix recovery key only
 * locked; this file makes the key that locks it, from the member's password, and never sends it anywhere.
 *
 *  - Sign-in and sign-up forms (`data-chat-prekey`): when submitted, the typed password becomes a "pre-key"
 *    (PBKDF2-SHA256, 210,000 rounds, a fixed label as salt), kept in this tab's sessionStorage until the chat page
 *    uses it to unlock its key, then deleted. Only this tab and the frames in it can read it: Google's ad tag, running
 *    in another tab of the site, reads localStorage as soon as a sign-in writes it, and can't read this. Chat opened in a new
 *    tab asks for the password once. The chat client stretches it again with the account's own salt
 *    (assets/chat_src/src/keylock.js), so a stolen database still costs a full PBKDF2 per guess per account.
 *    It is kept with the account it was typed for and a random id ({v: 1, name, k, at, ref}); the form sends the same id
 *    (`chat_prekey_ref`), which the sign-in keeps (helpers/session_cookie.py), and the chat page uses it only for that
 *    account and that sign-in: a sign-in typed with an email address names no account, and a pre-key an earlier sign-in
 *    left in another tab (its password since changed in another browser, by a reset or by a parent) names another id,
 *    so chat asks for the password once.
 *    It is written before the site has answered, so a refused sign-in leaves it: the next page drawn for someone who
 *    isn't signed in removes it (partials/forget_prekey.html).
 *  - The password change form (`data-chat-rekey`): unlocks the chat key with the old password and locks it with the
 *    new one before the form goes, sending only the new locked copy (`chat_key_blob`). It also notes the time in
 *    localStorage (`uqqus_chat_prekey_before`), so a pre-key any tab of this browser made from the old password is never
 *    used; it keeps a new one only when it could lock the key again with it.
 *
 * The numbers and labels here must match keylock.js exactly.
 */
(function () {
  var PREKEY_LABEL = 'uqqus-chat-prekey-v1';
  var PREKEY_ROUNDS = 210000;
  var KEK_ROUNDS = 100000;
  var STORE = 'uqqus_chat_prekey';
  var enc = new TextEncoder();

  function b64url(bytes) {
    var s = '';
    new Uint8Array(bytes).forEach(function (b) { s += String.fromCharCode(b); });
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function unb64url(text) {
    var s = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4));
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  function pbkdf2(secretBytes, saltBytes, rounds) {
    return crypto.subtle.importKey('raw', secretBytes, 'PBKDF2', false, ['deriveBits'])
      .then(function (base) {
        return crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: saltBytes, iterations: rounds }, base, 256);
      });
  }

  function prekey(password) {
    return pbkdf2(enc.encode(password), enc.encode(PREKEY_LABEL), PREKEY_ROUNDS).then(b64url);
  }

  function kek(prekeyText, salt) {
    return pbkdf2(unb64url(prekeyText), enc.encode(salt), KEK_ROUNDS).then(function (bits) {
      return crypto.subtle.importKey('raw', bits, 'AES-GCM', false, ['encrypt', 'decrypt']);
    });
  }

  function lock(key, text) {
    var iv = crypto.getRandomValues(new Uint8Array(12));
    return crypto.subtle.encrypt({ name: 'AES-GCM', iv: iv }, key, enc.encode(text)).then(function (ct) {
      var out = new Uint8Array(12 + ct.byteLength);
      out.set(iv, 0);
      out.set(new Uint8Array(ct), 12);
      return b64url(out);
    });
  }

  function unlock(key, blob) {
    var raw = unb64url(blob);
    return crypto.subtle.decrypt({ name: 'AES-GCM', iv: raw.slice(0, 12) }, key, raw.slice(12))
      .then(function (pt) { return new TextDecoder().decode(pt); });
  }

  // must match keylock.js accountName
  function accountName(name) {
    return String(name || '').trim().replace(/^@+/, '').toLowerCase();
  }

  // When this browser last sent a password change (localStorage, so every tab sees it; a time, nothing secret). A pre-key made before it
  // was made from a password that may no longer be the account's: the chat page never uses one (keylock.js takePrekey), so a tab that
  // signed in with the old password, never opened chat, and then saw the password changed (here or in another tab) asks for the
  // password instead of locking a new account's chat key with the old one.
  var CHANGED = 'uqqus_chat_prekey_before';

  function changedAt() {
    try { return Number(localStorage.getItem(CHANGED)) || 0; } catch (e) { return 0; }
  }

  function markChanged() {
    try { localStorage.setItem(CHANGED, String(Date.now())); } catch (e) { /* storage blocked: no pre-key was kept either */ }
  }

  function forget() {
    try { sessionStorage.removeItem(STORE); } catch (e) { /* nothing stored */ }
  }

  // Kept with the account it was typed for, which the chat page compares with its own (keylock.js takePrekey): a pre-key left in a tab
  // by an account that logged out in another tab is never used for whoever signs in next. `at`: when it was made, always after the last
  // password change this browser sent. `ref`: the sign-in it belongs to (the id its form sent, which the sign-in kept): the chat page uses
  // only its own sign-in's, so one an earlier sign-in left in another tab is never used.
  function remember(text, account, ref) {
    try {
      var at = Math.max(Date.now(), changedAt() + 1);
      sessionStorage.setItem(STORE, JSON.stringify({ v: 1, name: accountName(account), k: text, at: at, ref: ref || '' }));
    } catch (e) { /* private mode: chat asks */ }
  }

  // a new sign-in's id: 32 lower-case hex characters (helpers/session_cookie.py prekey_ref)
  function newRef() {
    var bytes = crypto.getRandomValues(new Uint8Array(16));
    var out = '';
    for (var i = 0; i < bytes.length; i++) out += (bytes[i] < 16 ? '0' : '') + bytes[i].toString(16);
    return out;
  }

  // the form sends the id with the password, in a field of its own (made here: a form sent without this script sends none, and its
  // sign-in has no pre-key for chat to use)
  function sendRef(form, ref) {
    var field = form.querySelector('input[name=chat_prekey_ref]');
    if (!field) {
      field = document.createElement('input');
      field.type = 'hidden';
      field.name = 'chat_prekey_ref';
      form.appendChild(field);
    }
    field.value = ref;
  }

  // the account a form is for: a signed-in form says so (data-chat-account); a sign-in or sign-up form, what was typed as the username
  function accountOf(form) {
    var named = form.getAttribute('data-chat-account');
    if (named) return named;
    var field = form.querySelector('[name=username]');
    return field ? field.value : '';
  }

  window.ChatKey = { prekey: prekey, kek: kek, lock: lock, unlock: unlock, remember: remember, newRef: newRef, STORE: STORE, CHANGED: CHANGED };

  if (!window.crypto || !crypto.subtle) return;

  // sign-in and sign-up: keep the pre-key, then let the form go on as it would have
  document.querySelectorAll('form[data-chat-prekey]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (form.dataset.chatPrekeyDone) return;
      var field = form.querySelector('input[type=password]');
      if (!field || !field.value) return;
      event.preventDefault();
      var account = accountOf(form);
      var ref = newRef();
      sendRef(form, ref);
      prekey(field.value).then(function (text) { remember(text, account, ref); }).catch(function () {}).then(function () {
        form.dataset.chatPrekeyDone = '1';
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      });
    });
  });

  // password change: lock the chat key again with the new password, in this browser
  document.querySelectorAll('form[data-chat-rekey]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (form.dataset.chatRekeyDone) return;
      var oldField = form.querySelector('[name=old_password]');
      var newField = form.querySelector('[name=new_password]');
      if (!oldField || !newField || !oldField.value || !newField.value) return;
      event.preventDefault();
      var go = function () {
        form.dataset.chatRekeyDone = '1';
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
      };
      // every pre-key made before now, in any tab, is from the old password; the one made below is from the new
      markChanged();
      forget();
      fetch('/api/chat/key', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (info) {
          // no password copy (chat never set up, or only the older server copy): nothing to lock again here, and the pre-key the
          // sign-in left is gone, so chat asks for the password once and checks it (crypto.js getPrekey)
          if (!info || !info.password_blob) return null;
          return Promise.all([prekey(oldField.value), prekey(newField.value)]).then(function (pre) {
            return kek(pre[0], info.salt).then(function (oldKey) { return unlock(oldKey, info.password_blob); })
              .then(function (recoveryKey) {
                return kek(pre[1], info.salt).then(function (newKey) { return lock(newKey, recoveryKey); });
              })
              .then(function (blob) {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'chat_key_blob';
                input.value = blob;
                form.appendChild(input);
                // this sign-in stays (helpers/account_sessions.py spares it), and with it its id, which the page names
                remember(pre[1], accountOf(form), form.getAttribute('data-chat-prekey-ref'));
              });
          });
        })
        .catch(function () { /* the old copy stays; chat will ask for the recovery code */ })
        .then(go);
    });
  });

})();
