/*
 * "Who upvoted" (routes/voters.py, helpers/voters.py).
 *
 * For the AUTHOR of a post or comment the score is a button (.has-voters, set by the templates only for
 * them). Clicking it, or Enter / Space on it, opens one small panel next to it with the people they follow
 * who follow them back and upvoted. Downvotes are never part of the answer. Escape, a click elsewhere or a
 * second click closes it. Names are drawn as text.
 */
(function () {
  'use strict';

  var panel = null;
  var anchor = null;
  var token = 0;                       // a late answer for a panel that has since been closed or reopened is ignored

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function build() {
    panel = make('div');
    panel.id = 'voters-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Who upvoted');
    panel.hidden = true;
    document.body.appendChild(panel);
  }

  function place() {
    var rect = anchor.getBoundingClientRect();
    var top = rect.top + window.pageYOffset - 8;
    var left = rect.right + window.pageXOffset + 10;
    var room = window.pageXOffset + window.innerWidth - panel.offsetWidth - 8;
    panel.style.top = top + 'px';
    panel.style.left = Math.max(window.pageXOffset + 8, Math.min(left, room)) + 'px';
  }

  function show(children) {
    panel.textContent = '';
    panel.appendChild(make('div', 'voters-title', 'Upvoted by'));
    children.forEach(function (child) { panel.appendChild(child); });
    place();
  }

  function row(person) {
    var link = make('a', 'voters-row');
    link.href = /^\//.test(person.permalink) ? person.permalink : '#';
    var avatar = make('img', 'voters-avatar');
    avatar.alt = '';
    avatar.width = avatar.height = 28;
    if (typeof person.avatar === 'string' && /^(https?:\/\/|\/)/.test(person.avatar)) avatar.src = person.avatar;
    link.appendChild(avatar);
    link.appendChild(make('span', 'voters-name', '@' + person.username));
    return link;
  }

  function draw(data) {
    var children = [];
    if (!data.people.length) {
      children.push(make('p', 'voters-empty', 'None of the people you follow back have upvoted this yet.'));
    } else {
      var list = make('div', 'voters-list');
      data.people.forEach(function (person) { list.appendChild(row(person)); });
      children.push(list);
    }
    children.push(make('p', 'voters-note', data.note || ''));
    show(children);
  }

  function failed(message) {
    var retry = make('button', 'btn btn-link btn-sm p-0 align-baseline', 'Try again');
    retry.type = 'button';
    retry.addEventListener('click', load);
    var line = make('p', 'voters-empty', message + ' ');
    line.appendChild(retry);
    show([line]);
  }

  function load() {
    var mine = ++token;
    show([make('p', 'voters-empty', 'Loading\u2026')]);
    fetch(anchor.getAttribute('data-voters-url'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (response) {
        if (response.status === 403) throw new Error('only the author');
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) { if (mine === token) draw(data); })
      .catch(function (error) {
        if (mine !== token) return;
        failed(error.message === 'only the author' ? 'Only the author can see this.' : 'Could not load the list.');
      });
  }

  function open(target) {
    if (!panel) build();
    anchor = target;
    // the score's own tooltip ("+3 | -1 - click to see who upvoted") would sit on top of the panel's title
    if (window.jQuery && jQuery.fn.tooltip) jQuery(target).closest('[data-toggle="tooltip"]').tooltip('hide');
    anchor.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
    load();
  }

  function close(returnFocus) {
    if (!panel || panel.hidden) return;
    token++;
    panel.hidden = true;
    if (anchor) {
      anchor.setAttribute('aria-expanded', 'false');
      if (returnFocus) anchor.focus();
    }
    anchor = null;
  }

  function toggle(target) {
    if (anchor === target && panel && !panel.hidden) close(false);
    else { close(false); open(target); }
  }

  document.addEventListener('click', function (event) {
    var target = event.target.closest ? event.target.closest('.has-voters') : null;
    if (target) {
      event.preventDefault();
      toggle(target);
      return;
    }
    if (panel && !panel.hidden && !panel.contains(event.target)) close(false);
  });

  document.addEventListener('keydown', function (event) {
    var target = event.target.closest ? event.target.closest('.has-voters') : null;
    if (target && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      toggle(target);
    } else if (event.key === 'Escape') {
      close(true);
    }
  });
})();
