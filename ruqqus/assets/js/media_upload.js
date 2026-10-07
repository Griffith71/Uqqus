/*
 * Uploads to a member's own linked storage (routes/media.py, helpers/media).
 *
 * The file never passes through this site: the server answers where to send it
 * (POST /api/media/uploads), the browser sends the bytes there, and the server then
 * checks the file (POST /api/media/uploads/<id>/complete) and answers with the address
 * to put in the post.
 *
 * Wires up, wherever they are on the page:
 *   - the post editor's image button (PostEditor.register, assets/js/post_editor.js);
 *   - any button  <button data-media-upload="image" data-target="<textarea id>">  (comments);
 *   - any input   <input type="file" data-media-main="<id of the hidden `media` field>">
 *     (the picture of a post, on Create a post and in the feed composer).
 *
 * Pictures are re-drawn before upload, which drops what a camera writes into the file
 * (location, device). The server did that for the old uploads.
 */
(function () {
  'use strict';

  var ACCEPT = { image: 'image/jpeg,image/png,image/gif,image/webp', audio: 'audio/*', video: 'video/*' };
  var REDRAW = /^image\/(jpeg|png|webp)$/;
  var statusPromise = null;

  function formkeyValue() { return typeof formkey === 'function' ? formkey() : ''; }

  // { offered: the site has linked storage, kinds: what this member can upload now, limits, settings }
  function status(fresh) {
    if (!statusPromise || fresh) {
      statusPromise = fetch('/api/media/status', { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : { offered: false, kinds: [] }; })
        .catch(function () { return { offered: false, kinds: [] }; });
    }
    return statusPromise;
  }
  // linking storage happens in another tab: look again when the member comes back
  window.addEventListener('focus', function () { statusPromise = null; });

  function fail(message, extra) {
    var error = new Error(message);
    if (extra) Object.keys(extra).forEach(function (k) { error[k] = extra[k]; });
    return error;
  }

  // Draw the picture again so nothing but the pixels is uploaded.
  function prepare(file, kind) {
    if (kind !== 'image' || !REDRAW.test(file.type)) return Promise.resolve(file);
    if (typeof createImageBitmap !== 'function') {
      return Promise.reject(fail('This browser cannot prepare pictures for upload. Try another browser.'));
    }
    return createImageBitmap(file, { imageOrientation: 'from-image' }).then(function (bitmap) {
      var canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext('2d').drawImage(bitmap, 0, 0);
      if (bitmap.close) bitmap.close();
      return new Promise(function (resolve, reject) {
        canvas.toBlob(function (blob) {
          if (!blob) { reject(new Error('empty')); return; }
          resolve(new File([blob], file.name, { type: file.type }));
        }, file.type, 0.92);
      });
    }).catch(function () {
      // never fall back to the original: it may carry the location it was taken at
      throw fail('That picture could not be prepared for upload. It may be damaged or too large.');
    });
  }

  function post(url, fields) {
    var data = new FormData();
    data.append('formkey', formkeyValue());
    Object.keys(fields || {}).forEach(function (k) { data.append(k, fields[k]); });
    return fetch(url, { method: 'POST', body: data, credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (r.ok) return body;
        throw fail(body.error || 'The upload could not be started. Try again.', { need: body.need, settings: body.settings, code: r.status });
      });
    });
  }

  function send(upload, file, onProgress, handle) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(upload.method || 'PUT', upload.url, true);
      // our own address (the test storage) needs the session; a provider's address must never get it
      xhr.withCredentials = upload.url.charAt(0) === '/';
      Object.keys(upload.headers || {}).forEach(function (k) { xhr.setRequestHeader(k, upload.headers[k]); });
      xhr.upload.onprogress = function (e) { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else reject(fail(xhr.status === 413 ? 'That file is too big.' : 'The upload did not go through. Try again.'));
      };
      xhr.onerror = function () { reject(fail('The upload was interrupted. Check your connection and try again.')); };
      xhr.onabort = function () { reject(fail('Upload cancelled.', { cancelled: true })); };
      if (handle) handle.cancel = function () { xhr.abort(); };
      xhr.send(file);
    });
  }

  // Upload one file; resolves with the asset ({ id, kind, path, markdown, ... }).
  function upload(file, kind, options) {
    options = options || {};
    return prepare(file, kind).then(function (ready) {
      return post('/api/media/uploads', { kind: kind, filename: ready.name, size: ready.size }).then(function (begun) {
        return send(begun.upload, ready, options.onProgress, options.handle).then(function () {
          return post('/api/media/uploads/' + begun.id + '/complete');
        });
      });
    });
  }

  function choose(kind) {
    return new Promise(function (resolve) {
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = ACCEPT[kind] || '';
      input.addEventListener('change', function () { resolve(input.files && input.files[0] ? input.files[0] : null); });
      input.click();
    });
  }

  // --- the line under an editor that says what is happening ------------------------

  function statusNode(anchor) {
    var host = (anchor.closest && anchor.closest('.comment-write, .ic-main, #image-upload-block')) || anchor.parentNode;
    var node = host.querySelector(':scope > .media-upload-status');
    if (!node) {
      node = document.createElement('div');
      node.className = 'media-upload-status text-small mt-1';
      node.setAttribute('role', 'status');
      host.appendChild(node);
    }
    return node;
  }

  function say(node, text, isError) {
    node.textContent = text || '';
    node.classList.toggle('text-danger', !!isError);
    node.classList.toggle('text-muted', !isError);
  }

  function askForStorage(node, kind) {
    node.textContent = '';
    node.classList.remove('text-danger');
    node.classList.add('text-muted');
    node.appendChild(document.createTextNode('To add ' + (kind === 'image' ? 'pictures' : kind) + ', turn on media storage once: '));
    var link = document.createElement('a');
    link.href = '/settings/media';
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Enable media storage';
    node.appendChild(link);
  }

  // Upload `file`, reporting in `node`. Resolves with the asset, or null when nothing was added.
  function run(file, kind, node) {
    var handle = {};
    var cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-link btn-sm p-0 ml-2';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', function () { if (handle.cancel) handle.cancel(); });
    say(node, 'Preparing…');
    return upload(file, kind, {
      handle: handle,
      onProgress: function (part) {
        say(node, 'Uploading ' + Math.round(part * 100) + '%');
        node.appendChild(cancel);
        if (part >= 1) say(node, 'Checking…');
      }
    }).then(function (asset) {
      say(node, '');
      return asset;
    }, function (error) {
      if (error.need === 'storage') askForStorage(node, kind);
      else say(node, error.cancelled ? '' : error.message, !error.cancelled);
      return null;
    });
  }

  function pickInto(kind, anchor, insert) {
    var node = statusNode(anchor);
    return status().then(function (s) {
      if (s.kinds.indexOf(kind) === -1) { askForStorage(node, kind); return null; }
      return choose(kind).then(function (file) {
        if (!file) return null;
        return run(file, kind, node).then(function (asset) {
          if (asset && asset.markdown) insert(asset.markdown);
          return asset;
        });
      });
    });
  }

  function insertAtCaret(textarea, text) {
    var block = (textarea.value && !/\n$/.test(textarea.value.slice(0, textarea.selectionStart)) ? '\n\n' : '') + text + '\n';
    if (typeof editorReplace === 'function') editorReplace(textarea, textarea.selectionStart, textarea.selectionEnd, block);
    else { textarea.value += block; textarea.dispatchEvent(new Event('input', { bubbles: true })); }
  }

  // --- wiring ------------------------------------------------------------------------

  // 1. the post editor's image button
  status().then(function (s) {
    if (!s.offered || !window.PostEditor) return;
    window.PostEditor.register('image', function (ctx) {
      pickInto('image', ctx.textarea, function (markdown) { insertAtCaret(ctx.textarea, markdown); });
    });
  });

  // 2. image buttons in comment forms
  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-media-upload]') : null;
    if (!button) return;
    event.preventDefault();
    var textarea = document.getElementById(button.getAttribute('data-target'));
    if (!textarea) return;
    pickInto(button.getAttribute('data-media-upload'), textarea, function (markdown) { insertAtCaret(textarea, markdown); });
  });

  // The page's own preview of the post's picture (data-media-preview / data-media-label on the
  // input): shown from the stored file once the upload is really there, cleared otherwise.
  function showPreview(input, asset, name) {
    var image = document.getElementById(input.getAttribute('data-media-preview') || '');
    var label = document.getElementById(input.getAttribute('data-media-label') || '');
    if (image) { if (asset) image.src = asset.path; else image.removeAttribute('src'); }
    if (label) {
      if (!label.hasAttribute('data-idle-text')) label.setAttribute('data-idle-text', label.textContent);
      label.textContent = asset ? name : label.getAttribute('data-idle-text');
    }
  }

  // 3. the picture of a post: the file goes to the member's storage and the form only carries its id
  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('input[type="file"][data-media-main]')) return;
    var field = document.getElementById(input.getAttribute('data-media-main'));
    var form = input.form;
    var file = input.files && input.files[0];
    if (!field || !file) return;
    var node = statusNode(input);

    status().then(function (s) {
      if (!s.offered) return;                       // this site has no linked storage: the old upload stays
      // from here the browser must not also send the file to this site
      var kept = file;
      input.value = '';
      field.value = '';
      showPreview(input, null);
      if (s.kinds.indexOf('image') === -1) { askForStorage(node, 'image'); return; }

      var buttons = form ? form.querySelectorAll('button[type="submit"], input[type="submit"], #create_button') : [];
      form && form.setAttribute('data-media-busy', '1');
      Array.prototype.forEach.call(buttons, function (b) { b.setAttribute('data-media-was-disabled', b.disabled ? '1' : '0'); b.disabled = true; });
      run(kept, 'image', node).then(function (asset) {
        form && form.removeAttribute('data-media-busy');
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = b.getAttribute('data-media-was-disabled') === '1'; });
        if (!asset) return;
        field.value = asset.id;
        showPreview(input, asset, kept.name);
        say(node, 'Picture added: ' + kept.name);
        var remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-link btn-sm p-0 ml-2';
        remove.textContent = 'Remove';
        remove.addEventListener('click', function () {
          field.value = '';
          showPreview(input, null);
          say(node, '');
          field.dispatchEvent(new Event('change', { bubbles: true }));
        });
        node.appendChild(remove);
        field.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
  });

  // a form whose picture is still uploading is not sent yet
  document.addEventListener('submit', function (event) {
    if (event.target.getAttribute && event.target.getAttribute('data-media-busy')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  window.MediaUpload = { status: status, upload: upload, version: 1 };
})();
