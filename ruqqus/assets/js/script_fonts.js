/*
  Links the stylesheet of a Noto family only when the page holds text in its script (helpers/script_fonts.py).

  The site's typeface is Noto Sans; Arabic script, Urdu's Nastaliq, Hebrew, Bengali, Thai, Chinese, Japanese and
  Korean have Noto families of their own (assets/fonts/scripts/). Their faces are cut by unicode-range, so a browser
  fetches a font file only for text it draws, but the CJK stylesheets alone are about 30 KB each: a page in a Latin
  language should not pay for them. partials/theme.html links the interface language's own family in <head>; this
  script links the others once text in their script is on the page: at load, whenever something is added
  (the continuous feed, comments, chat, tooltips), when a closed <details> opens (the footer's language list) and
  when the reader types it.

  Which family is decided by the text's script and the language of the element around it (`lang`): Han in a
  Japanese post is Noto Sans JP, in a Chinese one Noto Sans SC; text in no CJK language is Noto Sans SC (Korean
  letters also Noto Sans KR), the first CJK family in the stack (_tokens.scss). Never a layout read: the walk looks
  at text and attributes only.
*/
(function () {
  "use strict";

  var ANY = /[֐-ࣿঀ-৿฀-๿ᄀ-ᇿ⺀-⿟⿰-鿿ꥠ-꥿가-퟿豈-﫿יִ-﷿︐-︟︰-﹏ﹰ-ﻼ＀-￯\uD82B\uD82C\uD840-\uD884]/;
  var ARABIC = /[؀-ۿݐ-ݿࡰ-ࣿﭐ-﷿ﹰ-ﻼ]/;
  var HEBREW = /[֐-׿יִ-ﭏ]/;
  var BENGALI = /[ঀ-৿]/;
  var THAI = /[฀-๿]/;
  var HANGUL = /[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿]/;
  var CJK = /[ᄀ-ᇿ⺀-⿟⿰-鿿ꥠ-꥿가-퟿豈-﫿︐-︟︰-﹏＀-￯]|[\uD82B\uD82C\uD840-\uD884][\uDC00-\uDFFF]/;

  function cjkFamily(lang) {
    if (/^ja(-|$)/.test(lang)) return "jp";
    if (/^ko(-|$)/.test(lang)) return "kr";
    if (/^(zh-(hant|tw|hk|mo)|yue)(-|$)/.test(lang)) return "tc";
    if (/^zh(-|$)/.test(lang)) return "sc";
    return null;
  }

  // The families `text` needs, written inside an element in `lang`.
  function familiesFor(text, lang) {
    var out = [];
    if (!text || !ANY.test(text)) return out;
    lang = String(lang || "").toLowerCase().replace(/_/g, "-");
    if (ARABIC.test(text)) out.push(/^ur(-|$)/.test(lang) ? "urdu" : "arabic");
    if (HEBREW.test(text)) out.push("hebrew");
    if (BENGALI.test(text)) out.push("bengali");
    if (THAI.test(text)) out.push("thai");
    if (CJK.test(text)) {
      var own = cjkFamily(lang);
      if (own) {
        out.push(own);
      } else {
        out.push("sc");
        if (HANGUL.test(text)) out.push("kr");
      }
    }
    return out;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { familiesFor: familiesFor };
    return;
  }

  var tag = document.currentScript;
  var sheets = {};
  try { sheets = JSON.parse((tag && tag.getAttribute("data-sheets")) || "{}"); } catch (e) { return; }
  var linked = {};
  var left = Object.keys(sheets).length;
  Array.prototype.forEach.call(document.querySelectorAll("link[data-script-font]"), function (link) {
    var key = link.getAttribute("data-script-font");
    if (sheets[key] && !linked[key]) { linked[key] = true; left -= 1; }
  });

  function link(key) {
    if (linked[key] || !sheets[key]) return;
    linked[key] = true;
    left -= 1;
    var el = document.createElement("link");
    el.rel = "stylesheet";
    el.href = sheets[key];
    el.setAttribute("data-script-font", key);
    document.head.appendChild(el);
  }

  function langOf(node) {
    var el = node.nodeType === 1 ? node : node.parentElement;
    var holder = el && el.closest("[lang]");
    return holder ? holder.getAttribute("lang") : "";
  }

  function note(text, node) {
    if (!text || !ANY.test(text)) return;
    familiesFor(text, langOf(node)).forEach(link);
  }

  // Text a reader can't see without opening it: the inside of a closed <details> (its summary shows).
  function shut(el) {
    var details = el.parentElement;
    return details && details.tagName === "DETAILS" && !details.open && el.tagName !== "SUMMARY";
  }

  // The options of a closed <select> (and a <datalist>) are not drawn until it opens: a list of languages in their
  // own scripts (Settings > Content) would otherwise fetch five families for a Latin page. A select's chosen option
  // is drawn, so it counts; the rest count once the select is opened (focus, a press: onOpen below).
  var SKIP = { SCRIPT: 1, STYLE: 1, TEMPLATE: 1, NOSCRIPT: 1, OPTION: 1, OPTGROUP: 1, DATALIST: 1 };

  function chosen(select) {
    var options = select.selectedOptions || [];
    for (var i = 0; i < options.length; i++) note(options[i].text, options[i]);
  }

  function walk(root) {
    if (!root || left <= 0) return;
    if (root.nodeType === 3) { note(root.data, root); return; }
    if (root.nodeType !== 1 || SKIP[root.tagName] || root.hidden || shut(root)) return;
    if (root.tagName === "SELECT") { chosen(root); return; }
    if (!ANY.test(root.textContent || "")) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (node.nodeType === 3) return NodeFilter.FILTER_ACCEPT;
        if (SKIP[node.tagName] || node.hidden || shut(node)) return NodeFilter.FILTER_REJECT;
        if (node.tagName === "SELECT") { chosen(node); return NodeFilter.FILTER_REJECT; }
        return NodeFilter.FILTER_SKIP;
      }
    });
    var node;
    while (left > 0 && (node = walker.nextNode())) note(node.data, node);
  }

  // Inside a closed <details> (not its summary): nothing to do until it opens.
  function hiddenInDetails(node) {
    var el = node.nodeType === 1 ? node : node.parentElement;
    for (; el && el !== document.body; el = el.parentElement) {
      if (SKIP[el.tagName] || shut(el)) return true;
    }
    return false;
  }

  function start() {
    walk(document.body);
    if (left <= 0 || typeof MutationObserver === "undefined") return;
    var observer = new MutationObserver(function (records) {
      for (var i = 0; i < records.length && left > 0; i++) {
        var r = records[i];
        if (r.type === "characterData") {
          if (!hiddenInDetails(r.target)) note(r.target.data, r.target);
          continue;
        }
        for (var j = 0; j < r.addedNodes.length && left > 0; j++) {
          if (!hiddenInDetails(r.addedNodes[j])) walk(r.addedNodes[j]);
        }
      }
      if (left <= 0) stop();
    });
    function onToggle(event) {
      var details = event.target;
      if (details && details.tagName === "DETAILS" && details.open) walk(details);
      if (left <= 0) stop();
    }
    function onInput(event) {
      var field = event.target;
      if (field && field.tagName === "SELECT") chosen(field);
      else if (field && typeof field.value === "string") note(field.value, field);
      if (left <= 0) stop();
    }
    // a select about to open: its options are about to be drawn
    function onOpen(event) {
      var select = event.target && event.target.closest && event.target.closest("select");
      if (select) {
        for (var i = 0; i < select.options.length && left > 0; i++) note(select.options[i].text, select.options[i]);
      }
      if (left <= 0) stop();
    }
    function stop() {
      observer.disconnect();
      document.removeEventListener("toggle", onToggle, true);
      document.removeEventListener("input", onInput, true);
      document.removeEventListener("focusin", onOpen, true);
      document.removeEventListener("pointerdown", onOpen, true);
    }
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    document.addEventListener("toggle", onToggle, true);
    document.addEventListener("input", onInput, true);
    document.addEventListener("focusin", onOpen, true);
    document.addEventListener("pointerdown", onOpen, true);
  }

  if (document.body && document.readyState !== "loading") start();
  else document.addEventListener("DOMContentLoaded", start);
})();
