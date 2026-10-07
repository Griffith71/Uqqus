/*
 * Send a post to a chat (templates/partials/share_chat_modal.html).
 *
 * The share menu of a post has "Send in chat". It opens a sheet with your chats and the people
 * you follow; one tap on Send puts the post's link, and an optional message, into that chat.
 *
 * Chats are end-to-end encrypted and the keys are in the browser, so the message cannot be written
 * by the server. It is sent by the chat page itself: this script keeps /chat?embed=1 in a hidden
 * frame (the side panel's own chat frame when there is one, so there is never a second chat client),
 * waits until that page says it is ready and asks it to send. The chat page only listens to its
 * own origin and its parent (assets/chat_src/src/index.js).
 */
(function () {
  'use strict';

  var modal = document.getElementById('shareChatModal');
  if (!modal) return;

  var list = document.getElementById('share-chat-list');
  var search = document.getElementById('share-chat-search');
  var note = document.getElementById('share-chat-note');
  var status = document.getElementById('share-chat-status');

  var READY_WAIT = 25000;
  var SEND_WAIT = 15000;
  var current = null;          // { url } the post being sent
  var chat = null;             // { frame, ready: Promise } - made once, on first use
  var waiting = {};            // message id -> { resolve, reject, timer }
  var seq = 0;
  var searchTimer = null;
  var loadToken = 0;

  function say(text, isError) {
    status.textContent = text || '';
    status.className = 'text-small mt-2 ' + (isError ? 'text-danger' : 'text-muted');
  }

  // --- the chat page, in a frame ---------------------------------------------------------
  function chatFrame() {
    if (window.RuqqusPanels && window.RuqqusPanels.chatFrame) return window.RuqqusPanels.chatFrame();
    var frame = document.createElement('iframe');
    frame.src = '/chat?embed=1';
    frame.hidden = true;
    frame.title = 'Chat';
    document.body.appendChild(frame);
    return frame;
  }

  function startChat() {
    var frame = chatFrame();
    var ready = new Promise(function (resolve, reject) {
      var started = Date.now();
      var timer = setInterval(function () {
        if (Date.now() - started > READY_WAIT) {
          clearInterval(timer);
          reject(new Error('Chat is still starting. Try again in a moment.'));
          return;
        }
        if (frame.contentWindow) frame.contentWindow.postMessage({ type: 'ruqqus-chat-ping' }, window.location.origin);
      }, 400);
      waiting.ready = { resolve: function () { clearInterval(timer); resolve(); } };
    });
    chat = { frame: frame, ready: ready };
    // a failed start is tried again next time
    ready.catch(function () { chat = null; });
    return chat;
  }

  window.addEventListener('message', function (event) {
    if (event.origin !== window.location.origin || !chat || event.source !== chat.frame.contentWindow) return;
    var data = event.data;
    if (!data || typeof data.type !== 'string') return;
    if (data.type === 'ruqqus-chat-ready' && waiting.ready) {
      waiting.ready.resolve();
      delete waiting.ready;
    } else if (data.type === 'ruqqus-chat-sent' && waiting[data.id]) {
      var entry = waiting[data.id];
      delete waiting[data.id];
      clearTimeout(entry.timer);
      if (data.ok) entry.resolve(); else entry.reject(new Error(typeof data.error === 'string' ? data.error : 'Could not send.'));
    }
  });

  function sendMessage(roomId, text) {
    var session = chat || startChat();
    return session.ready.then(function () {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        waiting[id] = {
          resolve: resolve,
          reject: reject,
          // a frame that stopped answering (it was reloaded) is started afresh next time
          timer: setTimeout(function () { delete waiting[id]; chat = null; reject(new Error('Chat did not answer. Try again.')); }, SEND_WAIT)
        };
        session.frame.contentWindow.postMessage({ type: 'ruqqus-chat-send', id: id, room_id: roomId, text: text }, window.location.origin);
      });
    });
  }

  // --- the people ------------------------------------------------------------------------
  function messageText() {
    var written = note.value.trim();
    return (written ? written + '\n' : '') + current.url;
  }

  function startConversation(username) {
    var body = new URLSearchParams();
    body.set('formkey', formkey());
    body.set('username', username);
    return fetch('/api/chat/start?json=1', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body
    }).then(function (r) {
      if (!r.ok) throw new Error(r.status === 403 ? 'You can\'t message that person.' : 'Could not start that chat.');
      return r.json();
    }).then(function (data) { return data.room_id; });
  }

  function sendTo(target, button) {
    button.disabled = true;
    button.textContent = 'Sending…';
    say('');
    var room = target.room_id ? Promise.resolve(target.room_id) : startConversation(target.username);
    room.then(function (roomId) {
      target.room_id = roomId;
      return sendMessage(roomId, messageText());
    }).then(function () {
      button.textContent = 'Sent';
      button.classList.remove('btn-primary');
      button.classList.add('btn-secondary');
    }).catch(function (error) {
      button.disabled = false;
      button.textContent = 'Retry';
      say(error && error.message ? error.message : 'Could not send.', true);
    });
  }

  function row(target) {
    var item = document.createElement('div');
    item.className = 'share-chat-row d-flex align-items-center py-2';
    var avatar = document.createElement('img');
    avatar.className = 'rounded-circle mr-2';
    avatar.width = avatar.height = 36;
    avatar.alt = '';
    if (typeof target.profile_url === 'string' && /^(https?:\/\/|\/)/.test(target.profile_url)) avatar.src = target.profile_url;
    var name = document.createElement('div');
    name.className = 'flex-grow-1 text-truncate font-weight-bold';
    name.textContent = '@' + target.username;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-primary btn-sm ml-2';
    button.textContent = 'Send';
    button.addEventListener('click', function () { sendTo(target, button); });
    item.appendChild(avatar);
    item.appendChild(name);
    item.appendChild(button);
    return item;
  }

  function heading(text) {
    var h = document.createElement('div');
    h.className = 'text-small text-muted text-uppercase mt-2';
    h.textContent = text;
    return h;
  }

  function draw(targets) {
    list.textContent = '';
    if (!targets.length) {
      var empty = document.createElement('p');
      empty.className = 'text-muted text-small mb-0';
      empty.textContent = search.value.trim()
        ? 'No one matches. You can send to your chats and to people you follow.'
        : 'Nobody to send to yet. Follow someone, or start a chat from their profile.';
      list.appendChild(empty);
      return;
    }
    var recent = targets.filter(function (t) { return t.recent; });
    var people = targets.filter(function (t) { return !t.recent; });
    if (recent.length) list.appendChild(heading('Chats'));
    recent.forEach(function (t) { list.appendChild(row(t)); });
    if (people.length) list.appendChild(heading('People you follow'));
    people.forEach(function (t) { list.appendChild(row(t)); });
  }

  function load() {
    var token = ++loadToken;
    fetch('/api/chat/share_targets?q=' + encodeURIComponent(search.value.trim()), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('failed'); return r.json(); })
      .then(function (data) { if (token === loadToken) draw(data.targets || []); })
      .catch(function () { if (token === loadToken) { list.textContent = ''; say('Could not load your chats. Try again.', true); } });
  }

  search.addEventListener('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 200);
  });

  // --- opening the sheet -----------------------------------------------------------------
  document.addEventListener('click', function (event) {
    var item = event.target.closest ? event.target.closest('.share-chat-item') : null;
    if (!item) return;
    event.preventDefault();
    current = { url: item.getAttribute('data-share-url') };
    search.value = '';
    note.value = '';
    say('');
    list.textContent = '';
    $('#shareChatModal').modal('show');
    load();
    if (!chat) startChat().ready.catch(function () { /* said when someone presses Send */ });
  });
})();
