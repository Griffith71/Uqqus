"""Operational transformation for shared drafts (helpers/collab_ot.py and its browser copy assets/js/collab_ot.js).
The promises: two concurrent edits converge whichever order they are applied in; the op being brought in keeps its
insert first at the same place; positions are UTF-16 units and an emoji is never split; the Python and the JS copies
give the same answers on the same vectors."""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from ruqqus.helpers import collab_ot as ot

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "data" / "collab_ot_vectors.json"
ALPHABET = "ab cd\n😀é"


def random_text(rng, n=None):
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 12) if n is None else n))


def random_edit(rng, text):
    """A textarea-like edit: replace one stretch (made by diff, so it never splits a character)."""
    chars = list(text)
    i = rng.randint(0, len(chars))
    j = rng.randint(i, min(len(chars), i + rng.randint(0, 4)))
    new = "".join(chars[:i]) + random_text(rng, rng.randint(0, 3)) + "".join(chars[j:])
    return ot.diff(text, new)


def test_apply_diff_and_units():
    assert ot.units("a😀b") == 4
    assert ot.apply("hello", [1, "EE", -3, 1]) == "hEEo"
    assert ot.diff("hello world", "hello brave world") == [5, " brave", 6]
    assert ot.apply("a😀b", ot.diff("a😀b", "a😁b")) == "a😁b"
    assert ot.diff("a😀b", "a😁b") == [1, "😁", -2, 1]               # the whole pair, never half
    with pytest.raises(ot.OpError):
        ot.apply("a😀b", [2, -1, 1])                                    # would leave half an emoji
    with pytest.raises(ot.OpError):
        ot.apply("abc", [2])                                           # doesn't cover the text
    with pytest.raises(ot.OpError):
        ot.check([1, "\ud83d", 1])                                     # a lone surrogate
    for bad in ("x", [0.5], [True], [None], [1, {}]):
        with pytest.raises(ot.OpError):
            ot.check(bad)
    assert ot.check([1, 1, "a", "b", -1, -1]) == [2, "ab", -2]
    assert ot.normalise([2, -1, "x"]) == [2, "x", -1]
    # the caret says which of two equal letters was typed
    assert ot.diff("wordstart", "wordsstart") == [4, "s", 5]                  # text alone: one reading
    assert ot.diff("wordstart", "wordsstart", cursor=5) == [4, "s", 5]        # caret after "words": typed after "word"
    assert ot.diff("wordstart", "wordsstart", cursor=6) == [5, "s", 4]        # caret after "wordss": typed after "words"
    assert ot.diff("aab", "ab", cursor=1) == [1, -1, 1]
    for cursor in range(0, 7):                                                # any caret still gives a right op
        assert ot.apply("abcabc", ot.diff("abcabc", "abXcabc", cursor)) == "abXcabc"


def test_same_place_inserts_order_the_incoming_op_first():
    a, b = [1, "A", 2], [1, "B", 2]
    a2, b2 = ot.transform(a, b)
    assert ot.apply(ot.apply("xyz", a), b2) == ot.apply(ot.apply("xyz", b), a2) == "xAByz"


def test_overlapping_deletes_and_delete_against_insert():
    a, b = [1, -3, 1], [2, -3]                                         # both delete "cd" in "abcde"
    a2, b2 = ot.transform(a, b)
    assert ot.apply(ot.apply("abcde", a), b2) == ot.apply(ot.apply("abcde", b), a2) == "a"
    a, b = [1, -3, 1], [2, "Z", 3]                                     # insert inside a deleted stretch survives
    a2, b2 = ot.transform(a, b)
    assert ot.apply(ot.apply("abcde", a), b2) == ot.apply(ot.apply("abcde", b), a2) == "aZe"


def test_random_concurrent_edits_converge():
    for seed in range(1000):
        rng = random.Random(seed)
        text = random_text(rng)
        a, b = random_edit(rng, text), random_edit(rng, text)
        a2, b2 = ot.transform(a, b)
        assert ot.apply(ot.apply(text, a), b2) == ot.apply(ot.apply(text, b), a2), seed
        # compose: a then (an edit made after a) is one op
        after = random_edit(rng, ot.apply(text, a))
        assert ot.apply(text, ot.compose(a, after)) == ot.apply(ot.apply(text, a), after), seed


def test_a_client_with_waiting_edits_converges_with_the_server():
    """The browser's state machine in miniature: an edit in flight and a buffered one, while the server commits
    another author's edits; everyone ends with the same text."""
    for seed in range(300):
        _client_round(random.Random(1000 + seed))


def _client_round(rng):
    text = random_text(rng, 8)
    mine1 = random_edit(rng, text)
    mine2 = random_edit(rng, ot.apply(text, mine1))
    theirs = [random_edit(rng, text)]
    theirs.append(random_edit(rng, ot.apply(text, theirs[0])))
    server = ot.apply(ot.apply(text, theirs[0]), theirs[1])
    # the server receives mine1 (made on `text`) after theirs: bring it in
    incoming = mine1
    for t in theirs:
        incoming = ot.transform(incoming, t)[0]
    server = ot.apply(server, incoming)
    # the browser had applied mine1 and mine2 locally; it receives theirs and transforms them past its own edits
    local = ot.apply(ot.apply(text, mine1), mine2)
    outstanding, buffer = mine1, mine2
    for t in theirs:
        outstanding, t = ot.transform(outstanding, t)
        buffer, t = ot.transform(buffer, t)
        local = ot.apply(local, t)
    # its own mine1 comes back acknowledged; then it sends the buffer, which the server brings in past nothing new
    server = ot.apply(server, buffer)
    assert local == server


def test_vectors_file_is_current():
    """The vectors are made from the Python implementation; the JS check below runs the same file."""
    assert json.loads(VECTORS.read_text(encoding="utf-8")) == make_vectors()


def make_vectors():
    rng = random.Random(7)
    out = []
    for _ in range(150):
        text = random_text(rng)
        a, b = random_edit(rng, text), random_edit(rng, text)
        a2, b2 = ot.transform(a, b)
        after = random_edit(rng, ot.apply(text, a))
        new = random_text(rng)
        cursor = rng.randint(0, len(new.encode("utf-16-le")) // 2)
        out.append({"text": text, "a": a, "b": b, "a2": a2, "b2": b2, "after": after,
                    "composed": ot.compose(a, after), "new": new, "diff": ot.diff(text, new),
                    "cursor": cursor, "diff_cursor": ot.diff(text, new, cursor),
                    "index": len(text) // 2, "index_after_b": ot.transform_index(b, len(text) // 2)})
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_copy_gives_the_same_answers():
    script = f"""
      const ot = require({json.dumps(str(ROOT / 'ruqqus' / 'assets' / 'js' / 'collab_ot.js'))});
      const cases = require({json.dumps(str(VECTORS))});
      const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
      let bad = 0;
      for (const c of cases) {{
        const [a2, b2] = ot.transform(c.a, c.b);
        if (!same(a2, c.a2) || !same(b2, c.b2)) bad++;
        if (!same(ot.compose(c.a, c.after), c.composed)) bad++;
        if (!same(ot.diff(c.text, c.new), c.diff)) bad++;
        if (!same(ot.diff(c.text, c.new, c.cursor), c.diff_cursor)) bad++;
        if (ot.apply(c.text, c.diff_cursor) !== c.new) bad++;
        if (ot.transformIndex(c.b, c.index) !== c.index_after_b) bad++;
        if (ot.apply(ot.apply(c.text, c.a), c.b2) !== ot.apply(ot.apply(c.text, c.b), c.a2)) bad++;
      }}
      console.log(bad);
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "0"
