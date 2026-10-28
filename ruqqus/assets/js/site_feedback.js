/*
 * "Send feedback" (templates/partials/site_feedback_modal.html, POST /api/feedback, helpers/site_feedback.py). Any link
 * marked data-feedback-open opens the sheet. The page sends along its path, light or dark, the density, the window's
 * size and the page's language; the request carries the browser's own text, and nothing else is read.
 * A screenshot (picked, or pasted into the message box) is drawn again on a canvas before it leaves the browser:
 * smaller, and nothing but the pixels (no location from a camera's EXIF). It never falls back to the original. The
 * server checks and encodes it again (helpers/feedback_image.py). Messages go in as text.
 */
(function () {
  'use strict';

  var MAX_EDGE = 1600;
  var MAX_FILE = 25 * 1024 * 1024;           // before it is shrunk; the server's limit applies to what is sent
  var TYPES = /^image\/(png|jpeg|webp)$/;

  var modal = document.getElementById('siteFeedbackModal');
  if (!modal) return;
  var form = document.getElementById('site-feedback-form');
  var message = document.getElementById('site-feedback-message');
  var count = document.getElementById('site-feedback-count');
  var file = document.getElementById('site-feedback-file');
  var pick = document.getElementById('site-feedback-pick');
  var preview = document.getElementById('site-feedback-preview');
  var thumb = document.getElementById('site-feedback-thumb');
  var remove = document.getElementById('site-feedback-remove');
  var status = document.getElementById('site-feedback-status');
  var send = document.getElementById('site-feedback-send');
  var shot = null;                            // the redrawn screenshot (a Blob), or null
  var shotUrl = '';

  function toast(text, ok) {
    var id = ok ? 'toast-post-success' : 'toast-post-error';
    var holder = document.getElementById(id + '-text');
    if (!holder) return;
    holder.innerText = text;
    $('#' + id).toast('dispose');
    $('#' + id).toast('show');
  }

  function updateCount() { count.textContent = message.value.length + ' / ' + message.maxLength; }

  function clearShot() {
    shot = null;
    file.value = '';
    if (shotUrl) { URL.revokeObjectURL(shotUrl); shotUrl = ''; }
    thumb.removeAttribute('src');
    preview.hidden = true;
  }

  function reset() {
    form.reset();
    clearShot();
    updateCount();
    status.textContent = '';
    send.disabled = false;
  }

  // Draw the picture again, at most MAX_EDGE on its longer side, on white (a transparent corner would turn black).
  function redraw(picture) {
    if (typeof createImageBitmap !== 'function') {
      return Promise.reject(new Error(tr('This browser cannot prepare pictures for upload. Try another browser.')));
    }
    return createImageBitmap(picture, { imageOrientation: 'from-image' }).then(function (bitmap) {
      var scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
      var canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      var context = canvas.getContext('2d');
      context.fillStyle = '#fff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      if (bitmap.close) bitmap.close();
      return new Promise(function (resolve, reject) {
        canvas.toBlob(function (blob) { blob ? resolve(blob) : reject(new Error('empty')); }, 'image/jpeg', 0.85);
      });
    }).catch(function () {
      throw new Error(tr('That picture could not be prepared. It may be damaged or too large.'));
    });
  }

  function take(picture) {
    status.textContent = '';
    if (!picture || !TYPES.test(picture.type)) { status.textContent = tr('Choose a PNG, JPEG or WebP picture.'); return; }
    if (picture.size > MAX_FILE) { status.textContent = tr('That picture is too big.'); return; }
    redraw(picture).then(function (blob) {
      clearShot();
      shot = blob;
      shotUrl = URL.createObjectURL(blob);
      thumb.src = shotUrl;
      preview.hidden = false;
    }, function (error) { status.textContent = error.message; });
  }

  document.addEventListener('click', function (event) {
    var opener = event.target.closest ? event.target.closest('[data-feedback-open]') : null;
    if (!opener) return;
    event.preventDefault();
    $(modal).modal('show');
  });

  $(modal).on('shown.bs.modal', function () { message.focus(); });

  message.addEventListener('input', updateCount);
  message.addEventListener('paste', function (event) {
    var items = (event.clipboardData && event.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === 'file' && TYPES.test(items[i].type)) {
        event.preventDefault();
        take(items[i].getAsFile());
        return;
      }
    }
  });
  pick.addEventListener('click', function () { file.click(); });
  file.addEventListener('change', function () { if (file.files && file.files[0]) take(file.files[0]); });
  remove.addEventListener('click', function () { clearShot(); });

  function context() {
    return JSON.stringify({
      path: window.location.pathname,
      theme: typeof current_theme === 'function' ? current_theme() : '',
      density: document.documentElement.getAttribute('data-density') || '',
      width: window.innerWidth,
      height: window.innerHeight,
      lang: document.documentElement.lang || ''
    });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var text = message.value.trim();
    if (!text) { status.textContent = tr('Write what you want to tell us.'); message.focus(); return; }
    var kind = form.querySelector('input[name="kind"]:checked');
    var body = new FormData();
    body.append('formkey', formkey());
    body.append('kind', kind ? kind.value : 'other');
    body.append('message', text);
    body.append('context', context());
    if (shot) body.append('image', shot, 'screenshot.jpg');
    send.disabled = true;
    status.textContent = tr('Sending...');
    fetch('/api/feedback', { method: 'POST', credentials: 'same-origin', body: body })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (!res.ok) { send.disabled = false; status.textContent = res.data.error || tr('Could not send your feedback. Try again.'); return; }
        $(modal).modal('hide');
        reset();
        toast(res.data.message, true);
      })
      .catch(function () { send.disabled = false; status.textContent = tr('Could not send your feedback. Try again.'); });
  });

  updateCount();
})();
