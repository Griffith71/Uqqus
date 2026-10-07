/*
 * Side panels (desktop only, >= 992px): the navbar's Notifications, Chat and
 * Create post icons open their page in a panel that replaces the right sidebar
 * and folds the left sidebar into its rail, so you can use them while the feed
 * stays where it is and keeps scrolling.
 *
 * Each panel shows the real page in a frame with ?embed=1 (templates/default.html:
 * no navbar or sidebars). Frames are made when first opened and then only hidden,
 * so the chat keeps its connection. Below 992px, with a modifier key held, or
 * when already on that page, the icons are ordinary links.
 */
(function () {
  'use strict';

  var panel = document.getElementById('side-panel');
  var row = document.getElementById('main-content-row');
  if (!panel || !row) return;

  var PANELS = {
    notifications: { title: 'Notifications', src: '/notifications?embed=1', page: '/notifications', fresh: true },
    chat: { title: 'Chat', src: '/chat?embed=1', page: '/chat' },
    post: { title: 'Create post', src: '/composer?embed=1', page: '/submit' }
  };

  var wide = window.matchMedia('(min-width: 992px)');
  var body = document.getElementById('side-panel-body');
  var titleNode = document.getElementById('side-panel-title');
  var fullLink = document.getElementById('side-panel-full');
  var triggers = Array.prototype.slice.call(document.querySelectorAll('[data-side-panel]'));
  var frames = {};
  var current = null;
  var leftWasCollapsed = false;

  function leftSidebar() { return document.getElementById('sidebar-left'); }

  function frameFor(name) {
    if (!frames[name]) {
      var frame = document.createElement('iframe');
      frame.className = 'side-panel-frame';
      frame.title = PANELS[name].title;
      frame.src = PANELS[name].src;
      body.appendChild(frame);
      frames[name] = frame;
    } else if (PANELS[name].fresh && frames[name].hidden) {
      frames[name].contentWindow.location.replace(PANELS[name].src);   // what is new since it was last open
    }
    return frames[name];
  }

  function markActive(name) {
    triggers.forEach(function (t) { t.classList.toggle('panel-active', t.getAttribute('data-side-panel') === name); });
  }

  function open(name) {
    if (current === null) {
      // the panel takes the right sidebar's place; the left one folds into its rail (without
      // touching the saved preference, so closing restores what you had)
      var left = leftSidebar();
      if (left) {
        leftWasCollapsed = left.classList.contains('sidebar-collapsed');
        left.classList.add('sidebar-collapsed');
      }
      row.classList.add('panel-open');
      panel.hidden = false;
    }
    var frame = frameFor(name);
    Object.keys(frames).forEach(function (k) { frames[k].hidden = frames[k] !== frame; });
    current = name;
    titleNode.textContent = PANELS[name].title;
    fullLink.href = PANELS[name].page;
    markActive(name);
  }

  function close() {
    if (current === null) return;
    var left = leftSidebar();
    if (left && !leftWasCollapsed) left.classList.remove('sidebar-collapsed');
    row.classList.remove('panel-open');
    panel.hidden = true;
    current = null;
    markActive(null);
  }

  // the share sheet (share_chat.js) sends through this same chat frame, so there is only ever one chat
  // client on a page; made on first use, and shown only when the panel is opened
  window.RuqqusPanels = { chatFrame: function () { return frameFor('chat'); } };

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function (event) {
      var name = trigger.getAttribute('data-side-panel');
      if (!wide.matches || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (window.location.pathname === PANELS[name].page) return;      // already there: just the page
      event.preventDefault();
      if (current === name) close(); else open(name);
    });
  });

  document.getElementById('side-panel-close').addEventListener('click', close);
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape') close(); });
  (wide.addEventListener ? wide.addEventListener.bind(wide, 'change') : wide.addListener.bind(wide))(function () {
    if (!wide.matches) close();
  });

  // a theme switch (switch_css in all_js.js) changes the body's class: the frames already
  // open follow it (the choice is saved by then, so a reload renders them in the new theme)
  var wasDark = document.body.classList.contains('dark');
  new MutationObserver(function () {
    var isDark = document.body.classList.contains('dark');
    if (isDark === wasDark) return;
    wasDark = isDark;
    setTimeout(function () {
      Object.keys(frames).forEach(function (name) { frames[name].contentWindow.location.reload(); });
    }, 600);
  }).observe(document.body, { attributes: true, attributeFilter: ['class'] });

  // the notifications page, once it has marked things read, says how many are left
  window.addEventListener('message', function (event) {
    if (event.origin !== window.location.origin || !event.data || event.data.type !== 'ruqqus-notifications') return;
    var unread = Number(event.data.unread) || 0;
    triggers.forEach(function (t) {
      if (t.getAttribute('data-side-panel') !== 'notifications') return;
      var badge = t.querySelector('.badge-count');
      var icon = t.querySelector('.fa-bell');
      if (badge) { if (unread) badge.textContent = String(unread); else badge.remove(); }
      if (icon) icon.classList.toggle('text-danger', unread > 0);
    });
  });
})();
