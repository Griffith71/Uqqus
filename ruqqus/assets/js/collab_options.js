/*
 * The post options of a shared draft (templates/collab/editor.html): a link, who can comment, content disclosure,
 * sensitive, and the guilds to forward to. They are kept with the draft (POST /api/collab/<id>/options, any author,
 * the last save wins) and used when it is published. Text only: nothing here writes HTML.
 *
 * Loaded with `defer`, so Create a post's own script (forwardGuilds, renderForwardGuildChips in submit.html) has run.
 * The forwarding chips are that script's; they keep one hidden `forward_guilds` field per guild in #forward_guild_inputs.
 */
(function () {
  var box = document.getElementById('collab-options');
  if (!box) return;
  var status = document.getElementById('collab-options-status');
  var linkBtn = document.getElementById('collab-link-btn');
  var linkRow = document.getElementById('collab-link-row');
  var url = document.getElementById('collab-url');
  var timer = null;
  var saving = false;
  var again = false;

  if (typeof renderForwardGuildChips === 'function') renderForwardGuildChips();

  function say(text, bad) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('text-danger', !!bad);
  }

  function fields() {
    var form = new FormData();
    form.append('formkey', typeof formkey === 'function' ? formkey() : '');
    form.append('url', url ? url.value.trim() : '');
    var picked = box.querySelector('input[name="comment_permission"]:checked');
    form.append('comment_permission', picked ? picked.value : '0');
    Array.prototype.forEach.call(box.querySelectorAll('input[name="paid_partnership"], input[name="made_with_ai"]'), function (input) {
      if (input.type === 'hidden' || input.checked) form.append(input.name, input.type === 'hidden' ? '' : input.value);
    });
    if (box.querySelector('input[name="sensitive"]:checked')) form.append('sensitive', 'on');
    Array.prototype.forEach.call(box.querySelectorAll('#forward_guild_inputs input[name="forward_guilds"]'), function (input) {
      form.append('forward_guilds', input.value);
    });
    return form;
  }

  function save() {
    if (saving) { again = true; return; }
    saving = true;
    say('Saving options…');
    fetch(box.getAttribute('data-url'), { method: 'POST', credentials: 'same-origin', body: fields(), headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        saving = false;
        if (res.ok) say('Options saved.'); else say(res.data.error || 'The options couldn’t be saved.', true);
        if (again) { again = false; save(); }
      })
      .catch(function () { saving = false; say('The options couldn’t be saved. Check your connection.', true); });
  }

  function later() {
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  }

  box.addEventListener('input', later);
  box.addEventListener('change', later);
  // the forwarding chips are redrawn by script (no input event): look at the list when a chip is added or removed
  var chips = document.getElementById('forward_guild_inputs');
  if (chips && window.MutationObserver) new MutationObserver(later).observe(chips, { childList: true });

  if (linkBtn && linkRow) {
    linkBtn.addEventListener('click', function () {
      var show = linkRow.hidden;
      linkRow.hidden = !show;
      linkBtn.setAttribute('aria-expanded', show ? 'true' : 'false');
      if (show && url) url.focus();
    });
  }
})();
