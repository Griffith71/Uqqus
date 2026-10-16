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
 *
 * Connecting happens where the member is: a file is picked first (the click allows the file
 * dialog), and when no linked account takes that kind of file yet, the status line offers the
 * services this site has ("Google Drive, 15 GB free in your Google Drive"). The chosen one opens
 * in a popup; when the popup says it is done (routes/media.py `_finish`, media_connected.html)
 * the upload carries on with the same file. Nothing reloads and nothing written is lost.
 *
 * How bytes travel depends on the service, as the server's answer says:
 *   - one PUT of the whole file (Google Drive, YouTube, the test storage);
 *   - `chunk`: PUTs of that many bytes with a Content-Range each (OneDrive's upload session);
 *   - `form_field` + `answer`: one form POST whose answer the server needs back (Dailymotion).
 */
(function () {
  'use strict';

  var ACCEPT = { image: 'image/jpeg,image/png,image/gif,image/webp', audio: 'audio/mpeg,audio/mp4,audio/ogg,audio/wav,audio/flac,audio/webm,.mp3,.m4a,.ogg,.wav,.flac,.webm', video: 'video/*' };
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
        throw fail(body.error || 'The upload could not be started. Try again.',
                   { need: body.need, settings: body.settings, code: r.status, restricted: body.restricted });
      });
    });
  }

  function newXhr(upload, method, url, handle) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, url, true);
    // our own address (the test storage) needs the session; a provider's address must never get it
    xhr.withCredentials = upload.url.charAt(0) === '/';
    Object.keys(upload.headers || {}).forEach(function (k) { xhr.setRequestHeader(k, upload.headers[k]); });
    if (handle) handle.cancel = function () { xhr.abort(); };
    return xhr;
  }

  function sendError(xhr) {
    return fail(xhr.status === 413 ? 'That file is too big.' : 'The upload did not go through. Try again.');
  }

  // One request with the whole file; resolves with nothing (the server checks what arrived).
  function sendWhole(upload, file, onProgress, handle) {
    return new Promise(function (resolve, reject) {
      var xhr = newXhr(upload, upload.method || 'PUT', upload.url, handle);
      xhr.upload.onprogress = function (e) { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = function () { if (xhr.status >= 200 && xhr.status < 300) resolve(); else reject(sendError(xhr)); };
      xhr.onerror = function () { reject(fail('The upload was interrupted. Check your connection and try again.')); };
      xhr.onabort = function () { reject(fail('Upload cancelled.', { cancelled: true })); };
      xhr.send(file);
    });
  }

  // OneDrive's upload session: pieces of `upload.chunk` bytes, each saying where it goes.
  function sendChunks(upload, file, onProgress, handle) {
    var size = file.size;
    var step = Math.max(Number(upload.chunk) || size, 1);
    function piece(start) {
      return new Promise(function (resolve, reject) {
        var end = Math.min(start + step, size);
        var xhr = newXhr(upload, upload.method || 'PUT', upload.url, handle);
        xhr.setRequestHeader('Content-Range', 'bytes ' + start + '-' + (end - 1) + '/' + size);
        xhr.upload.onprogress = function (e) { if (e.lengthComputable && onProgress) onProgress((start + e.loaded) / size); };
        xhr.onload = function () { if (xhr.status >= 200 && xhr.status < 300) resolve(end); else reject(sendError(xhr)); };
        xhr.onerror = function () { reject(fail('The upload was interrupted. Check your connection and try again.')); };
        xhr.onabort = function () { reject(fail('Upload cancelled.', { cancelled: true })); };
        xhr.send(file.slice(start, end));
      });
    }
    function from(start) { return start >= size ? Promise.resolve() : piece(start).then(from); }
    return from(0);
  }

  // Dailymotion: the file as a form; its answer names the uploaded file, which the server needs.
  function sendForm(upload, file, onProgress, handle) {
    return new Promise(function (resolve, reject) {
      var xhr = newXhr(upload, upload.method || 'POST', upload.url, handle);
      var form = new FormData();
      form.append(upload.form_field, file, file.name);
      xhr.upload.onprogress = function (e) { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
      xhr.onload = function () {
        if (xhr.status < 200 || xhr.status >= 300) { reject(sendError(xhr)); return; }
        var answer = null;
        try { answer = JSON.parse(xhr.responseText || '{}').url || null; } catch (err) { answer = null; }
        if (typeof answer === 'string' && /^https:\/\//.test(answer)) resolve(answer);
        else reject(fail('The video service did not say where the upload went. Try again.'));
      };
      xhr.onerror = function () { reject(fail('The upload was interrupted. Check your connection and try again.')); };
      xhr.onabort = function () { reject(fail('Upload cancelled.', { cancelled: true })); };
      xhr.send(form);
    });
  }

  function send(upload, file, onProgress, handle) {
    if (upload.form_field) return sendForm(upload, file, onProgress, handle);
    if (upload.chunk) return sendChunks(upload, file, onProgress, handle);
    return sendWhole(upload, file, onProgress, handle);
  }

  // Upload one file; resolves with the asset ({ id, kind, path, markdown, ... } and `to`, where it went).
  function upload(file, kind, options) {
    options = options || {};
    return prepare(file, kind).then(function (ready) {
      var fields = { kind: kind, filename: ready.name, size: ready.size };
      Object.keys(options.fields || {}).forEach(function (k) { fields[k] = options.fields[k]; });
      return post('/api/media/uploads', fields).then(function (begun) {
        var finish = function (answer) {
          return post('/api/media/uploads/' + begun.id + '/complete', answer ? { answer: answer } : {}).then(function (asset) {
            asset.to = begun.to;
            return asset;
          });
        };
        return send(begun.upload, ready, options.onProgress, options.handle).then(finish, function (sendError) {
          if (sendError.cancelled || begun.upload.answer) throw sendError;
          // The bytes may have arrived even though the browser was not allowed to read the
          // provider's answer: the server can tell. If they did not, say what went wrong sending.
          return finish().catch(function () { throw sendError; });
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

  var WHAT = { image: 'pictures', audio: 'audio', video: 'videos' };

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function quiet(node) {
    node.textContent = '';
    node.classList.remove('text-danger');
    node.classList.add('text-muted');
  }

  function cancelButton(handle, label) {
    var button = make('button', 'btn btn-link btn-sm p-0 ml-2', label || 'Cancel');
    button.type = 'button';
    button.addEventListener('click', function () { if (handle.cancel) handle.cancel(); });
    return button;
  }

  // When nothing else works (a blocked popup): the settings page, in a new tab.
  function settingsLink(node, message, href) {
    quiet(node);
    node.appendChild(document.createTextNode(message + ' '));
    var link = make('a', '', 'Open media settings');
    link.href = /^\/settings\/media/.test(href || '') ? href : '/settings/media';
    link.target = '_blank';
    link.rel = 'noopener';
    node.appendChild(link);
  }

  // --- linking an account without leaving the page ------------------------------------------

  function makeNonce() {
    var bytes = new Uint8Array(12);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }

  // Link `service` for `kind` in a popup. Resolves with the fresh status once it is linked.
  // The popup's page tells this one on three channels (media_connected.html); only a message
  // from this origin carrying our own `n` counts. A sign-in page in between may cut the popup off
  // from this page, so coming back to this tab also asks the server whether it is linked now.
  function connect(service, kind, handle) {
    if (service.method === 'post') {        // the test storage: no other site involved
      return post(service.connect + '?json=1', {}).then(function () { return status(true); });
    }
    return new Promise(function (resolve, reject) {
      var nonce = makeNonce();
      var url = service.connect + '?' + (service.name === 'google' && kind === 'video' ? 'want=video&' : '') + 'popup=1&n=' + nonce;
      var popup = window.open(url, 'ruqqus-media-connect', 'width=520,height=700');
      if (!popup) {
        reject(fail('Your browser blocked the window for connecting. Allow pop-ups for this site, or connect in the settings.',
                    { blocked: true, settings: service.connect }));
        return;
      }
      var done = false;
      var channel = null;
      function cleanup() {
        window.removeEventListener('message', onMessage);
        window.removeEventListener('storage', onStorage);
        window.removeEventListener('focus', onFocus);
        if (channel) channel.close();
      }
      function settle(ok, value) {
        if (done) return;
        done = true;
        cleanup();
        if (ok) resolve(value); else reject(value);
      }
      function take(message) {
        if (done || !message || message.type !== 'ruqqus-media-connected' || message.n !== nonce) return;
        if (message.ok) status(true).then(function (s) { settle(true, s); });
        else settle(false, fail(message.message || 'Nothing was connected.'));
      }
      function onMessage(event) { if (event.origin === window.location.origin) take(event.data); }
      function onStorage(event) {
        if (event.key !== 'ruqqus-media-connect' || !event.newValue) return;
        try { take(JSON.parse(event.newValue).message); } catch (err) { /* not ours */ }
        try { localStorage.removeItem('ruqqus-media-connect'); } catch (err) { /* storage off */ }
      }
      function onFocus() {
        status(true).then(function (s) {
          var now = (s.services || []).filter(function (x) { return x.name === service.name; })[0];
          if (now && now.linked && s.kinds.indexOf(kind) !== -1) settle(true, s);
        });
      }
      try {
        channel = new BroadcastChannel('ruqqus-media-connect');
        channel.onmessage = function (event) { take(event.data); };
      } catch (err) { channel = null; }
      window.addEventListener('message', onMessage);
      window.addEventListener('storage', onStorage);
      window.addEventListener('focus', onFocus);
      if (handle) handle.cancel = function () { settle(false, fail('', { cancelled: true })); };
    });
  }

  // Offer the services that take this kind of file. Resolves with the one picked, or null.
  function chooser(node, kind, s, intro, exclude) {
    return new Promise(function (resolve) {
      quiet(node);
      var box = make('div', 'media-chooser');
      box.appendChild(make('div', 'media-chooser-title', intro || ('Where should your ' + WHAT[kind] + ' go?')));
      var offered = (s.services || []).filter(function (x) { return x.kinds.indexOf(kind) !== -1 && x.name !== exclude; });
      if (!offered.length) box.appendChild(make('div', 'media-chooser-hint', 'There is nowhere else to send them on this site yet.'));
      offered.forEach(function (service) {
        var button = make('button', 'btn btn-secondary btn-sm media-chooser-option');
        button.type = 'button';
        button.appendChild(make('span', 'media-chooser-name', service.names[kind] || service.label));
        var hint = service.linked ? 'Connected' : (service.expired ? 'Connect again' : (service.hints[kind] || ''));
        if (hint) button.appendChild(make('span', 'media-chooser-hint', hint));
        button.addEventListener('click', function () { resolve(service); });
        box.appendChild(button);
      });
      var no = make('button', 'btn btn-link btn-sm', 'Cancel');
      no.type = 'button';
      no.addEventListener('click', function () { quiet(node); resolve(null); });
      box.appendChild(no);
      if (!offered.some(function (x) { return x.linked; })) {
        box.appendChild(make('div', 'media-chooser-note',
          'Your files stay in your own account there, using its free space. This site only shows them in your posts.'));
      }
      node.appendChild(box);
    });
  }

  // Link `service` from the status line. Resolves with the fresh status, or null.
  function linkWith(node, service, kind) {
    var handle = {};
    quiet(node);
    node.appendChild(document.createTextNode('Connecting ' + (service.names[kind] || service.label) + '. Finish in the window that opened.'));
    node.appendChild(cancelButton(handle));
    return connect(service, kind, handle).then(function (s) { quiet(node); return s; }, function (error) {
      if (error.cancelled) { quiet(node); return null; }
      if (error.blocked) { settingsLink(node, error.message, error.settings); return null; }
      say(node, error.message, true);
      return null;
    });
  }

  // From now on this kind of file goes to `service` (already linked).
  function useService(kind, service) {
    return post('/api/media/default', { kind: kind, service: service.name }).then(function () { return status(true); });
  }

  // Make sure a linked account takes this kind of file. Resolves with the status, or null when
  // the member gave up. `exclude` (a service) asks for another one (that one is full).
  function ensureAccount(kind, node, intro, exclude) {
    return status().then(function (s) {
      if (s.kinds.indexOf(kind) !== -1 && !exclude) return s;
      return chooser(node, kind, s, intro, exclude).then(function (service) {
        if (!service) return null;
        if (service.linked) return useService(kind, service);
        return linkWith(node, service, kind).then(function (fresh) {
          // linked to replace a full or lost one: send this kind there from now on
          return fresh && exclude ? useService(kind, service) : fresh;
        });
      });
    });
  }

  // "Added to your Google Drive. Change": where it went, and a way to send the next ones elsewhere.
  function doneLine(node, kind, asset, text) {
    say(node, text);
    status().then(function (s) {
      var able = (s.services || []).filter(function (x) { return x.kinds.indexOf(kind) !== -1; });
      if (able.length < 2) return;
      var change = make('button', 'btn btn-link btn-sm p-0 ml-2', 'Change where ' + WHAT[kind] + ' go');
      change.type = 'button';
      change.addEventListener('click', function () {
        var current = asset && asset.to ? asset.to.service : null;
        chooser(node, kind, s, 'Send your next ' + WHAT[kind] + ' to:', current).then(function (service) {
          if (!service) return;
          var linked = service.linked ? Promise.resolve(true) : linkWith(node, service, kind);
          linked.then(function (ok) {
            if (!ok) return;
            useService(kind, service).then(function () {
              say(node, 'From now on your ' + WHAT[kind] + ' go to your ' + (service.names[kind] || service.label) + '.');
            }, function (error) { say(node, error.message, true); });
          });
        });
      });
      node.appendChild(change);
    });
  }

  // Upload `file`, reporting in `node`. Resolves with the asset, or null when nothing was added.
  // An account that is full, lost or missing a permission is fixed in place, then tried again once.
  function run(file, kind, node, fields, retried) {
    var handle = {};
    var cancel = cancelButton(handle);
    say(node, 'Preparing…');
    return upload(file, kind, {
      handle: handle,
      fields: fields,
      onProgress: function (part) {
        say(node, 'Uploading ' + Math.round(part * 100) + '%');
        node.appendChild(cancel);
        if (part >= 1) say(node, 'Checking…');
      }
    }).then(function (asset) {
      say(node, '');
      return asset;
    }, function (error) {
      if (error.cancelled) { say(node, ''); return null; }
      var again = function (s) { return s && !retried ? run(file, kind, node, fields, true) : null; };
      if (error.need === 'storage') {
        return ensureAccount(kind, node, 'Your storage needs connecting again. Send your ' + WHAT[kind] + ' to:').then(again);
      }
      if (error.need === 'video') {
        // a Google account without YouTube access yet: ask for it, then send the same video
        return status().then(function (s) {
          var google = (s.services || []).filter(function (x) { return x.name === 'google'; })[0];
          if (!google) { say(node, error.message, true); return null; }
          return linkWith(node, google, 'video').then(again);
        });
      }
      if (error.code === 507) {
        return status().then(function (s) {
          var full = s.using && s.using[kind] ? s.using[kind].service : null;
          return ensureAccount(kind, node, error.message + ' Send it somewhere else:', full).then(again);
        });
      }
      say(node, error.message, true);
      return null;
    });
  }

  function pickInto(kind, anchor, insert) {
    var node = statusNode(anchor);
    // the file first: the click that got here is what lets the browser open its file dialog
    return choose(kind).then(function (file) {
      if (!file) return null;
      return ensureAccount(kind, node).then(function (s) {
        if (!s) return null;
        return run(file, kind, node).then(function (asset) {
          if (asset && asset.markdown) {
            insert(asset.markdown);
            var where = asset.to && asset.to.label ? ' to your ' + asset.to.label : '';
            doneLine(node, kind, asset, (kind === 'audio' ? 'Sound' : 'Picture') + ' added' + where + '.');
          }
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
    ['image', 'audio'].forEach(function (kind) {
      window.PostEditor.register(kind, function (ctx) {
        pickInto(kind, ctx.textarea, function (markdown) { insertAtCaret(ctx.textarea, markdown); });
      });
    });
    if (s.video) window.PostEditor.register('video', function (ctx) { pickVideo(ctx.textarea); });
  });

  // Video: it goes to the member's own channel on a video site and the post links to it.
  // Before sending, the member chooses how it shows on their channel.
  function chooseVisibility(node, file, preset, site) {
    return new Promise(function (resolve) {
      quiet(node);
      node.appendChild(document.createTextNode('Upload "' + file.name + '" to your ' + site + ' channel as '));
      var select = make('select', 'custom-select custom-select-sm d-inline-block w-auto mx-1');
      select.setAttribute('aria-label', 'Who can find the video on ' + site);
      [['unlisted', 'Unlisted (only here and by link)'], ['public', 'Public (on your channel)']].forEach(function (o) {
        var option = make('option', '', o[1]);
        option.value = o[0];
        if (o[0] === preset) option.selected = true;
        select.appendChild(option);
      });
      node.appendChild(select);
      var go = make('button', 'btn btn-primary btn-sm ml-1', 'Upload');
      go.type = 'button';
      go.addEventListener('click', function () { resolve(select.value); });
      var no = make('button', 'btn btn-link btn-sm', 'Cancel');
      no.type = 'button';
      no.addEventListener('click', function () { say(node, ''); resolve(null); });
      node.appendChild(go);
      node.appendChild(no);
    });
  }

  // Put the video's address where this form keeps the post's link.
  function attachLink(textarea, link) {
    var form = textarea.form || textarea.closest('form');
    var hidden = document.getElementById('post-URL');
    if (hidden && (!form || hidden.form === form)) {           // Create a post
      hidden.value = link;
      if (typeof showAttachedLinkChip === 'function') showAttachedLinkChip(link);
      if (typeof hide_image === 'function') hide_image();
      return;
    }
    var field = form ? form.querySelector('input[name="url"]') : null;
    if (field) { field.value = link; field.dispatchEvent(new Event('input', { bubbles: true })); return; }
    insertAtCaret(textarea, link);
  }

  function pickVideo(textarea) {
    var node = statusNode(textarea);
    choose('video').then(function (file) {
      if (!file) return;
      ensureAccount('video', node, 'Where should your videos go?').then(function (s) {
        if (!s) return;
        var to = (s.using && s.using.video) || {};
        var site = to.label || 'video';
        var preset = (s.video_visibility_by || {})[to.service] || s.video_visibility;
        chooseVisibility(node, file, preset, site).then(function (visibility) {
          if (!visibility) return;
          var form = textarea.form || textarea.closest('form');
          var titleField = form ? form.querySelector('[name="title"]') : null;
          run(file, 'video', node, { title: (titleField && titleField.value.trim()) || file.name, visibility: visibility })
            .then(function (asset) {
              if (!asset || !asset.link) return;
              attachLink(textarea, asset.link);
              var where = asset.to && asset.to.label ? asset.to.label : site;
              doneLine(node, 'video', asset, 'Video added. ' + where + ' may take a few minutes before it plays.');
            });
        });
      });
    });
  }

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
      ensureAccount('image', node).then(function (ready) { if (ready) attachMain(kept); });
    });

    function attachMain(kept) {
      var buttons = form ? form.querySelectorAll('button[type="submit"], input[type="submit"], #create_button') : [];
      form && form.setAttribute('data-media-busy', '1');
      Array.prototype.forEach.call(buttons, function (b) { b.setAttribute('data-media-was-disabled', b.disabled ? '1' : '0'); b.disabled = true; });
      run(kept, 'image', node).then(function (asset) {
        form && form.removeAttribute('data-media-busy');
        Array.prototype.forEach.call(buttons, function (b) { b.disabled = b.getAttribute('data-media-was-disabled') === '1'; });
        if (!asset) return;
        field.value = asset.id;
        showPreview(input, asset, kept.name);
        say(node, 'Picture added: ' + kept.name + (asset.to && asset.to.label ? ' (in your ' + asset.to.label + ')' : ''));
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
    }
  });

  // a form whose picture is still uploading is not sent yet
  document.addEventListener('submit', function (event) {
    if (event.target.getAttribute && event.target.getAttribute('data-media-busy')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  window.MediaUpload = { status: status, upload: upload, connect: connect, version: 2 };
})();
