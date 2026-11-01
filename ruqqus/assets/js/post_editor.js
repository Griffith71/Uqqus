/*
 * Post editor toolbar (create a post, edit a post).
 *
 * Every <div class="post-toolbar" data-target="<textarea id>"> becomes a toolbar
 * for that text box. The text stays plain markdown: the buttons only write
 * syntax. Editing goes through the helpers in all_js.js (editorReplace,
 * editorToggleWrap, editorLineRange, ...), so the browser's undo/redo keeps
 * working. The colour, highlight and alignment names below must match
 * ruqqus/helpers/post_formatting.py (tests/test_post_editor_assets.py checks).
 *
 * The order is Reddit's: Undo, Redo, then what goes into the text (image, video,
 * sound, GIF, emoji, link), then "Aa", which folds the rest of the formatting away
 * (folded by default; the viewer's choice is kept in localStorage).
 *
 * On the Create a post card and the feed composer, from 992px, the toolbar is a column that stays beside the text box as
 * the page scrolls (assets/style/_post_rail.scss); its menus, link dialog and status line are then fixed to the viewport
 * beside it (isRail, placeBeside) because the column scrolls by itself. Elsewhere (the edit form, narrow screens) it is
 * the row at the top of the box.
 *
 * Image, audio and video buttons are placeholders until an uploader exists:
 * PostEditor.register('image', function (ctx) { ... ctx.insert('![](url)') })
 * switches the button on. PostEditor.use(kind, textarea) runs one for another
 * button (the composers' video and sound for the post itself).
 */
