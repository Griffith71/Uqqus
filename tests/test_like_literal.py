"""Name lookups (`get_user`, `get_guild`, `get_curation`, `get_promocode`) use ILIKE for case-insensitive equality, so the
text typed by a visitor must match only itself: `%` and `_` are wildcards, and a backslash is the escape character.
`like_literal` + `escape=LIKE_ESCAPE` do that; the escape character is named in the query instead of relying on a
database default. Before, `%` was stripped from a username (and not at all from a promo code, so the code `%` matched
the first promo code there was) and a backslash was dropped, so `a\b` looked up `ab`."""
import re
from pathlib import Path

from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "ruqqus" / "helpers" / "get.py").read_text(encoding="utf-8")


def helper():
    """The helper's own source, run on its own (importing get.py needs the whole app)."""
    start = SOURCE.index("def like_literal(text):")
    end = SOURCE.index("LIKE_ESCAPE =", start)
    end = SOURCE.index("\n", end)
    scope = {}
    exec(SOURCE[start:end], scope)
    return scope["like_literal"], scope["LIKE_ESCAPE"]


def lookup(names, typed):
    like_literal, escape = helper()
    table = Table("names", MetaData(), Column("name", String))
    engine = create_engine("sqlite://")
    table.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(table), [{"name": n} for n in names])
        return [r[0] for r in conn.execute(select(table.c.name).where(table.c.name.ilike(like_literal(typed), escape=escape)))]


def test_a_name_matches_itself_in_any_case():
    assert lookup(["Alice", "bob"], "aLiCe") == ["Alice"]


def test_percent_is_not_a_wildcard():
    assert lookup(["alice", "bob"], "%") == []
    assert lookup(["alice", "bob"], "a%") == []
    assert lookup(["50%"], "50%") == ["50%"]


def test_underscore_is_not_a_wildcard():
    assert lookup(["ab", "a_b"], "a_b") == ["a_b"]
    assert lookup(["ab", "axb"], "a_b") == []


def test_a_backslash_is_a_character_not_the_escape():
    assert lookup(["ab", "a\b"], "a\b") == ["a\b"]
    assert lookup(["ab"], "a\b") == []


def test_every_ilike_in_get_py_names_its_escape_character():
    calls = re.findall(r"\.ilike\(([^\n]*)\)", SOURCE)
    assert calls, "no ilike calls found: has the lookup code moved?"
    for call in calls:
        assert "escape=LIKE_ESCAPE" in call, call
    assert "ilike(" not in SOURCE.replace(".ilike(", "")
