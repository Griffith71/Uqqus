/*
 * The chat key's password lock, in the browser (audit M-03). The site keeps the account's Matrix recovery key only
 * locked; this file makes the key that locks it, from the member's password, and never sends it anywhere.
 *
 *  - Sign-in and sign-up forms (`data-chat-prekey`): when submitted, the typed password becomes a "pre-key"
 *    (PBKDF2-SHA256, 210,000 rounds, a fixed label as salt), kept in this browser's localStorage until the chat page
 *    uses it to unlock its key, then deleted. The chat client stretches it again with the account's own salt
 *    (assets/chat_src/src/keylock.js), so a stolen database still costs a full PBKDF2 per guess per account.
 *  - The password change form (`data-chat-rekey`): unlocks the chat key with the old password and locks it with the
 *    new one before the form goes, sending only the new locked copy (`chat_key_blob`).
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

  function remember(text) { try { localStorage.setItem(STORE, text); } catch (e) { /* private mode: chat asks */ } }

  window.ChatKey = { prekey: prekey, kek: kek, lock: lock, unlock: unlock, remember: remember, STORE: STORE };

  if (!window.crypto || !crypto.subtle) return;

  // sign-in and sign-up: keep the pre-key, then let the form go on as it would have
  document.querySelectorAll('form[data-chat-prekey]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (form.dataset.chatPrekeyDone) return;
      var field = form.querySelector('input[type=password]');
      if (!field || !field.value) return;
      event.preventDefault();
      prekey(field.value).then(remember).catch(function () {}).then(function () {
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
      fetch('/api/chat/key', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (info) {
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
                remember(pre[1]);
              });
          });
        })
        .catch(function () { /* the old copy stays; chat will ask for the recovery code */ })
        .then(go);
    });
  });

})();
