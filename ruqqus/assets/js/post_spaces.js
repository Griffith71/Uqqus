/*
 * The comment spaces of a post made for guilds (templates/partials/comment_spaces.html, routes/post_spaces.py).
 *
 * The bar lists the profile's own space (when the post has one), "All guilds" and each guild. Choosing a guild fetches
 * /post/<id>/space/<guild> and shows it under the bar; "All guilds" fetches /post/<id>/space/all (every guild's comments
 * in one list, read only). The profile's own comments are already on the page, so "On the profile" just shows them
 * again. The page's comment sort is passed along, and the choice is kept in the address (#space=name).
 *
 * The piece of page is made by the site from its own templates (the same HTML the page's comments are drawn with), so
 * it is put in with innerHTML, as the feed composer does with a new post's card.
 */
(function () {
  'use strict';

  var root = document.getElementById('comment-spaces');
  if (!root) return;
  var body = document.getElementById('space-fragment');
  var profile = document.getElementById('space-profile');
  var post = root.getAttribute('data-post');
  var sort = root.getAttribute('data-sort') || 'top';
  var tabs = Array.prototype.slice.call(root.querySelectorAll('.space-tab'));
  var token = 0;

  function mark(chosen) {
    tabs.forEach(function (tab) {
      var on = tab === chosen;
      tab.classList.toggle('active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  function find(name) {
    return tabs.filter(function (tab) { return tab.getAttribute('data-space') === name; })[0];
  }

  function show(name, quiet) {
    var tab = find(name);
    if (!tab) return;
    mark(tab);
    if (!quiet) {
      try { history.replaceState(null, '', name === 'profile' ? location.pathname + location.search : '#space=' + encodeURIComponent(name)); } catch (e) {}
    }
    if (name === 'profile') {
      body.hidden = true;
      if (profile) profile.hidden = false;
      return;
    }
    if (profile) profile.hidden = true;
    body.hidden = false;
    body.textContent = 'Loading…';
    var mine = ++token;
    fetch('/post/' + encodeURIComponent(post) + '/space/' + encodeURIComponent(name) + '?sort=' + encodeURIComponent(sort), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
      .then(function (html) {
        if (mine !== token) return;                      // another space was chosen meanwhile
        body.innerHTML = html;
        if (typeof register_votes === 'function') register_votes();
        if (window.jQuery) window.jQuery(body).find('[data-toggle="tooltip"]').tooltip();
      })
      .catch(function () {
        if (mine === token) body.textContent = 'These comments could not be loaded. Try again.';
      });
  }

  root.addEventListener('click', function (event) {
    var tab = event.target.closest ? event.target.closest('.space-tab') : null;
    if (!tab || !root.contains(tab)) return;
    show(tab.getAttribute('data-space'));
    var more = tab.closest('details');
    if (more) more.open = false;
  });

  var filter = root.querySelector('.space-filter');
  if (filter) {
    filter.addEventListener('input', function () {
      var wanted = filter.value.trim().toLowerCase().replace(/^\+/, '');
      Array.prototype.forEach.call(root.querySelectorAll('.space-more-list .space-tab'), function (tab) {
        tab.hidden = wanted !== '' && tab.getAttribute('data-space').toLowerCase().indexOf(wanted) === -1;
      });
    });
  }

  var chosen = /[#&]space=([^&]+)/.exec(location.hash);
  if (chosen) show(decodeURIComponent(chosen[1]), true);
})();
