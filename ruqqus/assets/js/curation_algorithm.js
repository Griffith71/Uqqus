/*
 * The "Algorithm" section of the curation form (templates/curations/algorithm_form.html).
 *
 * The section is an ordinary form and saves without this script. The script adds:
 * - "Start from": ready-made algorithms that fill the fields (each button carries its
 *   spec, made by feed_algorithm.preset on the server);
 * - the number beside each slider, and the mix settings only while "Your own mix" is chosen;
 * - Preview: posts the fields to the form's data-preview address and shows what the
 *   algorithm does in plain language and the first posts it would show. Nothing is saved.
 * - "Who decides": the rules, or an outside feed server (then only its address is shown).
 */
(function () {
  'use strict';

  var form = document.getElementById('curation-algorithm');
  if (!form) return;

  var rank = document.getElementById('alg-rank');
  var mixPanel = document.getElementById('alg-mix');
  var summary = document.getElementById('alg-summary');
  var summaryTitle = document.getElementById('alg-summary-title');
  var preview = document.getElementById('alg-preview');
  var previewButton = document.getElementById('alg-preview-button');
  var MIX = ['votes', 'comments', 'fade', 'members', 'media', 'variety'];

  function showMix() { mixPanel.hidden = rank.value !== 'mix'; }

  function drawSliders() {
    Array.prototype.forEach.call(form.querySelectorAll('input[type="range"]'), function (slider) {
      var out = document.getElementById(slider.id + '-value');
      if (out) out.textContent = slider.value;
    });
  }

  // --- the rules, or an outside feed server
  var rulesPanel = document.getElementById('alg-rules');
  var serverPanel = document.getElementById('alg-server');

  function mode() {
    var picked = form.querySelector('input[name="mode"]:checked');
    return picked ? picked.value : 'rules';
  }

  function showMode() {
    if (!serverPanel) return;
    serverPanel.hidden = mode() !== 'server';
    rulesPanel.hidden = mode() === 'server';
  }

  form.addEventListener('change', function (event) {
    if (event.target && event.target.name === 'mode') showMode();
  });
  showMode();

  rank.addEventListener('change', showMix);
  form.addEventListener('input', function (event) {
    if (event.target && event.target.type === 'range') drawSliders();
  });
  showMix();
  drawSliders();

  // --- ready-made starting points
  function setChecks(name, values) {
    Array.prototype.forEach.call(form.querySelectorAll('input[name="' + name + '"]'), function (box) {
      box.checked = values.indexOf(box.value) !== -1;
    });
  }

  function fill(spec) {
    setChecks('mode', ['rules']);
    showMode();
    setChecks('source', [spec.source]);
    form.elements.age.value = spec.age;
    form.elements.any_words.value = spec.any.join(', ');
    form.elements.all_words.value = spec.all.join(', ');
    form.elements.none_words.value = spec.none.join(', ');
    setChecks('kinds', spec.kinds);
    form.elements.only_sites.value = spec.only_sites.join(', ');
    form.elements.never_sites.value = spec.never_sites.join(', ');
    form.elements.min_votes.value = spec.min_votes;
    form.elements.min_comments.value = spec.min_comments;
    setChecks('hide', spec.hide);
    rank.value = spec.rank;
    MIX.forEach(function (name) { form.elements['mix_' + name].value = spec.mix[name]; });
    showMix();
    drawSliders();
  }

  var presets = document.getElementById('alg-presets');
  if (presets) {
    presets.hidden = false;
    presets.addEventListener('click', function (event) {
      var button = event.target.closest ? event.target.closest('.alg-preset') : null;
      if (!button) return;
      try { fill(JSON.parse(button.getAttribute('data-spec'))); } catch (e) { return; }
      runPreview();
    });
  }

  // --- preview
  function drawSummary(lines, title) {
    summaryTitle.textContent = title;
    summary.textContent = '';
    (lines || []).forEach(function (line) {
      var item = document.createElement('li');
      item.textContent = line;
      summary.appendChild(item);
    });
  }

  function say(message, isError) {
    preview.textContent = '';
    var note = document.createElement('p');
    note.className = 'text-small mb-0 ' + (isError ? 'text-danger' : 'text-muted');
    note.textContent = message;
    preview.appendChild(note);
  }

  function runPreview() {
    if (!form.dataset.preview) return;
    previewButton.disabled = true;
    say('Working it out…', false);
    fetch(form.dataset.preview, { method: 'POST', body: new FormData(form), credentials: 'same-origin' })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (answer) {
        var data = answer.data || {};
        if (data.summary) drawSummary(data.summary, 'What it would do (not saved yet)');
        if (!answer.ok) { say(data.error || 'Could not preview that. Try again.', true); return; }
        preview.innerHTML = data.html;
        if (data.note) {
          var note = document.createElement('p');
          note.className = 'text-small text-muted';
          note.textContent = data.note;
          preview.insertBefore(note, preview.firstChild);
        }
        if (typeof bindPostCards === 'function') bindPostCards(preview);
      })
      .catch(function () { say('Could not preview that. Try again.', true); })
      .then(function () { previewButton.disabled = false; });
  }

  previewButton.hidden = false;
  previewButton.addEventListener('click', runPreview);
})();
