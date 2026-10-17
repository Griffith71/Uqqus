/*
 * Operational transformation for shared drafts: the browser's copy of ruqqus/helpers/collab_ot.py. Both are checked
 * against tests/data/collab_ot_vectors.json, so keep them in step.
 *
 * An op covers a whole field: a positive number keeps that many units, a string inserts it, a negative number deletes
 * that many units. Units are UTF-16 code units (a JavaScript string's length), the same as the server counts.
 * transform(a, b) -> [a', b'], a's inserts first at the same place.
 */
(function (root) {
  function isRetain(c) { return typeof c === 'number' && c > 0; }
  function isDelete(c) { return typeof c === 'number' && c < 0; }
  function isInsert(c) { return typeof c === 'string' && c !== ''; }

  function normalise(op) {
    var out = [];
    for (var i = 0; i < op.length; i++) {
      var c = op[i];
      if (c === 0 || c === '') continue;
      var last = out[out.length - 1];
      if (out.length && isInsert(c) && isInsert(last)) {
        out[out.length - 1] = last + c;
      } else if (out.length && isInsert(c) && isDelete(last)) {
        if (out.length >= 2 && isInsert(out[out.length - 2])) out[out.length - 2] += c;
        else out.splice(out.length - 1, 0, c);
      } else if (out.length && isRetain(c) && isRetain(last)) {
        out[out.length - 1] = last + c;
      } else if (out.length && isDelete(c) && isDelete(last)) {
        out[out.length - 1] = last + c;
      } else {
        out.push(c);
      }
    }
    return out;
  }

  function baseLength(op) {
    var n = 0;
    op.forEach(function (c) { if (typeof c === 'number') n += Math.abs(c); });
    return n;
  }

  function targetLength(op) {
    var n = 0;
    op.forEach(function (c) { if (isRetain(c)) n += c; else if (isInsert(c)) n += c.length; });
    return n;
  }

  function isNoop(op) { return op.every(isRetain); }

  function apply(text, op) {
    if (baseLength(op) !== text.length) throw new Error("the op doesn't fit the text");
    var out = [], pos = 0;
    op.forEach(function (c) {
      if (isRetain(c)) { out.push(text.slice(pos, pos + c)); pos += c; }
      else if (isDelete(c)) { pos -= c; }
      else { out.push(c); }
    });
    return out.join('');
  }

  function shrink(c, n) {
    if (c > 0) return c > n ? c - n : null;
    return -c > n ? c + n : null;
  }

  function reader(op) {
    var i = 0;
    return { cur: op.length ? op[0] : null, next: function () { i += 1; this.cur = i < op.length ? op[i] : null; } };
  }

  function transform(a, b) {
    if (baseLength(a) !== baseLength(b)) throw new Error('the ops are for different texts');
    var a2 = [], b2 = [], ia = reader(a), ib = reader(b), x = ia.cur, y = ib.cur, n;
    while (x !== null || y !== null) {
      if (typeof x === 'string') { a2.push(x); b2.push(x.length); ia.next(); x = ia.cur; continue; }
      if (typeof y === 'string') { a2.push(y.length); b2.push(y); ib.next(); y = ib.cur; continue; }
      if (x === null || y === null) throw new Error('the ops are for different texts');
      if (isRetain(x) && isRetain(y)) { n = Math.min(x, y); a2.push(n); b2.push(n); }
      else if (isDelete(x) && isDelete(y)) { n = Math.min(-x, -y); }
      else if (isDelete(x) && isRetain(y)) { n = Math.min(-x, y); a2.push(-n); }
      else { n = Math.min(x, -y); b2.push(-n); }
      x = shrink(x, n); y = shrink(y, n);
      if (x === null) { ia.next(); x = ia.cur; }
      if (y === null) { ib.next(); y = ib.cur; }
    }
    return [normalise(a2), normalise(b2)];
  }

  function compose(a, b) {
    if (targetLength(a) !== baseLength(b)) throw new Error("the ops don't follow each other");
    var out = [], ia = reader(a), ib = reader(b), x = ia.cur, y = ib.cur, n;
    while (x !== null || y !== null) {
      if (isDelete(x)) { out.push(x); ia.next(); x = ia.cur; continue; }
      if (typeof y === 'string') { out.push(y); ib.next(); y = ib.cur; continue; }
      if (x === null || y === null) throw new Error("the ops don't follow each other");
      if (isRetain(x) && isRetain(y)) {
        n = Math.min(x, y); out.push(n); x = shrink(x, n); y = shrink(y, n);
      } else if (typeof x === 'string' && isDelete(y)) {
        n = Math.min(x.length, -y); x = x.slice(n) || null; y = shrink(y, n);
      } else if (typeof x === 'string' && isRetain(y)) {
        n = Math.min(x.length, y); out.push(x.slice(0, n)); x = x.slice(n) || null; y = shrink(y, n);
      } else {
        n = Math.min(x, -y); out.push(-n); x = shrink(x, n); y = shrink(y, n);
      }
      if (x === null) { ia.next(); x = ia.cur; }
      if (y === null) { ib.next(); y = ib.cur; }
    }
    return normalise(out);
  }

  function transformIndex(op, index) {
    var pos = 0, out = index;
    for (var i = 0; i < op.length; i++) {
      var c = op[i];
      if (pos > index) break;
      if (isRetain(c)) pos += c;
      else if (isInsert(c)) out += c.length;
      else { out -= Math.min(-c, index - pos); pos -= c; }
    }
    return out;
  }

  function isHigh(code) { return code >= 0xD800 && code <= 0xDBFF; }
  function isLow(code) { return code >= 0xDC00 && code <= 0xDFFF; }

  // `cursor`: the caret in newText after the edit. What follows it was already there, so the kept end stops at it
  // and a typed letter equal to its neighbour is credited where it was typed ("word|start" -> "words|start").
  function diff(oldText, newText, cursor) {
    var la = oldText.length, lb = newText.length, start = 0, end = 0;
    var maxEnd = (cursor === undefined || cursor === null) ? Math.min(la, lb) : Math.max(0, Math.min(la, lb, lb - cursor));
    while (end < maxEnd && oldText.charCodeAt(la - end - 1) === newText.charCodeAt(lb - end - 1)) end++;
    if (end && isLow(oldText.charCodeAt(la - end))) end--;
    while (start < la - end && start < lb - end && oldText.charCodeAt(start) === newText.charCodeAt(start)) start++;
    if (start && isHigh(oldText.charCodeAt(start - 1))) start--;
    return normalise([start, newText.slice(start, lb - end), -(la - start - end), end]);
  }

  var api = { normalise: normalise, baseLength: baseLength, targetLength: targetLength, isNoop: isNoop, apply: apply,
              transform: transform, compose: compose, transformIndex: transformIndex, diff: diff };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CollabOT = api;
})(typeof window !== 'undefined' ? window : this);
