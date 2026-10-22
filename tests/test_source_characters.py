"""No invisible or direction-changing character is written raw in the app's code ("Trojan Source": a bidi override or a
zero-width space makes code read differently from what runs). Where one is meant, it is written as an escape (\u200b).
Tests may hold them on purpose, as input."""
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_apps_code_holds_no_raw_invisible_characters():
    found = []
    for folder in ("ruqqus", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if "node_modules" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if any(unicodedata.category(c) == "Cf" for c in line):
                    found.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert found == []