(function () {
  'use strict';

  // loaded by default.html for the feed composer and by the post page's own edit form: once is enough
  if (window.PostEditor) return;

  var TEXT_COLORS = ['red', 'orange', 'green', 'blue', 'purple', 'pink', 'gray'];
  var HIGHLIGHTS = ['yellow', 'green', 'blue', 'pink'];
  var ALIGNMENTS = ['left', 'center', 'right'];

  // the colour names the swatches are titled with (the names above stay the codes the server reads)
  function colourName(name) {
    // Translators: a colour, in "Text colour %(colour)s" and "Highlight %(colour)s"
    return { red: tr('red'), orange: tr('orange'), green: tr('green'), blue: tr('blue'), purple: tr('purple'),
             pink: tr('pink'), gray: tr('gray'), yellow: tr('yellow') }[name] || name;
  }

  var BUILT_IN_TEMPLATES = [
    // Translators: the name of a ready-made block for a post: a centred headline and a button
    { name: tr('Call to action'),
      // Translators: placeholder text a member replaces: the headline of a "Call to action" block, and its button's label
      body: '::: center\n\n**' + tr('Your headline') + '**\n\n[' + tr('Button label') + '](https://){.button}\n\n:::' },
    // Translators: the name of a ready-made block for a post: a quotation set apart
    { name: tr('Pull quote'),
      // Translators: placeholder text a member replaces: the quotation of a "Pull quote" block, and who said it
      body: '> “' + tr('A line worth quoting') + '”\n>\n> — ' + tr('Who said it') },
    // Translators: the name of a ready-made block for a post: a horizontal line
    { name: tr('Divider'), body: '---' }
  ];

  var FOLD_KEY = 'post_toolbar_open';   // '1' while the viewer keeps the formatting open
  var uploaders = {};       // kind -> handler registered through PostEditor.register
  var stubButtons = [];     // [{kind, button}]
  var openMenus = [];

  // ---------------------------------------------------------------- DOM bits

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function icon(cls) {
    var i = document.createElement('i');
    i.className = cls;
    i.setAttribute('aria-hidden', 'true');
    return i;
  }

  // keep the text box's selection and focus when a toolbar control is pressed
  function keepSelection(node) {
    node.addEventListener('mousedown', function (event) { event.preventDefault(); });
  }

  function tbButton(title, iconClass, onClick, extra) {
    var b = make('button', 'btn btn-sm post-tb-btn' + (extra ? ' ' + extra : ''));
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', title);
    if (iconClass) b.appendChild(icon(iconClass));
    keepSelection(b);
    if (onClick) b.addEventListener('click', onClick);
    return b;
  }

  // a button whose face is a word ("GIF"), not a glyph
  function withText(button, text) {
    button.appendChild(make('span', 'post-tb-text', text));
    return button;
  }

  // ------------------------------------------------------- the right-hand rail

  // From 992px the composers' toolbar is a sticky column beside the text box (assets/style/_post_rail.scss, which also
  // sets --post-rail: 1 on it, so the width threshold lives in the stylesheet only). The column scrolls by itself when the
  // formatting is open, and that would clip a menu, the link dialog or a status line placed inside it: in the rail those
  // are fixed to the viewport beside it instead.
  function isRail(bar) {
    return !!bar && window.getComputedStyle(bar).getPropertyValue('--post-rail').trim() === '1';
  }

  // `anchor` (a button) sets the top edge; without it the rail's own top does. The node must be showing, to be measured.
  // It is held by the edge that faces the rail, so a menu that grows (the templates arrive after it opens) grows away from
  // the rail and never over it. It stays clear of the top bar and of Create a post's fixed bar along the bottom.
  function placeBeside(node, bar, anchor) {
    var rect = bar.getBoundingClientRect();
    var from = (anchor || bar).getBoundingClientRect();
    var margin = 8;
    var navbar = document.getElementById('navbar');
    var bottomBar = document.querySelector('.composer-bar');
    var floor = (navbar ? navbar.getBoundingClientRect().bottom : 0) + margin;
    var ceiling = Math.min(window.innerHeight, bottomBar ? bottomBar.getBoundingClientRect().top : window.innerHeight) - margin;
    node.style.position = 'fixed';
    node.style.bottom = 'auto';
    node.style.margin = '0';
    if (document.documentElement.dir === 'rtl') {
      node.style.right = 'auto';
      node.style.left = (rect.right + margin) + 'px';
    } else {
      node.style.left = 'auto';
      node.style.right = Math.max(margin, document.documentElement.clientWidth - rect.left + margin) + 'px';
    }
    node.style.top = Math.max(floor, Math.min(from.top, ceiling - node.offsetHeight)) + 'px';
  }

  function clearPlacement(node) {
    ['position', 'left', 'top', 'right', 'bottom', 'margin'].forEach(function (p) { node.style[p] = ''; });
  }

  // ---------------------------------------------------------------- Preview

  // The title a composer's own field holds (Create a post, a shared draft, the feed composer), for Preview to show as the post
  // page does. The edit-post box, the wiki and the event editors are in neither and preview the text alone.
  function titleField(bar) {
    var scope = bar.closest('.composer-card, .inline-composer');
    return scope ? scope.querySelector('.composer-title-input, .ic-title') : null;
  }

  // Everything that is the server's HTML goes in here: the one place this script writes markup (the Preview's text, from the same
  // pipeline as a posted body, and the overlay's card, built and sanitized by templates/partials/post_preview.html).
  function setServerHtml(node, html) {
    node.innerHTML = html;
  }

  // The width of the post page's card for the composer this toolbar belongs to. The feed composer's own card is exactly as wide as
  // the post page's; on Create a post (and a shared draft) the probe in templates/submit.html is as wide as the post page's column,
  // which has the card's two 15px gutters round it.
  function postedCardWidth(bar) {
    var feed = bar.closest('.inline-composer-card');
    if (feed) return feed.getBoundingClientRect().width;
    var probe = document.getElementById('posted-width-probe');
    return probe ? probe.getBoundingClientRect().width - 30 : 0;
  }

  // From 992px the two composers preview in an overlay that looks like the post (below that, and in the edit-post box, the wiki and
  // the event editors, Preview is the in-place Write / Preview toggle). It holds the post page's own card at the post page's own
  // width, built by the server (routes/post_editor.py post_preview), so the browser lays the text out as it will lay it out.
  function overlayWanted(bar) {
    return isRail(bar) && !!bar.closest('.composer-card, .inline-composer');
  }

  var overlay = null;     // { modal, dialog, sheet, field, token }, made once on the first Preview

  function previewOverlay() {
    if (overlay) return overlay;
    var modal = make('div', 'modal fade');
    modal.id = 'postPreviewModal';
    modal.tabIndex = -1;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-labelledby', 'postPreviewTitle');
    modal.setAttribute('aria-hidden', 'true');
    var dialog = make('div', 'modal-dialog modal-dialog-centered post-preview-dialog');
    dialog.setAttribute('role', 'document');
    var content = make('div', 'modal-content');
    var head = make('div', 'modal-header');
    var title = make('h5', 'modal-title', tr('Preview'));
    title.id = 'postPreviewTitle';
    var close = make('button', 'close');
    close.type = 'button';
    close.setAttribute('data-dismiss', 'modal');
    close.setAttribute('aria-label', tr('Close'));
    var mark = make('span');
    mark.setAttribute('aria-hidden', 'true');
    mark.appendChild(icon('fas fa-times'));
    close.appendChild(mark);
    head.appendChild(title);
    head.appendChild(close);
    var body = make('div', 'modal-body');
    var sheet = make('div', 'post-preview-sheet');
    body.appendChild(sheet);
    content.appendChild(head);
    content.appendChild(body);
    dialog.appendChild(content);
    modal.appendChild(dialog);
    document.body.appendChild(modal);
    overlay = { modal: modal, dialog: dialog, sheet: sheet, field: null, token: 0 };
    // an answer that arrives after it closed is let go, and writing carries on in the text box
    $(modal).on('hidden.bs.modal', function () {
      overlay.token++;
      if (overlay.field) overlay.field.focus();
    });
    return overlay;
  }

  // what the composer holds, read from its own card: the title, the text, the link and the four flags
  function previewFields(bar, el) {
    var scope = bar.closest('.composer-card, .inline-composer');
    var form = new FormData();
    var title = titleField(bar), url = scope.querySelector('[name="url"]');
    form.append('formkey', formkey());
    form.append('body', el.value);
    form.append('title', title ? title.value : '');
    form.append('url', url ? url.value : '');
    ['anonymous', 'paid_partnership', 'made_with_ai', 'sensitive'].forEach(function (name) {
      var box = scope.querySelector('input[type="checkbox"][name="' + name + '"]');
      if (box && box.checked) form.append(name, '1');
    });
    return form;
  }

  function openPreviewOverlay(bar, el) {
    var o = previewOverlay();
    var ticket = ++o.token;
    o.field = el;
    o.dialog.style.setProperty('--preview-card-w', postedCardWidth(bar) + 'px');
    o.sheet.textContent = tr('Loading preview...');
    $(o.modal).modal('show');
    fetch('/api/post_preview', { method: 'POST', body: previewFields(bar, el), credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (res) {
        if (ticket !== o.token) return;
        if (!res.ok) { o.sheet.textContent = res.data.error || tr('Could not load the preview.'); return; }
        setServerHtml(o.sheet, res.data.html || '');
      })
      .catch(function () { if (ticket === o.token) o.sheet.textContent = tr('Could not load the preview.'); });
  }

  // ------------------------------------------------------------------ menus

  function closeMenus() {
    openMenus.forEach(function (m) {
      m.menu.classList.add('d-none');
      m.button.setAttribute('aria-expanded', 'false');
    });
    openMenus = [];
  }

  // a menu fixed beside the rail would stay put while the page (and the sticky rail with it) moves
  function closeFloatingMenus() {
    if (openMenus.some(function (m) { return m.menu.style.position === 'fixed'; })) closeMenus();
  }

  document.addEventListener('click', function (event) {
    if (!event.target.closest || !event.target.closest('.post-tb-menuwrap')) closeMenus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMenus();
  });
  window.addEventListener('scroll', closeFloatingMenus, { passive: true });
  window.addEventListener('resize', closeFloatingMenus);

  // fill(menu, close) builds the menu's items each time it opens
  function dropdown(title, iconClass, fill) {
    var wrap = make('div', 'post-tb-menuwrap');
    var menu = make('div', 'post-tb-menu d-none');
    menu.setAttribute('role', 'menu');
    var button = tbButton(title, iconClass, function () {
      var wasClosed = menu.classList.contains('d-none');
      closeMenus();
      if (!wasClosed) return;
      while (menu.firstChild) menu.removeChild(menu.firstChild);
      fill(menu, closeMenus);
      menu.classList.remove('d-none');
      var bar = button.closest('.post-toolbar');
      if (isRail(bar)) placeBeside(menu, bar, button); else clearPlacement(menu);
      button.setAttribute('aria-expanded', 'true');
      openMenus.push({ menu: menu, button: button });
    });
    button.setAttribute('aria-haspopup', 'true');
    button.setAttribute('aria-expanded', 'false');
    wrap.appendChild(button);
    wrap.appendChild(menu);
    return wrap;
  }

  function menuItem(label, onClick, iconClass) {
    var item = make('button', 'post-tb-item');
    item.type = 'button';
    item.setAttribute('role', 'menuitem');
    if (iconClass) item.appendChild(icon(iconClass + ' fa-fw mr-2'));
    item.appendChild(document.createTextNode(label));
    keepSelection(item);
    item.addEventListener('click', function () { closeMenus(); onClick(); });
    return item;
  }

  // ----------------------------------------------------------- text helpers

  // put a block (rule, table, template...) on its own paragraph at the caret
  function insertBlock(el, text) {
    var v = el.value, s = el.selectionStart, e = el.selectionEnd;
    var before = v.substring(0, s), after = v.substring(e);
    var lead = !before ? '' : before.slice(-2) === '\n\n' ? '' : before.slice(-1) === '\n' ? '\n' : '\n\n';
    var trail = !after ? '' : after.slice(0, 2) === '\n\n' ? '' : after.charAt(0) === '\n' ? '\n' : '\n\n';
    editorReplace(el, s, e, lead + text + trail, s + lead.length + text.length);
  }

  function setHeading(el, level) {
    var r = editorLineRange(el);
    var lines = el.value.substring(r.start, r.end).split('\n');
    var prefix = level ? new Array(level + 1).join('#') + ' ' : '';
    var filled = lines.filter(function (l) { return l.trim().length; });
    var same = level && filled.length && filled.every(function (l) {
      return l.indexOf(prefix) === 0 && l.charAt(prefix.length) !== '#';
    });
    var out = lines.map(function (l) {
      if (!l.trim().length) return l;
      var bare = l.replace(/^\s{0,3}#{1,6}\s+/, '');
      return same ? bare : prefix + bare;
    }).join('\n');
    editorReplace(el, r.start, r.end, out, r.start, r.start + out.length);
  }

  function blockCode(el) {
    var r = editorLineRange(el);
    var text = el.value.substring(r.start, r.end);
    var lines = text.split('\n');
    var out;
    if (lines.length >= 2 && /^```/.test(lines[0]) && /^```\s*$/.test(lines[lines.length - 1])) {
      out = lines.slice(1, -1).join('\n');
    } else {
      out = '```\n' + text + '\n```';
    }
    editorReplace(el, r.start, r.end, out, r.start, r.start + out.length);
  }

  // {c:name}..{/c} (kind 'c') and {h:name}..{/h} (kind 'h'); name null removes it
  function applyTag(el, kind, name) {
    var v = el.value, s = el.selectionStart, e = el.selectionEnd;
    var sel = v.substring(s, e);
    var inner = sel, from = s, to = e;
    var inside = new RegExp('^\\{' + kind + ':[a-z]+\\}([\\s\\S]*)\\{/' + kind + '\\}$').exec(sel);
    if (inside) {
      inner = inside[1];
    } else {
      var open = new RegExp('\\{' + kind + ':[a-z]+\\}$').exec(v.substring(0, s));
      var close = new RegExp('^\\{/' + kind + '\\}').exec(v.substring(e));
      if (open && close) {
        from = s - open[0].length;
        to = e + close[0].length;
      }
    }
    if (name === null) {
      editorReplace(el, from, to, inner, from, from + inner.length);
      return;
    }
    var opener = '{' + kind + ':' + name + '}';
    editorReplace(el, from, to, opener + inner + '{/' + kind + '}', from + opener.length, from + opener.length + inner.length);
  }

  // ::: left|center|right around the lines the selection touches; picking the
  // same alignment again (or "none") removes the block
  function applyAlign(el, name) {
    var v = el.value;
    var r = editorLineRange(el);
    var openRe = /^:::[ \t]*(left|center|right)[ \t]*$/;
    var closeRe = /^:::[ \t]*$/;
    var lines = v.substring(r.start, r.end).split('\n');
    var start = r.start, end = r.end;

    var wrapped = lines.length >= 2 && openRe.test(lines[0]) && closeRe.test(lines[lines.length - 1]);
    if (!wrapped && r.start > 0 && r.end < v.length) {
      // the markers may sit on the lines just outside the selection
      var prevStart = v.lastIndexOf('\n', r.start - 2) + 1;
      var prevLine = v.substring(prevStart, r.start - 1);
      var nextEnd = v.indexOf('\n', r.end + 1);
      var nextLine = v.substring(r.end + 1, nextEnd === -1 ? v.length : nextEnd);
      if (openRe.test(prevLine) && closeRe.test(nextLine)) {
        start = prevStart;
        end = nextEnd === -1 ? v.length : nextEnd;
        lines = [prevLine].concat(lines, [nextLine]);
        wrapped = true;
      }
    }

    var out;
    if (wrapped) {
      var current = openRe.exec(lines[0])[1];
      var inner = lines.slice(1, -1).join('\n');
      out = (name === null || name === current) ? inner : '::: ' + name + '\n' + inner + '\n:::';
    } else {
      if (name === null) return;
      var text = lines.join('\n');
      if (!text.trim().length) {
        editorReplace(el, start, end, '::: ' + name + '\n\n:::', start + name.length + 5);
        return;
      }
      out = '::: ' + name + '\n' + text + '\n:::';
    }
    editorReplace(el, start, end, out, start, start + out.length);
  }

  // an address the markdown can safely carry, or null
  function cleanUrl(raw) {
    var u = (raw || '').trim();
    if (!u) return null;
    if (/^\/\//.test(u)) {
      u = 'https:' + u;
    } else if (/^\//.test(u)) {
      // a path on this site
    } else if (/^https?:\/\//i.test(u)) {
      // fine
    } else if (/^[a-z][a-z0-9+.\-]*:/i.test(u)) {
      return null;   // javascript:, data:, mailto: ... are not links we accept
    } else {
      u = 'https://' + u;
    }
    return u.replace(/\s/g, '%20').replace(/\(/g, '%28').replace(/\)/g, '%29');
  }

  function escapeLabel(text) {
    return text.replace(/([\[\]\\])/g, '\\$1');
  }

  // --------------------------------------------------------------- the bar

  function PostToolbar(bar) {
    var self = this;
    var id = bar.getAttribute('data-target');
    // A comment box's bar (partials/comment_tools.html): the formatting alone. The row under the box already has the
    // pictures, sound, GIF and emoji, its own "Aa" opens and closes this bar, and a comment has no Write / Preview.
    var forComment = bar.getAttribute('data-variant') === 'comment';
    var statusTimer = null;
    var dialog = null;
    var dialogMover = null;   // keeps the dialog beside a rail that moves with the page
    var previewing = false;
    var tabs = {};

    function ta() { return document.getElementById(id); }

    function status(message) {
      statusNode.textContent = message || '';
      clearTimeout(statusTimer);
      if (message) {
        if (isRail(bar)) placeBeside(statusNode, bar); else clearPlacement(statusNode);
        statusTimer = setTimeout(function () { statusNode.textContent = ''; }, 4000);
      }
    }

    // --- a one-row dialog under the buttons (link, button, template name)
    function openDialog(spec) {
      closeDialog();
      var el = ta();
      var range = { s: el.selectionStart, e: el.selectionEnd };
      var form = make('form', 'post-tb-dialog');
      form.setAttribute('aria-label', spec.title);
      var fields = {};
      spec.fields.forEach(function (f) {
        var input = make('input', 'form-control form-control-sm');
        input.type = 'text';
        input.placeholder = f.label;
        input.setAttribute('aria-label', f.label);
        input.value = f.value || '';
        if (f.max) input.maxLength = f.max;
        fields[f.name] = input;
        form.appendChild(input);
      });
      // Translators: a button (a verb): put the link or button into the post
      var go = make('button', 'btn btn-sm btn-primary', spec.submit || tr('Insert'));
      go.type = 'submit';
      var cancel = make('button', 'btn btn-sm btn-link text-muted', tr('Cancel'));
      cancel.type = 'button';
      var error = make('span', 'post-tb-dialog-error text-danger text-small');
      error.setAttribute('role', 'alert');
      form.appendChild(go);
      form.appendChild(cancel);
      form.appendChild(error);

      cancel.addEventListener('click', function () { closeDialog(); ta().focus(); });
      form.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { event.stopPropagation(); closeDialog(); ta().focus(); }
      });
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var values = {};
        Object.keys(fields).forEach(function (k) { values[k] = fields[k].value; });
        var problem = spec.onSubmit(values, range);
        if (problem) { error.textContent = problem; return; }
        closeDialog();
      });

      bar.appendChild(form);
      dialog = form;
      if (isRail(bar)) {
        placeBeside(form, bar);
        dialogMover = function () { placeBeside(form, bar); };
        window.addEventListener('scroll', dialogMover, { passive: true });
        window.addEventListener('resize', dialogMover);
      }
      var firstEmpty = spec.fields.filter(function (f) { return !f.value; })[0];
      fields[(firstEmpty || spec.fields[0]).name].focus();
    }

    function closeDialog() {
      if (dialog && dialog.parentNode) dialog.parentNode.removeChild(dialog);
      dialog = null;
      if (dialogMover) {
        window.removeEventListener('scroll', dialogMover);
        window.removeEventListener('resize', dialogMover);
        dialogMover = null;
      }
    }

    function linkDialog() {
      var el = ta();
      var selected = el.value.substring(el.selectionStart, el.selectionEnd);
      openDialog({
        title: tr('Insert link'),
        fields: [{ name: 'label', label: tr('Link text'), value: selected }, { name: 'url', label: 'https://...' }],
        onSubmit: function (v, range) {
          var url = cleanUrl(v.url);
          if (!url) return tr('Enter a web address, like https://example.com');
          var label = v.label.trim() || url;
          editorReplace(el, range.s, range.e, '[' + escapeLabel(label) + '](' + url + ')');
        }
      });
    }

    function buttonDialog() {
      var el = ta();
      var selected = el.value.substring(el.selectionStart, el.selectionEnd).trim();
      openDialog({
        title: tr('Insert button'),
        fields: [{ name: 'label', label: tr('Button text'), value: selected.indexOf('\n') === -1 ? selected : '' }, { name: 'url', label: 'https://...' }],
        onSubmit: function (v, range) {
          var url = cleanUrl(v.url);
          var label = v.label.trim();
          if (!label) return tr('Give the button some text.');
          if (!url) return tr('Enter a web address, like https://example.com');
          el.setSelectionRange(range.s, range.e);
          insertBlock(el, '[' + escapeLabel(label) + '](' + url + '){.button}');
        }
      });
    }

    function saveTemplateDialog() {
      var el = ta();
      var text = el.value.substring(el.selectionStart, el.selectionEnd) || el.value;
      if (!text.trim()) { status(tr('Write something first.')); return; }
      openDialog({
        title: tr('Save as template'),
        // Translators: a button that saves a template
        submit: tr('Save'),
        fields: [{ name: 'name', label: tr('Template name'), max: 60 }],
        onSubmit: function (v) {
          if (!v.name.trim()) return tr('Give the template a name.');
          var form = new FormData();
          form.append('formkey', formkey());
          form.append('name', v.name);
          form.append('body', text);
          fetch('/api/post_templates', { method: 'POST', body: form, credentials: 'same-origin' })
            .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
            .then(function (res) { status(res.ok ? tr('Template saved.') : (res.data.error || tr('Could not save the template.'))); })
            .catch(function () { status(tr('Could not save the template.')); });
        }
      });
    }

    function fillTemplates(menu) {
      var el = ta();
      BUILT_IN_TEMPLATES.forEach(function (t) {
        menu.appendChild(menuItem(t.name, function () { insertBlock(el, t.body); }, 'far fa-file'));
      });
      var saved = make('div', 'post-tb-saved');
      var loading = make('div', 'post-tb-note text-muted text-small', tr('Loading your templates...'));
      saved.appendChild(loading);
      menu.appendChild(make('div', 'post-tb-rule'));
      menu.appendChild(saved);
      menu.appendChild(make('div', 'post-tb-rule'));
      menu.appendChild(menuItem(tr('Save as template...'), saveTemplateDialog, 'fas fa-plus'));

      fetch('/api/post_templates', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          saved.removeChild(loading);
          if (!data.templates || !data.templates.length) {
            saved.appendChild(make('div', 'post-tb-note text-muted text-small', tr('Your saved templates show up here.')));
            return;
          }
          data.templates.forEach(function (t) {
            var row = make('div', 'post-tb-saved-row');
            var use = menuItem(t.name, function () { insertBlock(el, t.body); }, 'far fa-bookmark');
            var del = tbButton(tr('Delete "%(name)s"', {name: t.name}), 'fas fa-times', function (event) {
              event.stopPropagation();
              var form = new FormData();
              form.append('formkey', formkey());
              fetch('/api/post_templates/' + t.id + '/delete', { method: 'POST', body: form, credentials: 'same-origin' })
                .then(function (r) { if (r.ok) row.parentNode.removeChild(row); });
            }, 'post-tb-delete');
            row.appendChild(use);
            row.appendChild(del);
            saved.appendChild(row);
          });
        })
        .catch(function () {
          if (loading.parentNode) loading.textContent = tr('Could not load your templates.');
        });
    }

    // --- Write / Preview
    function setPreview(on) {
      var el = ta();
      var area = bar.parentNode.querySelector('.post-preview');
      previewing = on;
      tabs.write.classList.toggle('active', !on);
      tabs.preview.classList.toggle('active', on);
      bar.classList.toggle('is-previewing', on);
      Array.prototype.forEach.call(bar.querySelectorAll('.post-tb-btn:not(.post-tb-tab)'), function (b) {
        b.disabled = on || (b.getAttribute('data-stub') === '1');
      });
      closeMenus();
      closeDialog();
      // the composer wraps its text box (and character counter) in an input group
      var holder = el.closest('.input-group') || el;
      if (!on) {
        area.classList.add('d-none');
        holder.classList.remove('d-none');
        el.focus();
        return;
      }
      holder.classList.add('d-none');
      area.classList.remove('d-none');
      area.textContent = tr('Loading preview...');
      var field = titleField(bar);
      var title = field ? field.value.trim() : '';
      var form = new FormData();
      form.append('formkey', formkey());
      form.append('body', el.value);
      fetch('/api/preview', { method: 'POST', body: form, credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (!previewing) return;
          if (!res.ok) { area.textContent = res.data.error || tr('Could not load the preview.'); return; }
          // the post as the post page shows it: its title, then its text (the title is the member's own words, set as text)
          area.textContent = '';
          if (title) {
            var heading = make('div', 'post-preview-title');
            heading.setAttribute('dir', 'auto');
            heading.textContent = title;
            area.appendChild(heading);
          }
          var text = make('div', 'post-preview-text');
          text.setAttribute('dir', 'auto');
          // server output: rendered and sanitized by the same pipeline as a posted body
          setServerHtml(text, res.data.html || '');
          area.appendChild(text);
          if (!title && !text.textContent.trim() && !text.querySelector('img')) area.textContent = tr('Nothing to preview yet.');
        })
        .catch(function () { if (previewing) area.textContent = tr('Could not load the preview.'); });
    }

    // --- build the buttons
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', tr('Text formatting'));

    function group() {
      var g = make('span', 'post-tb-group');
      Array.prototype.slice.call(arguments).forEach(function (n) { g.appendChild(n); });
      bar.appendChild(g);
      return g;
    }

    function exec(command) {
      return function () {
        var el = ta();
        el.focus();
        try { document.execCommand(command); } catch (err) { /* nothing to undo */ }
      };
    }

    // Undo and redo first, then what goes into the text (pictures, video, sound, GIF, emoji), as Reddit's editor has them;
    // the formatting folds behind "Aa" (the owner's choice: collapsed, at the top of the box)
    // (the symbols are the Create post card's blue row: a solid picture, GIF written out, a solid smile)
    var toggle = null;
    if (!forComment) {
      group(
        tbButton(tr('Undo'), 'fas fa-undo', exec('undo')),
        tbButton(tr('Redo'), 'fas fa-redo', exec('redo'))
      );
      group(
        uploadStub('image', tr('Image (coming soon)'), tr('Add image'), 'fas fa-image'),
        uploadStub('video', tr('Video (coming soon)'), tr('Add video'), 'fas fa-video'),
        uploadStub('audio', tr('Sound (coming soon)'), tr('Add sound'), 'fas fa-volume-up'),
        withText(tbButton(tr('GIF'), null, function () { getGif(); commentForm(id); $('#gifModal').modal('show'); }), 'GIF'),
        tbButton(tr('Emoji'), 'fas fa-smile-beam', function () { commentForm(id); loadEmojis(); $('#emojiModal').modal('show'); })
      );
      toggle = tbButton(tr('Formatting'), null, null, 'post-tb-toggle');
      toggle.appendChild(make('span', 'post-tb-toggle-text', 'Aa'));
      toggle.setAttribute('aria-expanded', 'false');
      group(toggle);
    }

    // A link in the text is formatting: first behind "Aa", in a post's body and a comment alike (the owner, 2026-10-30:
    // the two editors keep one rule). The main row is what is added (pictures, sound, GIF, emoji), and on the Create post
    // card the blue row's own link button is the post's link, so the two link symbols no longer sit side by side.
    // Ctrl+K still opens it without unfolding anything.
    var extras = [];
    extras.push(group(tbButton(tr('Link (Ctrl+K)'), 'fas fa-link', linkDialog)));
    extras.push(group(dropdown(tr('Text style'), 'fas fa-heading', function (menu) {
      var el = ta();
      menu.appendChild(menuItem(tr('Paragraph'), function () { setHeading(el, 0); }, 'fas fa-paragraph'));
      [1, 2, 3, 4].forEach(function (n) {
        menu.appendChild(menuItem(tr('Heading %(level)s', {level: n}), function () { setHeading(el, n); }, 'fas fa-heading'));
      });
      // Translators: a text style in the editor: make the lines a quotation
      menu.appendChild(menuItem(tr('Quote'), function () { makeQuote(id); }, 'fas fa-quote-right'));
      menu.appendChild(menuItem(tr('Code block'), function () { blockCode(el); }, 'fas fa-code'));
    })));
    extras.push(group(
      tbButton(tr('Bold (Ctrl+B)'), 'fas fa-bold', function () { makeBold(id); }),
      tbButton(tr('Italic (Ctrl+I)'), 'fas fa-italic', function () { makeItalics(id); }),
      tbButton(tr('Strikethrough'), 'fas fa-strikethrough', function () { makeStrikethrough(id); }),
      // Translators: a toolbar button: format the selected words as computer code
      tbButton(tr('Code'), 'fas fa-code', function () { makeCode(id); }),
      dropdown(tr('Text colour'), 'fas fa-font', function (menu) {
        var el = ta();
        var grid = make('div', 'post-tb-swatches');
        TEXT_COLORS.forEach(function (name) {
          var sw = make('button', 'post-tb-swatch');
          sw.type = 'button';
          sw.title = colourName(name);
          sw.setAttribute('aria-label', tr('Text colour %(colour)s', {colour: colourName(name)}));
          sw.appendChild(make('span', 'tc-' + name, 'A'));
          keepSelection(sw);
          sw.addEventListener('click', function () { closeMenus(); applyTag(el, 'c', name); });
          grid.appendChild(sw);
        });
        menu.appendChild(grid);
        menu.appendChild(menuItem(tr('Remove colour'), function () { applyTag(el, 'c', null); }, 'fas fa-eraser'));
      }),
      // Translators: a toolbar menu: colour the background of the selected words, as a highlighter pen does
      dropdown(tr('Highlight'), 'fas fa-highlighter', function (menu) {
        var el = ta();
        var grid = make('div', 'post-tb-swatches');
        HIGHLIGHTS.forEach(function (name) {
          var sw = make('button', 'post-tb-swatch');
          sw.type = 'button';
          sw.title = colourName(name);
          sw.setAttribute('aria-label', tr('Highlight %(colour)s', {colour: colourName(name)}));
          sw.appendChild(make('span', 'hl-' + name, 'A'));
          keepSelection(sw);
          sw.addEventListener('click', function () { closeMenus(); applyTag(el, 'h', name); });
          grid.appendChild(sw);
        });
        menu.appendChild(grid);
        menu.appendChild(menuItem(tr('Remove highlight'), function () { applyTag(el, 'h', null); }, 'fas fa-eraser'));
      }),
      tbButton(tr('Superscript'), 'fas fa-superscript', function () { editorToggleWrap(id, '<sup>', '</sup>'); }),
      tbButton(tr('Subscript'), 'fas fa-subscript', function () { editorToggleWrap(id, '<sub>', '</sub>'); })
    ));
    extras.push(group(
      tbButton(tr('Quote'), 'fas fa-quote-right', function () { makeQuote(id); }),
      tbButton(tr('Bulleted list'), 'fas fa-list-ul', function () { makeBulletList(id); }),
      tbButton(tr('Numbered list'), 'fas fa-list-ol', function () { makeNumberedList(id); }),
      dropdown(tr('Alignment'), 'fas fa-align-left', function (menu) {
        var el = ta();
        var labels = { left: tr('Align left'), center: tr('Align center'), right: tr('Align right') };
        ALIGNMENTS.forEach(function (name) {
          menu.appendChild(menuItem(labels[name], function () { applyAlign(el, name); }, 'fas fa-align-' + name));
        });
        menu.appendChild(menuItem(tr('Remove alignment'), function () { applyAlign(el, null); }, 'fas fa-eraser'));
      })
    ));
    extras.push(group(
      // Translators: a toolbar button that inserts a button (a link drawn as a button) into the post
      tbButton(tr('Button'), 'fas fa-hand-pointer', buttonDialog),
      // Translators: a toolbar menu of ready-made blocks to insert into the post
      dropdown(tr('Template'), 'far fa-file-alt', fillTemplates),
      // Translators: a toolbar menu with more formatting
      dropdown(tr('More'), 'fas fa-ellipsis-h', function (menu) {
        var el = ta();
        menu.appendChild(menuItem(tr('Horizontal rule'), function () { insertBlock(el, '---'); }, 'fas fa-minus'));
        // Translators: a menu item: hide the selected words until a reader taps them
        menu.appendChild(menuItem(tr('Spoiler'), function () { makeSpoiler(id); }, 'fas fa-eye-slash'));
        // Translators: a menu item: insert a table into the post
        menu.appendChild(menuItem(tr('Table'), function () {
          // Translators: placeholder text a member replaces: a table's column heading and one of its cells
          var column = tr('Column'), cell = tr('Cell');
          insertBlock(el, '| ' + column + ' | ' + column + ' |\n| --- | --- |\n| ' + cell + ' | ' + cell + ' |');
        }, 'fas fa-table'));
      })
    ));
    extras.forEach(function (g) { g.classList.add('post-tb-extra'); });

    // folded or not is the viewer's own choice, kept in this browser (it may be unavailable: then it starts folded)
    function setFolded(folded, remember) {
      bar.classList.toggle('post-tb-collapsed', folded);
      toggle.setAttribute('aria-expanded', String(!folded));
      toggle.title = folded ? tr('Show formatting') : tr('Hide formatting');
      toggle.setAttribute('aria-label', toggle.title);
      if (remember) {
        try { localStorage.setItem(FOLD_KEY, folded ? '0' : '1'); } catch (err) { /* storage unavailable */ }
      }
    }
    if (toggle) {
      var startOpen = false;
      try { startOpen = localStorage.getItem(FOLD_KEY) === '1'; } catch (err) { startOpen = false; }
      setFolded(!startOpen, false);
      toggle.addEventListener('click', function () { closeMenus(); setFolded(!bar.classList.contains('post-tb-collapsed'), true); });
    }

    var statusNode = make('span', 'post-tb-status text-muted text-small');
    statusNode.setAttribute('role', 'status');
    if (forComment) {
      bar.appendChild(statusNode);
      return bindKeys();
    }
    var spacer = make('span', 'post-tb-spacer');
    var tabGroup = make('span', 'post-tb-group post-tb-tabs');
    // The tabs carry an icon and their words: a top toolbar shows the words, the right-hand rail (too narrow for them)
    // shows the icon and keeps the words for screen readers and the tooltip (_post_rail.scss)
    // Translators: a tab: write the post's text (beside Preview)
    tabs.write = tbButton(tr('Write'), 'fas fa-pen post-tb-tab-icon', function () { if (previewing) setPreview(false); }, 'post-tb-tab post-tb-tab-write active');
    tabs.write.appendChild(make('span', 'post-tb-tab-text', tr('Write')));
    // Translators: a tab: see the post's text as it will look (beside Write)
    // From 992px, in the composers, Preview is a button that opens the overlay (Write is hidden: there is nothing to go back from)
    tabs.preview = tbButton(tr('Preview'), 'fas fa-eye post-tb-tab-icon', function () {
      if (overlayWanted(bar)) {
        if (previewing) setPreview(false);      // a window widened while previewing in place
        openPreviewOverlay(bar, ta());
      } else if (!previewing) {
        setPreview(true);
      }
    }, 'post-tb-tab');
    tabs.preview.appendChild(make('span', 'post-tb-tab-text', tr('Preview')));
    tabGroup.appendChild(tabs.write);
    tabGroup.appendChild(tabs.preview);
    bar.appendChild(spacer);
    bar.appendChild(tabGroup);
    bar.appendChild(statusNode);   // wraps to its own row, so it never pushes the tabs down

    // `waiting`: the button's name before an uploader exists; `ready`: once one does
    function uploadStub(kind, waiting, ready, iconClass) {
      var b = tbButton(waiting, iconClass, function () {
        var handler = uploaders[kind];
        if (!handler) return;
        var el = ta();
        handler({
          textarea: el,
          kind: kind,
          insert: function (text) { editorReplace(el, el.selectionStart, el.selectionEnd, text); }
        });
      });
      b.disabled = true;
      b.setAttribute('data-stub', '1');
      b.setAttribute('data-kind', kind);
      stubButtons.push({ kind: kind, button: b, ready: ready });
      if (uploaders[kind]) enableStub(stubButtons[stubButtons.length - 1]);
      return b;
    }

    // Ctrl/Cmd + B, I, K
    function bindKeys() {
      ta().addEventListener('keydown', function (event) {
        if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
        var key = event.key.toLowerCase();
        if (key === 'b') { event.preventDefault(); makeBold(id); }
        else if (key === 'i') { event.preventDefault(); makeItalics(id); }
        else if (key === 'k') { event.preventDefault(); linkDialog(); }
      });
      self.bar = bar;
    }
    bindKeys();
  }

  function enableStub(entry) {
    entry.button.disabled = false;
    entry.button.setAttribute('data-stub', '0');
    entry.button.title = entry.ready;
    entry.button.setAttribute('aria-label', entry.button.title);
  }

  window.PostEditor = {
    // register('image' | 'audio' | 'video', function (ctx) {...}); ctx has
    // textarea, kind and insert(text) (puts text at the caret)
    register: function (kind, handler) {
      uploaders[kind] = handler;
      stubButtons.forEach(function (entry) { if (entry.kind === kind) enableStub(entry); });
      // the composers' own buttons (a video or a sound for the post itself) wait for this
      document.dispatchEvent(new CustomEvent('posteditor:register', { detail: { kind: kind } }));
    },
    // whether an uploader exists for a kind, and running it for a given text box (the composers' post-level buttons)
    has: function (kind) { return !!uploaders[kind]; },
    use: function (kind, textarea) {
      var handler = uploaders[kind];
      if (!handler || !textarea) return false;
      handler({
        textarea: textarea,
        kind: kind,
        insert: function (text) { editorReplace(textarea, textarea.selectionStart, textarea.selectionEnd, text); }
      });
      return true;
    },
    version: 1
  };

  function build(bar) {
    if (bar.getAttribute('data-ready')) return;
    if (!document.getElementById(bar.getAttribute('data-target'))) return;
    bar.setAttribute('data-ready', '1');
    new PostToolbar(bar);
  }

  // a comment box's bar is built the first time its "Aa" opens it (comment boxes are many, and some arrive after the page)
  function init() {
    Array.prototype.forEach.call(document.querySelectorAll('.post-toolbar[data-target]:not([data-variant="comment"])'), build);
  }

  // A comment box's "Aa" (partials/comment_tools.html, data-comment-format) opens and closes the formatting above the box.
  // On the document, so the forms added later (a comment's own comment box, more comments) need nothing.
  document.addEventListener('mousedown', function (event) {
    if (event.target.closest && event.target.closest('[data-comment-format]')) event.preventDefault();   // the caret stays put
  });
  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('[data-comment-format]');
    if (!button) return;
    var bar = document.getElementById(button.getAttribute('aria-controls'));
    if (!bar) return;
    build(bar);
    var open = bar.hidden;
    closeMenus();
    bar.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    var box = document.getElementById(bar.getAttribute('data-target'));
    if (open && box) box.focus();
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
