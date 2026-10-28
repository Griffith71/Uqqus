// The interface language for the page's scripts (partials/i18n_boot.html sets window.I18N first; docs/i18n.md).
// window.tr("Text with %(name)s", {name: x}) and window.trn(singular, plural, n, {...}) (%(num)s is n) answer from
// I18N.messages, the reader's language's table (assets/i18n/<key>.js, made from the js catalogue by
// scripts/i18n_catalogs.py compile-js), else the English. Every string is a format string: a literal percent sign is
// %%. Format dates and numbers with I18N.intl, never the browser's own language (the page is in the interface's).
// The pseudo-locales (I18N_PSEUDO=1) accent every string, so unmarked text stands out.
(function () {
  "use strict";
  var I = window.I18N || (window.I18N = {lang: "en", intl: "en", dir: "ltr", pseudo: false});
  I.messages = I.messages || null;
  I.plural = I.plural || null;
  var plain = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  var accented = "àƀçđéƒĝĥîĵķļɱñöþǫŕšţûṽŵẋýžÀƁÇĐÉƑĜĤÎĴĶĻṀÑÖÞǪŔŠŢÛṼŴẊÝŽ";

  function pseudo(text) {
    var out = "", i = 0;
    while (i < text.length) {
      var m = /^%(\([^)]*\))?[sd%]/.exec(text.slice(i));
      if (m) { out += m[0]; i += m[0].length; continue; }
      var at = plain.indexOf(text[i]);
      out += at === -1 ? text[i] : accented[at];
      i += 1;
    }
    var body = "[" + out + " " + new Array(Math.max(1, Math.floor(text.length / 3)) + 1).join("·") + "]";
    return I.dir === "rtl" ? "\u202b" + body + "\u202c" : body;
  }

  function format(text, params) {
    params = params || {};
    return text.replace(/%(\(([^)]*)\))?([sd%])/g, function (all, named, name, kind) {
      if (kind === "%") return "%";
      if (!named) return all;
      return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : all;
    });
  }

  function look(key) {
    var table = I.messages;
    return table && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : null;
  }

  window.tr = function (message, params) {
    var found = look(message);
    var text = typeof found === "string" && found ? found : (I.pseudo ? pseudo(message) : message);
    return format(text, params);
  };

  window.trn = function (singular, plural, n, params) {
    var all = Object.assign({num: n}, params || {});
    var found = look(singular + "\u0000" + plural);
    var index = I.plural ? Number(I.plural(n)) : (n === 1 ? 0 : 1);
    var text = Array.isArray(found) && found[index] ? found[index] : (n === 1 ? singular : plural);
    if (!found && I.pseudo) text = pseudo(text);
    return format(text, all);
  };
})();
