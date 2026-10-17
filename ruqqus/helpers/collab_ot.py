"""Operational transformation for shared drafts (pure, no I/O; the browser's copy is assets/js/collab_ot.js and both
are checked against the same vectors, tests/data/collab_ot_vectors.json).

An op changes one plain-text field and covers all of it, as ot.js does: a list whose items are
    a positive int n   keep the next n units,
    a string s         insert s here,
    a negative int -n  delete the next n units.
Lengths are **UTF-16 code units**, the unit a browser's textarea counts in, so a position means the same thing on
both sides. An op that would split a surrogate pair (half an emoji) leaves text that can't be decoded and is refused.

`transform(a, b)` takes two ops made on the same text and gives (a', b') so that apply(apply(s, a), b') ==
apply(apply(s, b), a'). When both insert at the same place, **a's insert goes first** (ot.js's rule); the server and
the browser both call it with the op being brought in first, so every copy ends the same.
"""

MAX_ITEMS = 5000           # parts in one op
MAX_INSERT = 25_000        # units one op may insert


class OpError(ValueError):
    """The op is malformed, doesn't fit the text, or would leave text that can't be decoded."""


def units(s):
    """Length of `s` in UTF-16 code units. A lone surrogate (half a pair) is refused."""
    try:
        return len(s.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise OpError("broken text")


def _is_retain(c):
    return isinstance(c, int) and not isinstance(c, bool) and c > 0


def _is_delete(c):
    return isinstance(c, int) and not isinstance(c, bool) and c < 0


def _is_insert(c):
    return isinstance(c, str) and c != ""


def check(op):
    """`op` as a normalised list, or OpError. Used on everything a browser sends."""
    if not isinstance(op, list) or len(op) > MAX_ITEMS:
        raise OpError("not an op")
    inserted = 0
    for c in op:
        if not (_is_retain(c) or _is_delete(c) or _is_insert(c)):
            raise OpError("not an op")
        if isinstance(c, str):
            inserted += units(c)
    if inserted > MAX_INSERT:
        raise OpError("too much at once")
    return normalise(op)


def normalise(op):
    """Merge neighbours of a kind and put an insert before a delete at the same place (ot.js's canonical form)."""
    out = []
    for c in op:
        if c == 0 or c == "":
            continue
        if out and isinstance(c, str) and isinstance(out[-1], str):
            out[-1] += c
        elif out and isinstance(c, str) and _is_delete(out[-1]):
            # keep inserts before deletes: "delete then insert" and "insert then delete" are the same change
            if len(out) >= 2 and isinstance(out[-2], str):
                out[-2] += c
            else:
                out.insert(len(out) - 1, c)
        elif out and _is_retain(c) and _is_retain(out[-1]):
            out[-1] += c
        elif out and _is_delete(c) and _is_delete(out[-1]):
            out[-1] += c
        else:
            out.append(c)
    return out


def base_length(op):
    return sum(c if _is_retain(c) else -c for c in op if not isinstance(c, str))


def target_length(op):
    return sum(c if _is_retain(c) else units(c) for c in op if not _is_delete(c))


def is_noop(op):
    return all(_is_retain(c) for c in op)


def apply(text, op):
    """The text after `op`. OpError when it doesn't fit or would split a character."""
    try:
        data = text.encode("utf-16-le")
    except UnicodeEncodeError:
        raise OpError("broken text")
    if base_length(op) != len(data) // 2:
        raise OpError("the op doesn't fit the text")
    parts, pos = [], 0
    for c in op:
        if _is_retain(c):
            parts.append(data[pos * 2:(pos + c) * 2])
            pos += c
        elif _is_delete(c):
            pos += -c
        else:
            parts.append(c.encode("utf-16-le"))
    try:
        return b"".join(parts).decode("utf-16-le")
    except UnicodeDecodeError:
        raise OpError("the op splits a character")


def _uslice(s, start, end=None):
    """`s` between two UTF-16 positions (surrogates kept whole when they are; a split shows up as broken text later)."""
    data = s.encode("utf-16-le", "surrogatepass")
    end = len(data) // 2 if end is None else end
    return data[start * 2:end * 2].decode("utf-16-le", "surrogatepass")


def _ulen(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


class _Reader:
    """Walks an op a piece at a time (ot.js's iterator)."""

    def __init__(self, op):
        self.op, self.i, self.cur = op, 0, None
        self._next()

    def _next(self):
        self.cur = self.op[self.i] if self.i < len(self.op) else None
        self.i += 1


def transform(a, b):
    """(a', b') for two ops on the same text; a's inserts go first at the same place."""
    if base_length(a) != base_length(b):
        raise OpError("the ops are for different texts")
    a2, b2 = [], []
    ia, ib = _Reader(a), _Reader(b)
    x, y = ia.cur, ib.cur
    while x is not None or y is not None:
        if isinstance(x, str):
            a2.append(x)
            b2.append(_ulen(x))
            ia._next()
            x = ia.cur
            continue
        if isinstance(y, str):
            a2.append(_ulen(y))
            b2.append(y)
            ib._next()
            y = ib.cur
            continue
        if x is None or y is None:
            raise OpError("the ops are for different texts")
        if _is_retain(x) and _is_retain(y):
            n = min(x, y)
            a2.append(n)
            b2.append(n)
        elif _is_delete(x) and _is_delete(y):
            n = min(-x, -y)                     # both deleted the same text: nothing left to do for either
        elif _is_delete(x) and _is_retain(y):
            n = min(-x, y)
            a2.append(-n)
        else:                                   # x retains, y deletes
            n = min(x, -y)
            b2.append(-n)
        x = _shrink(x, n)
        y = _shrink(y, n)
        if x is None:
            ia._next()
            x = ia.cur
        if y is None:
            ib._next()
            y = ib.cur
    return normalise(a2), normalise(b2)


def _shrink(c, n):
    """What is left of a retain or delete after n units of it (None when nothing is)."""
    if c > 0:
        return c - n if c > n else None
    return c + n if -c > n else None


def compose(a, b):
    """One op doing a then b (b is made on a's result). The browser folds its waiting edits with it."""
    if target_length(a) != base_length(b):
        raise OpError("the ops don't follow each other")
    out = []
    ia, ib = _Reader(a), _Reader(b)
    x, y = ia.cur, ib.cur
    while x is not None or y is not None:
        if _is_delete(x):
            out.append(x)
            ia._next()
            x = ia.cur
            continue
        if isinstance(y, str):
            out.append(y)
            ib._next()
            y = ib.cur
            continue
        if x is None or y is None:
            raise OpError("the ops don't follow each other")
        if _is_retain(x) and _is_retain(y):
            n = min(x, y)
            out.append(n)
            x, y = _shrink(x, n), _shrink(y, n)
        elif isinstance(x, str) and _is_delete(y):
            n = min(_ulen(x), -y)
            x = _uslice(x, n) or None
            y = _shrink(y, n)
        elif isinstance(x, str) and _is_retain(y):
            n = min(_ulen(x), y)
            out.append(_uslice(x, 0, n))
            x = _uslice(x, n) or None
            y = _shrink(y, n)
        else:                                   # x retains, y deletes
            n = min(x, -y)
            out.append(-n)
            x, y = _shrink(x, n), _shrink(y, n)
        if x is None:
            ia._next()
            x = ia.cur
        if y is None:
            ib._next()
            y = ib.cur
    return normalise(out)


def transform_index(op, index):
    """Where a caret at `index` ends up after `op` (an insert at the caret pushes it along)."""
    pos, new = 0, index
    for c in op:
        if pos > index:
            break
        if _is_retain(c):
            pos += c
        elif isinstance(c, str):
            new += _ulen(c)
        else:
            new -= min(-c, index - pos)
            pos += -c
    return new


def diff(old, new, cursor=None):
    """The op turning `old` into `new`: one replaced stretch, found by the common end and start (what a textarea edit
    is). Never splits a surrogate pair.

    `cursor` is where the caret is in `new` after the edit. Text can't say which of two equal letters was typed
    ("word|start" -> "words|start"), but the caret can: what follows it was already there. So the kept end never
    reaches before the caret, and the typed letter is credited where it was typed."""
    a, b = old.encode("utf-16-le", "surrogatepass"), new.encode("utf-16-le", "surrogatepass")
    la, lb = len(a) // 2, len(b) // 2

    def unit_a(i):                               # one UTF-16 unit of each text
        return a[i * 2:i * 2 + 2]

    def unit_b(i):
        return b[i * 2:i * 2 + 2]

    max_end = min(la, lb) if cursor is None else max(0, min(la, lb, lb - cursor))
    end = 0
    while end < max_end and unit_a(la - end - 1) == unit_b(lb - end - 1):
        end += 1
    if end and 0xDC00 <= int.from_bytes(unit_a(la - end), "little") <= 0xDFFF:
        end -= 1                                 # don't begin the kept end on the second half of a pair
    start = 0
    while start < la - end and start < lb - end and unit_a(start) == unit_b(start):
        start += 1
    if start and 0xD800 <= int.from_bytes(unit_a(start - 1), "little") <= 0xDBFF:
        start -= 1                               # nor end the kept start on the first half
    op = [start, _uslice(new, start, lb - end), -(la - start - end), end]
    return normalise(op)
