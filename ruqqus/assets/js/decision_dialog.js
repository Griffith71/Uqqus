// A guildmaster's decision that must say why (the owner, 2026-11-14): `decisionDialog({...})` opens #decisionModal (partials/decision_dialog.html) and posts the reason,
// the rule and the cited items to the route. Used by an exile (by name, from a post or comment's menu or the guild's Exiled page; by alias, "Anonymous #3") and a chat mute by alias.
//   title, ok, intro     the words (the menu's own, already translated)
//   url                  where to post (the route's own address, with ?toast=1 for the small answer)
//   fields               {name: value} the route also reads (thing, username)
//   evidence             true: the account's items in that guild are offered to cite (an exile by name needs at least one)
//   evidenceUrl, thing   where to ask for them, and the item (a fullname) the menu was opened on, already ticked
//   ask_username         true: the Exiled page's typed name; the items are asked for once it is typed
//   rule                 false to leave out the rule menu
// Everything is drawn with textContent: an item's words are a stranger's.
(function () {
  "use strict";
  var modal = document.getElementById("decisionModal");
  if (!modal) return;
  var form = document.getElementById("decisionForm");
  var $ = function (id) { return document.getElementById(id); };
  var el = {title: $("decisionTitle"), intro: $("decisionIntro"), userGroup: $("decisionUserGroup"), user: $("decisionUser"), find: $("decisionFind"),
            evidence: $("decisionEvidence"), items: $("decisionItems"), reason: $("decisionReason"), ruleGroup: $("decisionRuleGroup"),
            rule: $("decisionRule"), error: $("decisionError"), ok: $("decisionOk")};
  var current = null;
  var MAX = 5;

  function say(text) { el.error.textContent = text || ""; el.error.hidden = !text; }

  function row(item, checked) {
    var label = document.createElement("label");
    label.className = "d-flex align-items-start mb-1";
    var box = document.createElement("input");
    box.type = "checkbox"; box.className = "mr-2 mt-1"; box.value = item.id; box.checked = !!checked;
    box.setAttribute("data-decision-item", "1");
    var text = document.createElement("span");
    text.className = "text-small";
    text.textContent = (item.kind === "comment" ? "“" : "") + item.text + (item.kind === "comment" ? "”" : "");
    label.appendChild(box); label.appendChild(text);
    return label;
  }

  function offered(items) {
    el.items.textContent = "";
    var seen = {};
    if (current.thing) {
      var own = null;
      items.forEach(function (i) { if (i.id === current.thing) own = i; });
      el.items.appendChild(row(own || {id: current.thing, kind: "post", text: current.thingText || tr("The one you opened this on")}, true));
      seen[current.thing] = true;
    }
    items.forEach(function (i) { if (!seen[i.id]) el.items.appendChild(row(i, false)); });
    if (!el.items.children.length) {
      var none = document.createElement("div");
      none.className = "text-small text-muted";
      none.textContent = tr("They have no posts or comments here that can be cited.");
      el.items.appendChild(none);
    }
  }

  function load(username) {
    if (!current.evidenceUrl || !username) return;
    say("");
    var xhr = new XMLHttpRequest();
    xhr.open("GET", current.evidenceUrl + (current.evidenceUrl.indexOf("?") < 0 ? "?" : "&") + "username=" + encodeURIComponent(username.replace(/^@/, "")), true);
    xhr.withCredentials = true;
    xhr.onload = function () {
      var data = xhrData(xhr);
      if (xhrOk(xhr) && data && data.items) { el.evidence.hidden = false; offered(data.items); }
      else say(xhrError(xhr, data));
    };
    xhr.onerror = function () { say(tr("Something went wrong. Please try again.")); };
    xhr.send();
  }

  window.decisionDialog = function (opts) {
    current = opts || {};
    el.title.textContent = current.title || "";
    el.intro.textContent = current.intro || ""; el.intro.hidden = !current.intro;
    el.ok.textContent = current.ok || tr("Save");
    el.reason.value = ""; el.rule.value = "";
    el.ruleGroup.hidden = current.rule === false;
    el.userGroup.hidden = !current.ask_username;
    el.user.value = "";
    el.evidence.hidden = true; el.items.textContent = "";
    say("");
    el.ok.disabled = false;
    if (window.jQuery) window.jQuery(modal).modal("show"); else modal.classList.add("show");
    setTimeout(function () { (current.ask_username ? el.user : el.reason).focus(); }, 400);
    if (current.evidence && !current.ask_username) load((current.fields && current.fields.username) || "");
  };

  el.find.addEventListener("click", function () { load(el.user.value); });
  el.user.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); load(el.user.value); } });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!current) return;
    var reason = el.reason.value.trim();
    if (!reason) { say(tr("A reason is required.")); el.reason.focus(); return; }
    var data = new FormData();
    data.append("formkey", formkey());
    data.append("reason", reason);
    if (!el.ruleGroup.hidden && el.rule.value) data.append("rule", el.rule.value);
    var fields = current.fields || {};
    // in a dialog that asks what the decision relies on, the item the menu was opened on is sent only if it is still ticked (`thing` is a citation like the others)
    Object.keys(fields).forEach(function (k) { if (!(current.evidence && k === "thing")) data.append(k, fields[k]); });
    if (current.ask_username) {
      if (!el.user.value.trim()) { say(tr("Enter a username.")); el.user.focus(); return; }
      data.append("username", el.user.value.trim().replace(/^@/, ""));
    }
    if (current.evidence) {
      var ticked = Array.prototype.slice.call(el.items.querySelectorAll("input[data-decision-item]:checked")).map(function (b) { return b.value; });
      if (!ticked.length) { say(tr("Choose at least one post or comment this relies on.")); return; }
      if (ticked.length > MAX) { say(tr("Choose at most five.")); return; }
      ticked.forEach(function (id) { data.append("evidence", id); });
      if (fields.thing && ticked.indexOf(fields.thing) >= 0) data.append("thing", fields.thing);
    }
    el.ok.disabled = true; say("");
    var xhr = new XMLHttpRequest();
    xhr.open("POST", current.url, true);
    xhr.withCredentials = true;
    xhr.onload = function () {
      var answer = xhrData(xhr);
      if (xhr.status === 204 || (xhrOk(xhr) && (answer !== null || xhr.response === ""))) {
        if (window.jQuery) window.jQuery(modal).modal("hide");
        if (answer && answer.message) {
          window.jQuery("#toast-post-success").toast("dispose").toast("show");
          document.getElementById("toast-post-success-text").innerText = answer.message;
        }
        if (current.reload !== false) window.location.reload(true);
        return;
      }
      el.ok.disabled = false;
      say(xhrError(xhr, answer));
    };
    xhr.onerror = function () { el.ok.disabled = false; say(tr("Something went wrong. Please try again.")); };
    xhr.send(data);
  });
})();
