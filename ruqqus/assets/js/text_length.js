// How long a piece of text is, fairly across scripts: the browser's copy of helpers/text_length.py (read its notes).
// A character counts 1 in U+0000-U+10FF and the general punctuation X keeps at 1, 2 everywhere else (CJK, kana,
// Hangul, full-width forms, emoji); the pieces that join onto a character count 0, and text is cut only between whole
// characters. tests/data/text_length_vectors.json is run against both copies (tests/test_text_length.py).
(function (root) {
  "use strict";

  var LIGHT = [[0x0000, 0x10FF], [0x2000, 0x200D], [0x2010, 0x201F], [0x2032, 0x2037]];
  var ZWJ = 0x200D;

  function joins(cp) {
    return (cp >= 0xFE00 && cp <= 0xFE0F) || (cp >= 0x1F3FB && cp <= 0x1F3FF) ||
      (cp >= 0xE0020 && cp <= 0xE007F) || cp === 0x20E3;
  }

  function flagHalf(cp) {
    return cp >= 0x1F1E6 && cp <= 0x1F1FF;
  }

  function weight(cp) {
    for (var i = 0; i < LIGHT.length; i++) {
      if (cp >= LIGHT[i][0] && cp <= LIGHT[i][1]) return 1;
    }
    return 2;
  }

  // [[the character's text, its weight], ...]
  function clusters(text) {
    var out = [];
    var joining = false;
    var chars = Array.from(String(text || "").normalize("NFC"));
    for (var i = 0; i < chars.length; i++) {
      var ch = chars[i];
      var cp = ch.codePointAt(0);
      var last = out.length ? out[out.length - 1] : null;
      var lastChars = last ? Array.from(last[0]) : [];
      if (last && (joining || joins(cp) || cp === ZWJ ||
          (flagHalf(cp) && lastChars.length === 1 && flagHalf(lastChars[0].codePointAt(0))))) {
        last[0] += ch;
        // a digit or symbol drawn as an emoji (VS16, a keycap, a skin tone) counts as the emoji it is
        if (cp === 0xFE0F || cp === 0x20E3 || (cp >= 0x1F3FB && cp <= 0x1F3FF)) last[1] = Math.max(last[1], 2);
        joining = cp === ZWJ;
        continue;
      }
      out.push([ch, weight(cp)]);
      joining = false;
    }
    return out;
  }

  function weighted(text) {
    var total = 0;
    var parts = clusters(text);
    for (var i = 0; i < parts.length; i++) total += parts[i][1];
    return total;
  }

  function clip(text, limit) {
    var kept = "";
    var total = 0;
    var parts = clusters(text);
    for (var i = 0; i < parts.length; i++) {
      if (total + parts[i][1] > limit) break;
      kept += parts[i][0];
      total += parts[i][1];
    }
    return kept;
  }

  // the note beside a field's limit, in the page's language (this file also runs under node, where there is no tr)
  var api = { weighted: weighted, clip: clip, clusters: clusters,
    NOTE: typeof tr === "function" ? tr("Chinese, Japanese and Korean characters and emoji count as 2")
      : "Chinese, Japanese and Korean characters and emoji count as 2" };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.TextLength = api;
})(typeof window !== "undefined" ? window : this);
