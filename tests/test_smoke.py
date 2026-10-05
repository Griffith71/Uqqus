"""Cheap guard that every Python module in the legacy app still parses.

Does not import the app (which needs Postgres/Redis/env). Real behaviour
tests - auth, permissions, voting - are added as characterization tests.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "ruqqus"


def test_all_modules_parse():
    files = sorted(APP.rglob("*.py"))
    assert files, "no Python files found under ruqqus/"
    for f in files:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
