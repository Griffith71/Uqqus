"""Scores and counts read 999, 1.2k, 12k, 1.2M where room is short (helpers/compact_count.py), and the script that
redraws a score after a vote (`compactCount` in all_js.js) reads them the same way."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ruqqus.helpers.compact_count import compact

CASES = [(0, "0"), (None, "0"), (5, "5"), (999, "999"), (1000, "1k"), (1049, "1k"), (1250, "1.2k"), (1299, "1.2k"),
         (9999, "9.9k"), (10_000, "10k"), (12_345, "12k"), (999_999, "999k"), (1_000_000, "1M"),
         (1_204_877, "1.2M"), (-3, "-3"), (-15_000, "-15k")]
JS = (Path(__file__).resolve().parent.parent / "ruqqus" / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("count,text", CASES)
def test_counts_read_short(count, text):
    assert compact(count) == text


def test_the_script_reads_counts_the_same_way():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = re.search(r"function compactCount\(count\) \{.*?\n\}", JS, re.S).group(0)
    numbers = [c for c, _ in CASES if c is not None]
    out = subprocess.run([node, "-e", source + f"\nconsole.log({numbers}.map(compactCount).join(' '))"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == [text for c, text in CASES if c is not None]


def test_a_score_after_a_vote_is_redrawn_short():
    assert "el.textContent = compactCount(net ? ups - downs : ups);" in JS
