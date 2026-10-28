/*
 * Incoming calls on every page of a signed-in member (routes/calls.py, assets/js/live_events.js).
 *
 * A 1:1 call rings here (`call.ring`): Answer opens the call window, Decline tells the caller. A call someone started
 * in a group (`call.started`) offers Join. The toast goes when the call is answered in another tab or window
 * (`call.answered`), when the caller gives up (`call.cancelled`), or after the ring time. Every name goes in as text.
 */
(function () {
  if (!window.UqqusLive || window.UqqusCallRing) return;
  window.UqqusCallRing = true;

  var RING_MS = 45000;
  var shown = {};          // room id -> { el, timer }
  // the tab's title while a call rings, split around the page's own title (English: "(Call) " before it)
  // Translators: the browser tab's title while a call is ringing; %(title)s is the page's own title
  var RINGING = tr('(Call) %(title)s', {title: '\u0000'}).split('\u0000');

  function ringing(title) {
    return title.length >= RINGING[0].length + RINGING[1].length && title.startsWith(RINGING[0]) && title.endsWith(RINGING[1]);
  }

  function openCall(roomId, kind, answer) {
    var url = '/call/' + encodeURIComponent(roomId) + '?kind=' + (kind === 'video' ? 'video' : 'audio') + (answer ? '&answer=1' : '');
    var win = window.open(url, 'uqqus-call', 'popup,width=960,height=680');
    if (!win) window.location.href = url;        // a blocked popup: open it here instead
  }

  function hide(roomId) {
    var item = shown[roomId];
    if (!item) return;
    clearTimeout(item.timer);
    item.el.remove();
    delete shown[roomId];
    if (!Object.keys(shown).length && ringing(document.title)) {
      document.title = document.title.slice(RINGING[0].length, document.title.length - RINGING[1].length);
    }
  }

  function button(label, className, icon, onClick) {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-sm ' + className;
    var i = document.createElement('i');
    i.className = 'fas ' + icon + ' mr-1';
    i.setAttribute('aria-hidden', 'true');
    b.appendChild(i);
    b.appendChild(document.createTextNode(label));
    b.addEventListener('click', onClick);
    return b;
  }

  function show(data, group) {
    if (!data || typeof data.room_id !== 'string' || shown[data.room_id]) return;
    var box = document.createElement('div');
    box.className = 'call-ring shadow';
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-live', 'assertive');
    var who = document.createElement('div');
    who.className = 'call-ring-who';
    var img = document.createElement('img');
    img.alt = '';
    img.className = 'rounded-circle mr-2';
    img.width = img.height = 40;
    if (typeof data.avatar === 'string' && /^(https?:\/\/|\/)/.test(data.avatar)) img.src = data.avatar;
    var text = document.createElement('div');
    var name = document.createElement('div');
    name.className = 'font-weight-bold text-truncate';
    // Translators: shown in place of the caller's name when it is unknown
    name.textContent = data.from || tr('Someone');
    var what = document.createElement('div');
    what.className = 'small text-muted';
    var video = data.kind === 'video';
    // Translators: shown under the caller's name, on its own line
    if (group) what.textContent = video ? tr('started a video call in a group') : tr('started a voice call in a group');
    else what.textContent = video ? tr('Video call') : tr('Voice call');
    text.appendChild(name);
    text.appendChild(what);
    who.appendChild(img);
    who.appendChild(text);
    var actions = document.createElement('div');
    actions.className = 'call-ring-actions';
    if (group) {
      actions.appendChild(button(tr('Not now'), 'btn-secondary', 'fa-xmark', function () { hide(data.room_id); }));
      // Translators: a button that joins the group call that has just started
      actions.appendChild(button(tr('Join'), 'btn-primary', video ? 'fa-video' : 'fa-phone', function () {
        hide(data.room_id);
        openCall(data.room_id, data.kind, true);
      }));
    } else {
      actions.appendChild(button(tr('Decline'), 'btn-danger', 'fa-phone-slash', function () {
        hide(data.room_id);
        var body = new URLSearchParams();
        body.set('formkey', formkey());
        body.set('room', data.room_id);
        fetch('/api/calls/decline', { method: 'POST', credentials: 'same-origin', body: body }).catch(function () {});
      }));
      // Translators: a button that answers (picks up) the incoming call
      actions.appendChild(button(tr('Answer'), 'btn-success', video ? 'fa-video' : 'fa-phone', function () {
        hide(data.room_id);
        openCall(data.room_id, data.kind, true);
      }));
    }
    box.appendChild(who);
    box.appendChild(actions);
    document.body.appendChild(box);
    shown[data.room_id] = { el: box, timer: setTimeout(function () { hide(data.room_id); }, RING_MS) };
    if (!ringing(document.title)) document.title = tr('(Call) %(title)s', {title: document.title});
    actions.lastChild.focus({ preventScroll: true });
  }

  window.UqqusLive.on('call.ring', function (data) { show(data, false); });
  window.UqqusLive.on('call.started', function (data) { show(data, true); });
  window.UqqusLive.on('call.answered', function (data) { hide(data.room_id); });
  window.UqqusLive.on('call.cancelled', function (data) { hide(data.room_id); });

  window.UqqusCalls = { open: openCall };
})();
