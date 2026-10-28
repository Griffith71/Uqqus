"""Weighted text length (helpers/text_length.py and its browser copy assets/js/text_length.js): the owner's choice of
2026-10-23, as X counts: Chinese, Japanese and Korean characters and emoji count as 2, an emoji however it's built
counts once, and text is cut only between whole characters. One set of vectors for both copies."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ruqqus.helpers import text_length as tl

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "data" / "text_length_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS, ids=[c["text"] or "empty" for c in VECTORS])
def test_python_counts_and_cuts_as_the_vectors_say(case):
    assert tl.weighted_length(case["text"]) == case["weighted"]
    assert tl.clip(case["text"], 3) == case["clip3"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_copy_agrees():
    script = (
        "const tl = require(%s); const v = require(%s);"
        "const bad = v.filter(c => tl.weighted(c.text) !== c.weighted || tl.clip(c.text, 3) !== c.clip3)"
        ".map(c => [c.text, tl.weighted(c.text), tl.clip(c.text, 3)]);"
        "console.log(JSON.stringify(bad));"
    ) % (json.dumps(str(ROOT / "ruqqus" / "assets" / "js" / "text_length.js")),
         json.dumps(str(ROOT / "tests" / "data" / "text_length_vectors.json")))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == []


def test_a_title_holds_280_letters_or_140_cjk_characters():
    assert not tl.too_long("a" * 280, 280) and tl.too_long("a" * 281, 280)
    assert not tl.too_long("字" * 140, 280) and tl.too_long("字" * 141, 280)
    assert tl.weighted_length(tl.clip("字" * 200, 280)) == 280


def test_clip_never_cuts_inside_an_emoji():
    family = "👨‍👩‍👧"
    assert tl.clip("a" + family, 2) == "a"            # the family needs 2; it isn't split to fit
    assert tl.clip("a" + family, 3) == "a" + family


def test_the_message_names_the_rule():
    assert tl.limit_message("Titles", 280) == (
        "Titles can be 280 characters (Chinese, Japanese and Korean characters and emoji count as 2).")
