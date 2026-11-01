/*
 * The Edit profile pop-up (templates/partials/profile_edit_modal.html), opened from the button and the "⋯" item on your
 * own profile (partials/profile_header.html, [data-profile-edit]). Everything is staged until Save: a picture chosen shows
 * at once here and is only sent with the rest, in ONE request to POST /settings/profile/edit (routes/profile_edit_pages.py),
 * which checks everything before it changes anything. The server compares each field with what is stored; the Save button
 * is on when something differs from what the pop-up opened with.
 *
 * - Without script, with a blocked Bootstrap or in a side-panel frame, the button stays a link to Settings > Profile.
 * - Closing with changes (Esc, the X, a tap outside) asks "Discard changes?" in a row of this pop-up: a sheet of the site's
 *   own (confirm_sheet.js) would close this one first.
 * - After a save the page reloads, so the header, the navbar and the menu all read the new profile; "Saved" is shown after.
 * - This file loads before jQuery: it touches $, tr and formkey only when something happens. Text from the server goes
 *   in with textContent.
 */
(function () {
  'use strict';

  var modal = document.getElementById('profileEditModal');
  if (!modal) return;
  // the profile header is its own stacking context: inside it the pop-up would sit under the page's backdrop
  if (modal.parentNode !== document.body) document.body.appendChild(modal);

  var form = document.getElementById('profile-edit-form');
  var saveButton = document.getElementById('profile-edit-save');
  var closeButton = document.getElementById('profile-edit-close');
  var statusLine = document.getElementById('profile-edit-status');
  var discardRow = document.getElementById('profile-edit-discard');
  var title = document.getElementById('profileEditTitle');

  var FIELDS = { display_name: 'pe-display-name', bio: 'pe-bio', profile_job: 'pe-job', profile_location: 'pe-location', profile_website: 'pe-website' };
  var TEXT = Object.keys(FIELDS);
  var KINDS = ['profile', 'banner'];
  var MAX_BYTES = 16 * 1024 * 1024;
  var SAVED_FLAG = 'profile_edit_saved';

  var baseline = {};                          // the text as the server has it
  var stored = {};                            // each picture as the server has it: { src, has }
  var staged = { profile: null, banner: null };     // a File chosen, not sent yet
  var previews = { profile: null, banner: null };   // object URLs of those
  var removed = { profile: false, banner: false };  // "Remove" pressed, not sent yet
  var busy = false;
  var leaving = false;                        // the pop-up may close (changes saved or thrown away)
  var reloadOnClose = false;                  // part of a save went through: the page behind is out of date
  var opener = null;
  var afterDiscard = null;                    // where to go once the changes are thrown away (the footer's link to Settings)
  var lastFocus = null;                       // the field the member was in, for when they choose Keep editing
  var SAVE_TIMEOUT = 180000;                  // two 16 MB pictures on a slow line

  function field(name) { return document.getElementById(FIELDS[name]); }
  function newlines(value) { return (value || '').replace(/\r\n?/g, '\n'); }
  function parts(kind) {
    var profile = kind === 'profile';
    return {
      root: document.getElementById(profile ? 'pe-avatar' : 'pe-banner'),
      img: document.getElementById(profile ? 'pe-avatar-img' : 'pe-banner-img'),
      none: profile ? document.getElementById('pe-avatar-none') : null,
      file: document.getElementById('pe-file-' + kind),
      remove: modal.querySelector('[data-pe-remove="' + kind + '"]')
    };
  }

  // --- what differs from what the pop-up opened with ------------------------------------------------------------------

  function dirty() {
    var changed = TEXT.some(function (name) { return newlines(field(name).value) !== baseline[name]; });
    return changed || KINDS.some(function (kind) { return staged[kind] || removed[kind]; });
  }

  function refresh() {
    saveButton.disabled = busy || !dirty();
    closeButton.disabled = busy;
    modal.classList.toggle('is-busy', busy);
    // what is being sent can't change under it: the page reloads after a save and would lose a keystroke typed meanwhile
    TEXT.forEach(function (name) { field(name).readOnly = busy; });
  }

  function say(text, isError) {
    statusLine.textContent = text || '';
    statusLine.classList.toggle('text-danger', !!isError);
    statusLine.classList.toggle('text-muted', !isError);
  }

  function setError(name, message) {
    var box = document.getElementById('pe-error-' + name);
    if (box) { box.textContent = message || ''; box.hidden = !message; }
    var input = FIELDS[name] ? field(name) : null;
    if (input) {
      if (message) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
    }
  }

  function clearErrors() {
    TEXT.concat(KINDS).forEach(function (name) { setError(name, ''); });
    say('');
  }

  // --- the two pictures ---------------------------------------------------------------------------------------------------

  function forget(kind) {
    if (previews[kind]) { URL.revokeObjectURL(previews[kind]); previews[kind] = null; }
    staged[kind] = null;
  }

  // the picture as the server has it
  function showStored(kind) {
    var p = parts(kind);
    forget(kind);
    removed[kind] = false;
    if (p.file) p.file.value = '';
    p.img.src = stored[kind].src;
    // an account with no avatar shows the site's default one; with no banner there is nothing to show
    p.img.hidden = kind === 'banner' && !stored[kind].has;
    if (p.none) p.none.hidden = true;
    if (p.remove) p.remove.hidden = !stored[kind].has;
  }

  function showStaged(kind, file) {
    var p = parts(kind);
    forget(kind);
    staged[kind] = file;
    removed[kind] = false;
    previews[kind] = URL.createObjectURL(file);
    p.img.src = previews[kind];
    p.img.hidden = false;
    if (p.none) p.none.hidden = true;
    if (p.remove) p.remove.hidden = false;
  }

  function showRemoved(kind) {
    var p = parts(kind);
    forget(kind);
    removed[kind] = true;
    p.img.hidden = true;
    if (p.none) p.none.hidden = false;
    if (p.remove) p.remove.hidden = true;
  }

  function pick(kind, file) {
    setError(kind, '');
    // some platforms give no type for a valid file: only one that says it is not a picture is turned away here (the server opens it anyway)
    if (file.type && !/^image\//.test(file.type)) { setError(kind, modal.getAttribute('data-not-picture') || ''); return; }
    if (file.size > MAX_BYTES) { setError(kind, modal.getAttribute('data-too-big') || ''); return; }
    showStaged(kind, file);
    refresh();
  }

  // --- opening, resetting, closing ----------------------------------------------------------------------------------------

  function weigh(input) {
    if (typeof window.updateWeightedLimit === 'function' && input.hasAttribute('data-weighted-max')) window.updateWeightedLimit(input);
  }

  function reset() {
    TEXT.forEach(function (name) { var input = field(name); input.value = baseline[name]; weigh(input); });
    KINDS.forEach(showStored);
    clearErrors();
    discardRow.hidden = true;
    discardRow.textContent = '';
    refresh();
  }

  function keepEditing() {
    discardRow.hidden = true;
    discardRow.textContent = '';
    afterDiscard = null;
    (lastFocus && document.contains(lastFocus) ? lastFocus : field('display_name')).focus();
  }

  function askToDiscard() {
    discardRow.textContent = '';
    var question = document.createElement('span');
    question.className = 'pe-discard-text';
    question.textContent = tr('Discard changes?');
    var keep = document.createElement('button');
    keep.type = 'button';
    keep.className = 'btn btn-secondary btn-sm';
    keep.textContent = tr('Keep editing');
    var drop = document.createElement('button');
    drop.type = 'button';
    drop.className = 'btn btn-danger btn-sm';
    drop.textContent = tr('Discard');
    keep.addEventListener('click', keepEditing);
    drop.addEventListener('click', function () {
      if (afterDiscard) { window.location.href = afterDiscard; return; }      // the footer's link: what was typed is let go with the page
      leaving = true;                   // onHidden puts the fields back once the fade is over
      window.jQuery(modal).modal('hide');
    });
    discardRow.appendChild(question);
    discardRow.appendChild(keep);
    discardRow.appendChild(drop);
    discardRow.hidden = false;
    keep.focus();
  }

  function onHide(event) {
    if (busy) { event.preventDefault(); return; }
    if (leaving) return;
    if (!discardRow.hidden) { event.preventDefault(); keepEditing(); return; }       // Esc while it is asked: keep editing
    if (!dirty()) return;
    event.preventDefault();             // Esc, the X and a tap outside all arrive here
    askToDiscard();
  }

  function onShown() {
    // a keyboard coming up over a phone's sheet would hide the form: only a fine pointer starts in the first field
    if (window.matchMedia && window.matchMedia('(pointer: fine)').matches) field('display_name').focus();
    else if (title) title.focus();
  }

  function onHidden() {
    leaving = false;
    afterDiscard = null;
    reset();
    if (reloadOnClose) { window.location.reload(); return; }
    if (opener && document.contains(opener) && opener.focus) opener.focus();
  }

  document.addEventListener('click', function (event) {
    var target = event.target && event.target.closest ? event.target : null;
    if (!target) return;

    var link = target.closest('[data-profile-edit]');
    if (link) {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      // no Bootstrap (blocked, or not loaded yet): the link goes to Settings > Profile
      if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.modal) return;
      event.preventDefault();
      // the "⋯" menu closes as the pop-up opens: the focus goes back to its button
      opener = link.closest('.dropdown-menu') ? document.getElementById('profile-x-more') : link;
      reset();
      window.jQuery(modal).modal('show');
      return;
    }

    if (!modal.contains(target) || busy) return;
    // the footer's link to Settings: with changes it asks first, as closing does
    var settings = target.closest('.modal-footer a[href]');
    if (settings && dirty() && !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) {
      event.preventDefault();
      afterDiscard = settings.getAttribute('href');
      askToDiscard();
      return;
    }
    var choose = target.closest('[data-pe-pick]');
    if (choose) { parts(choose.getAttribute('data-pe-pick')).file.click(); return; }
    var clear = target.closest('[data-pe-remove]');
    if (clear) {
      var kind = clear.getAttribute('data-pe-remove');
      // a picture chosen but not sent: this takes it back; otherwise it removes the one the account has
      if (staged[kind]) showStored(kind); else showRemoved(kind);
      setError(kind, '');
      refresh();
      // the button just pressed hides itself: the focus goes to the camera beside it, not to the page
      var camera = modal.querySelector('[data-pe-pick="' + kind + '"]');
      if (camera) camera.focus();
    }
  });

  modal.addEventListener('change', function (event) {
    var input = event.target;
    if (!input || input.type !== 'file') return;
    var kind = input.name;
    var file = input.files && input.files[0];
    input.value = '';                   // the same file can be chosen again; the File itself is kept in `staged`
    if (file && KINDS.indexOf(kind) !== -1) pick(kind, file);
  });

  modal.addEventListener('input', refresh);
  modal.addEventListener('focusin', function (event) { if (form.contains(event.target)) lastFocus = event.target; });

  // --- saving ---------------------------------------------------------------------------------------------------------------

  function adopt(data) {
    // what the server has now: the text as stored, and each picture that went through
    if (data.values) {
      TEXT.forEach(function (name) {
        if (typeof data.values[name] !== 'string') return;
        baseline[name] = newlines(data.values[name]);
        var input = field(name);
        input.value = baseline[name];
        weigh(input);
      });
    }
    (data.saved || []).forEach(function (what) {
      if (KINDS.indexOf(what) === -1) return;
      stored[what] = { src: what === 'profile' ? data.profile_url : data.banner_url, has: !!(what === 'profile' ? data.has_profile : data.has_banner) };
      showStored(what);
    });
  }

  function show(errors) {
    var first = null;
    var picture = null;
    Object.keys(errors).forEach(function (name) {
      setError(name, errors[name]);
      if (!first && FIELDS[name]) first = field(name);
      if (!picture && KINDS.indexOf(name) !== -1) picture = document.getElementById('pe-error-' + name);
    });
    // the Save button is in the header and the pictures at the top of a form that scrolls: what failed is brought into view
    if (first) first.focus();
    else if (picture && picture.scrollIntoView) picture.scrollIntoView({ block: 'nearest' });
  }

  // a save that did not go through: `message` is the sentence to show in the status line (none when each field says its own)
  function failed(message) {
    busy = false;
    say(message, true);
    refresh();
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (busy || !dirty()) return;
    // a text left as it was is not held to today's limit (an older, longer bio still saves a new job)
    TEXT.forEach(function (name) { var input = field(name); if (newlines(input.value) === baseline[name]) input.setCustomValidity(''); });
    if (!form.checkValidity()) { form.reportValidity(); return; }       // a text past its limit (all_js.js updateWeightedLimit)
    clearErrors();
    busy = true;
    refresh();
    say(tr('Saving\u2026'));

    var body;
    try {
      body = new FormData();
      body.append('formkey', formkey());
      // only what this pop-up changed: a tab opened before another one saved would otherwise send the old text back as an edit
      TEXT.forEach(function (name) { if (newlines(field(name).value) !== baseline[name]) body.append(name, field(name).value); });
      KINDS.forEach(function (kind) {
        if (staged[kind]) body.append(kind, staged[kind], staged[kind].name);
        else if (removed[kind]) body.append('remove_' + kind, '1');
      });
    } catch (e) {
      failed(tr('Could not save that. Try again.'));
      return;
    }

    var abort = window.AbortController ? new window.AbortController() : null;
    var timer = abort ? window.setTimeout(function () { abort.abort(); }, SAVE_TIMEOUT) : null;
    fetch('/settings/profile/edit', { method: 'POST', credentials: 'same-origin', body: body, signal: abort ? abort.signal : undefined })
      .then(function (res) {
        // a session that ended is answered with the sign-in page (a redirect), and a file past the server's limit with a page
        // of HTML: neither is a save
        var type = res.headers.get('content-type') || '';
        if (res.status === 429) { var limited = new Error('limited'); limited.limited = true; throw limited; }       // 20 saves an hour: that is not "try again"
        if (res.redirected || type.indexOf('application/json') !== 0) throw new Error('not a save');
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (res) {
        var data = res.data || {};
        if (res.ok && data.ok) {
          if (!data.changed) { busy = false; leaving = true; window.jQuery(modal).modal('hide'); return; }
          // saved: the page reloads (the pop-up stays busy until it does), so every place that shows the name or a picture is current
          try { window.sessionStorage.setItem(SAVED_FLAG, '1'); } catch (e) { /* the page just reloads without the note */ }
          window.location.reload();
          return;
        }
        if ((data.saved || []).length) { reloadOnClose = true; adopt(data); }      // part of it went through: the page behind reloads when this closes
        if (data.errors) show(data.errors);
        failed(data.errors ? '' : data.error);
      })
      .catch(function (error) { failed(error && error.limited ? tr('Too many saves. Try again in a while.') : tr('Could not save that. Try again.')); })
      .then(function () { if (timer) window.clearTimeout(timer); });
  });

  // --- start ------------------------------------------------------------------------------------------------------------------

  function toast(message) {
    var text = document.getElementById('toast-post-success-text');
    if (!text || !window.jQuery) return;
    text.textContent = message;
    window.jQuery('#toast-post-success').toast('dispose');
    window.jQuery('#toast-post-success').toast('show');
  }

  function init() {
    TEXT.forEach(function (name) { baseline[name] = newlines(field(name).value); });
    KINDS.forEach(function (kind) {
      var p = parts(kind);
      stored[kind] = { src: p.img.getAttribute('src'), has: p.root.getAttribute('data-has') === '1' };
    });
    if (window.jQuery) {
      window.jQuery(modal).on('hide.bs.modal', onHide).on('shown.bs.modal', onShown).on('hidden.bs.modal', onHidden);
    }
    var saved = false;
    try { saved = !!window.sessionStorage.getItem(SAVED_FLAG); window.sessionStorage.removeItem(SAVED_FLAG); } catch (e) { saved = false; }
    if (saved) toast(tr('Saved'));
    refresh();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
