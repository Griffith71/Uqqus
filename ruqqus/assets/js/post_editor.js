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
 * Image, audio and video buttons are placeholders until an uploader exists:
 * PostEditor.register('image', function (ctx) { ... ctx.insert('![](url)') })
 * switches the button on.
 */
(function () {
  'use strict';

  var TEXT_COLORS = ['red', 'orange', 'green', 'blue', 'purple', 'pink', 'gray'];
  var HIGHLIGHTS = ['yellow', 'green', 'blue', 'pink'];
  var ALIGNMENTS = ['left', 'center', 'right'];

  var BUILT_IN_TEMPLATES = [
    { name: 'Call to action', body: '::: center\n\n**Your headline**\n\n[Button label](https://){.button}\n\n:::' },
    { name: 'Pull quote', body: '> “A line worth quoting”\n>\n> — Who said it' },
    { name: 'Divider', body: '---' }
  ];

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

  // ------------------------------------------------------------------ menus

  function closeMenus() {
    openMenus.forEach(function (m) {
      m.menu.classList.add('d-none');
      m.button.setAttribute('aria-expanded', 'false');
    });
    openMenus = [];
  }

  document.addEventListener('click', function (event) {
    if (!event.target.closest || !event.target.closest('.post-tb-menuwrap')) closeMenus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeMenus();
  });

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
    var statusTimer = null;
    var dialog = null;
    var previewing = false;
    var tabs = {};

    function ta() { return document.getElementById(id); }

    function status(message) {
      statusNode.textContent = message || '';
      clearTimeout(statusTimer);
      if (message) statusTimer = setTimeout(function () { statusNode.textContent = ''; }, 4000);
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
      var go = make('button', 'btn btn-sm btn-primary', spec.submit || 'Insert');
      go.type = 'submit';
      var cancel = make('button', 'btn btn-sm btn-link text-muted', 'Cancel');
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
      var firstEmpty = spec.fields.filter(function (f) { return !f.value; })[0];
      fields[(firstEmpty || spec.fields[0]).name].focus();
    }

    function closeDialog() {
      if (dialog && dialog.parentNode) dialog.parentNode.removeChild(dialog);
      dialog = null;
    }

    function linkDialog() {
      var el = ta();
      var selected = el.value.substring(el.selectionStart, el.selectionEnd);
      openDialog({
        title: 'Insert link',
        fields: [{ name: 'label', label: 'Link text', value: selected }, { name: 'url', label: 'https://...' }],
        onSubmit: function (v, range) {
          var url = cleanUrl(v.url);
          if (!url) return 'Enter a web address, like https://example.com';
          var label = v.label.trim() || url;
          editorReplace(el, range.s, range.e, '[' + escapeLabel(label) + '](' + url + ')');
        }
      });
    }

    function buttonDialog() {
      var el = ta();
      var selected = el.value.substring(el.selectionStart, el.selectionEnd).trim();
      openDialog({
        title: 'Insert button',
        fields: [{ name: 'label', label: 'Button text', value: selected.indexOf('\n') === -1 ? selected : '' }, { name: 'url', label: 'https://...' }],
        onSubmit: function (v, range) {
          var url = cleanUrl(v.url);
          var label = v.label.trim();
          if (!label) return 'Give the button some text.';
          if (!url) return 'Enter a web address, like https://example.com';
          el.setSelectionRange(range.s, range.e);
          insertBlock(el, '[' + escapeLabel(label) + '](' + url + '){.button}');
        }
      });
    }

    function saveTemplateDialog() {
      var el = ta();
      var text = el.value.substring(el.selectionStart, el.selectionEnd) || el.value;
      if (!text.trim()) { status('Write something first.'); return; }
      openDialog({
        title: 'Save as template',
        submit: 'Save',
        fields: [{ name: 'name', label: 'Template name', max: 60 }],
        onSubmit: function (v) {
          if (!v.name.trim()) return 'Give the template a name.';
          var form = new FormData();
          form.append('formkey', formkey());
          form.append('name', v.name);
          form.append('body', text);
          fetch('/api/post_templates', { method: 'POST', body: form, credentials: 'same-origin' })
            .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
            .then(function (res) { status(res.ok ? 'Template saved.' : (res.data.error || 'Could not save the template.')); })
            .catch(function () { status('Could not save the template.'); });
        }
      });
    }

    function fillTemplates(menu) {
      var el = ta();
      BUILT_IN_TEMPLATES.forEach(function (t) {
        menu.appendChild(menuItem(t.name, function () { insertBlock(el, t.body); }, 'far fa-file'));
      });
      var saved = make('div', 'post-tb-saved');
      var loading = make('div', 'post-tb-note text-muted text-small', 'Loading your templates...');
      saved.appendChild(loading);
      menu.appendChild(make('div', 'post-tb-rule'));
      menu.appendChild(saved);
      menu.appendChild(make('div', 'post-tb-rule'));
      menu.appendChild(menuItem('Save as template...', saveTemplateDialog, 'fas fa-plus'));

      fetch('/api/post_templates', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          saved.removeChild(loading);
          if (!data.templates || !data.templates.length) {
            saved.appendChild(make('div', 'post-tb-note text-muted text-small', 'Your saved templates show up here.'));
            return;
          }
          data.templates.forEach(function (t) {
            var row = make('div', 'post-tb-saved-row');
            var use = menuItem(t.name, function () { insertBlock(el, t.body); }, 'far fa-bookmark');
            var del = tbButton('Delete "' + t.name + '"', 'fas fa-times', function (event) {
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
          if (loading.parentNode) loading.textContent = 'Could not load your templates.';
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
      area.textContent = 'Loading preview...';
      var form = new FormData();
      form.append('formkey', formkey());
      form.append('body', el.value);
      fetch('/api/preview', { method: 'POST', body: form, credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (res) {
          if (!previewing) return;
          if (!res.ok) { area.textContent = res.data.error || 'Could not load the preview.'; return; }
          // server output: rendered and sanitized by the same pipeline as a posted body
          area.innerHTML = res.data.html || '';
          if (!area.textContent.trim() && !area.querySelector('img')) area.textContent = 'Nothing to preview yet.';
        })
        .catch(function () { if (previewing) area.textContent = 'Could not load the preview.'; });
    }

    // --- build the buttons
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', 'Text formatting');

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

    group(
      tbButton('Undo', 'fas fa-undo', exec('undo')),
      tbButton('Redo', 'fas fa-redo', exec('redo'))
    );
    group(dropdown('Text style', 'fas fa-heading', function (menu) {
      var el = ta();
      menu.appendChild(menuItem('Paragraph', function () { setHeading(el, 0); }, 'fas fa-paragraph'));
      [1, 2, 3, 4].forEach(function (n) {
        menu.appendChild(menuItem('Heading ' + n, function () { setHeading(el, n); }, 'fas fa-heading'));
      });
      menu.appendChild(menuItem('Quote', function () { makeQuote(id); }, 'fas fa-quote-right'));
      menu.appendChild(menuItem('Code block', function () { blockCode(el); }, 'fas fa-code'));
    }));
    group(
      tbButton('Bold (Ctrl+B)', 'fas fa-bold', function () { makeBold(id); }),
      tbButton('Italic (Ctrl+I)', 'fas fa-italic', function () { makeItalics(id); }),
      tbButton('Strikethrough', 'fas fa-strikethrough', function () { makeStrikethrough(id); }),
      tbButton('Code', 'fas fa-code', function () { makeCode(id); }),
      dropdown('Text colour', 'fas fa-font', function (menu) {
        var el = ta();
        var grid = make('div', 'post-tb-swatches');
        TEXT_COLORS.forEach(function (name) {
          var sw = make('button', 'post-tb-swatch');
          sw.type = 'button';
          sw.title = name;
          sw.setAttribute('aria-label', 'Text colour ' + name);
          sw.appendChild(make('span', 'tc-' + name, 'A'));
          keepSelection(sw);
          sw.addEventListener('click', function () { closeMenus(); applyTag(el, 'c', name); });
          grid.appendChild(sw);
        });
        menu.appendChild(grid);
        menu.appendChild(menuItem('Remove colour', function () { applyTag(el, 'c', null); }, 'fas fa-eraser'));
      }),
      dropdown('Highlight', 'fas fa-highlighter', function (menu) {
        var el = ta();
        var grid = make('div', 'post-tb-swatches');
        HIGHLIGHTS.forEach(function (name) {
          var sw = make('button', 'post-tb-swatch');
          sw.type = 'button';
          sw.title = name;
          sw.setAttribute('aria-label', 'Highlight ' + name);
          sw.appendChild(make('span', 'hl-' + name, 'A'));
          keepSelection(sw);
          sw.addEventListener('click', function () { closeMenus(); applyTag(el, 'h', name); });
          grid.appendChild(sw);
        });
        menu.appendChild(grid);
        menu.appendChild(menuItem('Remove highlight', function () { applyTag(el, 'h', null); }, 'fas fa-eraser'));
      }),
      tbButton('Superscript', 'fas fa-superscript', function () { editorToggleWrap(id, '<sup>', '</sup>'); }),
      tbButton('Subscript', 'fas fa-subscript', function () { editorToggleWrap(id, '<sub>', '</sub>'); })
    );
    group(
      tbButton('Link (Ctrl+K)', 'fas fa-link', linkDialog),
      uploadStub('image', 'Image', 'far fa-image'),
      uploadStub('audio', 'Audio', 'fas fa-volume-up'),
      uploadStub('video', 'Video', 'fas fa-video'),
      tbButton('Quote', 'fas fa-quote-right', function () { makeQuote(id); })
    );
    group(
      tbButton('Bulleted list', 'fas fa-list-ul', function () { makeBulletList(id); }),
      tbButton('Numbered list', 'fas fa-list-ol', function () { makeNumberedList(id); }),
      dropdown('Alignment', 'fas fa-align-left', function (menu) {
        var el = ta();
        ALIGNMENTS.forEach(function (name) {
          menu.appendChild(menuItem('Align ' + name, function () { applyAlign(el, name); }, 'fas fa-align-' + name));
        });
        menu.appendChild(menuItem('Remove alignment', function () { applyAlign(el, null); }, 'fas fa-eraser'));
      })
    );
    group(
      tbButton('Button', 'fas fa-hand-pointer', buttonDialog),
      dropdown('Template', 'far fa-file-alt', fillTemplates),
      dropdown('More', 'fas fa-ellipsis-h', function (menu) {
        var el = ta();
        menu.appendChild(menuItem('Horizontal rule', function () { insertBlock(el, '---'); }, 'fas fa-minus'));
        menu.appendChild(menuItem('Spoiler', function () { makeSpoiler(id); }, 'fas fa-eye-slash'));
        menu.appendChild(menuItem('Table', function () {
          insertBlock(el, '| Column | Column |\n| --- | --- |\n| Cell | Cell |');
        }, 'fas fa-table'));
        menu.appendChild(menuItem('Emoji', function () {
          commentForm(id); loadEmojis(); $('#emojiModal').modal('show');
        }, 'far fa-smile-beam'));
        menu.appendChild(menuItem('GIF', function () {
          getGif(); commentForm(id); $('#gifModal').modal('show');
        }, 'fas fa-film'));
      })
    );

    var statusNode = make('span', 'post-tb-status text-muted text-small');
    statusNode.setAttribute('role', 'status');
    var spacer = make('span', 'post-tb-spacer');
    var tabGroup = make('span', 'post-tb-group post-tb-tabs');
    tabs.write = tbButton('Write', null, function () { if (previewing) setPreview(false); }, 'post-tb-tab active');
    tabs.write.textContent = 'Write';
    tabs.preview = tbButton('Preview', null, function () { if (!previewing) setPreview(true); }, 'post-tb-tab');
    tabs.preview.textContent = 'Preview';
    tabGroup.appendChild(tabs.write);
    tabGroup.appendChild(tabs.preview);
    bar.appendChild(spacer);
    bar.appendChild(tabGroup);
    bar.appendChild(statusNode);   // wraps to its own row, so it never pushes the tabs down

    function uploadStub(kind, label, iconClass) {
      var b = tbButton(label + ' (coming soon)', iconClass, function () {
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
      stubButtons.push({ kind: kind, button: b, label: label });
      if (uploaders[kind]) enableStub(stubButtons[stubButtons.length - 1]);
      return b;
    }

    // Ctrl/Cmd + B, I, K
    ta().addEventListener('keydown', function (event) {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
      var key = event.key.toLowerCase();
      if (key === 'b') { event.preventDefault(); makeBold(id); }
      else if (key === 'i') { event.preventDefault(); makeItalics(id); }
      else if (key === 'k') { event.preventDefault(); linkDialog(); }
    });

    self.bar = bar;
  }

  function enableStub(entry) {
    entry.button.disabled = false;
    entry.button.setAttribute('data-stub', '0');
    entry.button.title = 'Add ' + entry.label.toLowerCase();
    entry.button.setAttribute('aria-label', entry.button.title);
  }

  window.PostEditor = {
    // register('image' | 'audio' | 'video', function (ctx) {...}); ctx has
    // textarea, kind and insert(text) (puts text at the caret)
    register: function (kind, handler) {
      uploaders[kind] = handler;
      stubButtons.forEach(function (entry) { if (entry.kind === kind) enableStub(entry); });
    },
    version: 1
  };

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll('.post-toolbar[data-target]'), function (bar) {
      if (bar.getAttribute('data-ready')) return;
      if (!document.getElementById(bar.getAttribute('data-target'))) return;
      bar.setAttribute('data-ready', '1');
      new PostToolbar(bar);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
