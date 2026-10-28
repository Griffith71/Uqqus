// The Translate button (partials/translate_button.html; the route is routes/translation_pages.py). Pressing it asks
// the site for a translation of the post or comment and puts it IN PLACE of the text (the owner, 2026-10-24): the
// parts its template marks with data-translate-part ("title", "snippet", "body") inside its own card (a post) or
// .comment-main (a comment), found from the row, never by id, since a post can be on a page twice and a comment can
// be drawn again. The originals are moved aside, not copied, so pressing "Show original" puts back the very same
// nodes (their links, players and anything bound to them), and the two versions swap without asking again.
// The text arrives as the site's own sanitized HTML and is parsed with DOMParser and moved in node by node (never innerHTML); the title and the first lines arrive as
// text. Nothing happens until a member presses it.
(function () {
  "use strict";

  var versions = new WeakMap();      // a part -> {original, translated, showing}
  var rows = new WeakMap();          // a row -> the last answer
  var asking = new WeakMap();        // a row -> the request on its way (reset forgets it, so its answer is dropped)

  function formKey() {
    if (typeof formkey === "function") return formkey();
    var input = document.querySelector('input[name="formkey"]');
    return input ? input.value : "";
  }

  function scopeOf(row) {
    return row.closest(row.dataset.translateKind === "post" ? '.card[id^="post-"]' : ".comment-main");
  }

  function partsOf(row) {
    var scope = scopeOf(row);
    return scope ? Array.prototype.slice.call(scope.querySelectorAll("[data-translate-part]")) : [];
  }

  function isText(part) {
    var kind = part.getAttribute("data-translate-part");
    return kind === "title" || kind === "snippet";
  }

  // what a part shows now, moved out of it (a body's nodes into a fragment; a title's or snippet's text)
  function takeOut(part) {
    var version = { lang: part.getAttribute("lang") };
    if (isText(part)) {
      version.text = part.textContent;
    } else {
      version.nodes = document.createDocumentFragment();
      while (part.firstChild) version.nodes.appendChild(part.firstChild);
    }
    return version;
  }

  function putIn(part, version) {
    if (isText(part)) {
      part.textContent = version.text;
    } else {
      part.replaceChildren();
      part.appendChild(version.nodes);
    }
    if (version.lang) part.setAttribute("lang", version.lang);
    else part.removeAttribute("lang");
  }

  function decoded(escaped) {
    // the title comes escaped ("&amp;"): read it as text
    return new DOMParser().parseFromString("<p>" + (escaped || "") + "</p>", "text/html").body.textContent;
  }

  function translatedFor(part, data) {
    var kind = part.getAttribute("data-translate-part");
    if (kind === "title") return data.title ? { text: decoded(data.title), lang: data.to_code } : null;
    if (kind === "snippet") return data.snippet ? { text: data.snippet, lang: data.to_code } : null;
    if (!data.html) return null;
    var nodes = document.createDocumentFragment();
    var parsed = new DOMParser().parseFromString(data.html, "text/html").body;
    while (parsed.firstChild) nodes.appendChild(document.adoptNode(parsed.firstChild));
    return { nodes: nodes, lang: data.to_code };
  }

  // A long comment is clamped to its first lines (comments.html .comment-clamp, all_js.js settleCommentClamps): with
  // other text in it, it is measured again. One that now fits is drawn whole and its "Show more" hidden, not removed,
  // since the other version may need them back. One the reader opened, or that isn't on screen, is left as it is.
  function settle(part) {
    if (!part.classList.contains("comment-clamp") || part.offsetParent === null) return;
    var button = document.getElementById(part.id.replace("comment-clamp-", "comment-overflow-toggle-"));
    if (!button || button.classList.contains("is-open")) return;
    part.classList.add("is-clamped");
    var fits = part.scrollHeight <= part.clientHeight + 2;
    part.classList.toggle("is-clamped", !fits);
    button.hidden = fits;
  }

  // back to the clamp as the page drew it, for its own measuring (settleCommentClamps) once new text is in
  function unsettle(part) {
    if (!part.classList.contains("comment-clamp")) return;
    var button = document.getElementById(part.id.replace("comment-clamp-", "comment-overflow-toggle-"));
    if (!button || !button.hidden) return;
    button.hidden = false;
    part.classList.add("is-clamped");
  }

  function show(row, which) {
    partsOf(row).forEach(function (part) {
      var state = versions.get(part);
      if (!state || state.showing === which || !state[which]) return;
      var current = takeOut(part);
      state[state.showing] = current;
      putIn(part, state[which]);
      state.showing = which;
      settle(part);
    });
    setRow(row, which);
  }

  function setRow(row, which, message) {
    var data = rows.get(row);
    var label = row.querySelector(".translate-btn-label");
    var status = row.querySelector(".translate-status");
    if (which === "translated" && data) {
      label.textContent = tr("Show original");
      // Translators: %(language)s is the language the text was written in, %(service)s the translator (LibreTranslate)
      status.textContent = message || tr("Translated from %(language)s by %(service)s", {language: data.from, service: data.engine_label});
    } else {
      // Translators: a button that translates a post or a comment (a verb)
      label.textContent = data ? tr("Show translation") : tr("Translate");
      status.textContent = message || "";
    }
    row.classList.toggle("is-translated", which === "translated");
    row.classList.toggle("is-error", Boolean(message));
  }

  function apply(row, data) {
    rows.set(row, data);
    partsOf(row).forEach(function (part) {
      var translated = translatedFor(part, data);
      if (!translated) return;
      var state = versions.get(part);
      if (!state) {
        state = { original: takeOut(part), translated: null, showing: "original" };
        versions.set(part, state);
        putIn(part, translated);
      } else {
        state.translated = translated;
        state.original = takeOut(part);
        putIn(part, translated);
      }
      state.translated = null;
      state.showing = "translated";
      settle(part);
    });
    setRow(row, "translated");
  }

  // while a request is on its way the row is busy: presses are ignored, but the button stays focusable
  // (aria-disabled, not disabled, which would drop the keyboard's focus to the page)
  function setBusy(row, busy) {
    row.classList.toggle("is-busy", busy);
    var button = row.querySelector(".translate-btn");
    if (busy) button.setAttribute("aria-disabled", "true");
    else button.removeAttribute("aria-disabled");
  }

  function ask(row) {
    var body = new URLSearchParams();
    body.set("formkey", formKey());
    var request = {};
    asking.set(row, request);
    setBusy(row, true);
    row.querySelector(".translate-status").textContent = tr("Translating…");
    function current() { return asking.get(row) === request; }
    fetch("/api/translate/" + row.dataset.translateKind + "/" + row.dataset.translateId, {
      method: "POST", body: body, credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" }
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
    }).then(function (answer) {
      if (!current()) return;                // the text was redrawn meanwhile (Translate.reset): this answer is stale
      if (answer.ok) apply(row, answer.data);
      else setRow(row, currentOf(row), answer.data.error || tr("Translation isn't available right now."));
    }).catch(function () {
      if (current()) setRow(row, currentOf(row), tr("Translation isn't available right now."));
    }).finally(function () {
      if (!current()) return;
      asking.delete(row);
      setBusy(row, false);
    });
  }

  function currentOf(row) {
    var showing = "original";
    partsOf(row).forEach(function (part) {
      var state = versions.get(part);
      if (state && state.showing === "translated") showing = "translated";
    });
    return showing;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".translate-btn");
    var row = button && button.closest(".translate-row");
    if (!row) return;
    event.preventDefault();
    event.stopPropagation();                  // a feed card's own link must not open the post
    if (row.classList.contains("is-busy")) return;
    if (!rows.get(row)) { ask(row); return; }
    show(row, currentOf(row) === "translated" ? "original" : "translated");
  });

  // the row partials/translate_button.html draws, for text that only became translatable after the page was drawn
  function makeRow(kind, id) {
    var row = document.createElement("div");
    row.className = "translate-row";
    row.dataset.translateKind = kind;
    row.dataset.translateId = id;
    // in the interface's language and direction, as the partial draws it
    row.setAttribute("lang", I18N.lang);
    row.setAttribute("dir", I18N.dir);
    var status = document.createElement("span");
    status.className = "translate-status";
    status.setAttribute("aria-live", "polite");
    var button = document.createElement("button");
    button.type = "button";
    button.className = "translate-btn";
    var icon = document.createElement("i");
    icon.className = "fas fa-language";
    icon.setAttribute("aria-hidden", "true");
    var label = document.createElement("span");
    label.className = "translate-btn-label";
    // Translators: a button that translates a post or a comment (a verb)
    label.textContent = tr("Translate");
    button.append(icon, label);
    row.append(status, button);
    return row;
  }

  // For code that redraws a post's or a comment's text (a comment's edit, all_js.js comment_edit): puts the originals
  // back and forgets the translations inside `scope` (and any answer still on its way), and shows or hides its rows
  // (`offered`, when the server says). With `kind`, `id` and `after`, a row is made after `after` when the text has
  // become translatable and `scope` has none.
  window.Translate = {
    reset: function (scope, options) {
      if (!scope) return;
      options = options || {};
      scope.querySelectorAll("[data-translate-part]").forEach(function (part) {
        var state = versions.get(part);
        if (state && state.showing === "translated") putIn(part, state.original);
        versions.delete(part);
        unsettle(part);
      });
      var found = scope.querySelectorAll(".translate-row");
      if (!found.length && options.offered === true && options.kind && options.id && options.after) {
        options.after.insertAdjacentElement("afterend", makeRow(options.kind, options.id));
        found = scope.querySelectorAll(".translate-row");
      }
      found.forEach(function (row) {
        asking.delete(row);
        setBusy(row, false);
        rows.delete(row);
        setRow(row, "original");
        if (typeof options.offered === "boolean") row.hidden = !options.offered;
      });
    }
  };
})();
