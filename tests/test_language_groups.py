"""Language rows (helpers/languages.py, the owner's 2026-10-24 asks): the languages offered, site-wide, are those the
site's own LibreTranslate translates, fixed in code (not read from the running services, so an outage never empties the filter or drops
saved choices), and languages the detector can't tell apart share one row, in the filter and wherever a language is
chosen."""
import re
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from ruqqus.helpers import languages as lg

ROOT = Path(__file__).resolve().parent.parent


def test_every_listed_language_is_in_one_row_or_other():
    seen = {}
    for key, (_label, codes) in lg.ROWS.items():
        for code in codes:
            assert code not in seen, code
            seen[code] = key
    assert set(seen) == set(lg.OFFERED_CODES)
    assert set(lg.LANGUAGE_NAMES) == set(seen) | set(lg.NOT_OFFERED)
    assert lg.LIBRE_SUPPORTED <= set(seen)                                  # every LibreTranslate language has a row


def test_the_rows():
    assert len(lg.ROWS) == 46                                       # LibreTranslate's 47, Malay sharing Indonesian's row
    assert set(lg.ROWS) == lg.LIBRE_SUPPORTED - {"ms"}
    assert lg.ROWS["bg"] == ("Bulgarian", ("bg", "mk"))           # Macedonian is shown as Bulgarian (the owner)
    assert lg.ROWS["el"] == ("Greek", ("el",))
    for code in ("eo", "ht", "pcm", "gcf", "gcr"):                 # taken off the list (the owner, 2026-10-24)
        assert code not in lg.ROWS and lg.key_of(code) is None and lg.label(code) is None, code
    for code in ("hr", "sr", "bs", "uz", "af", "sa", "wa"):      # detected, but not offered: Other or unknown
        assert code not in lg.ROWS and lg.key_of(code) == lg.UNKNOWN, code
    assert lg.ROWS["no"] == ("Norwegian", ("nn", "no"))
    assert lg.ROWS["zh"] == ("Chinese", ("wuu", "yue", "zh"))
    assert lg.ROWS["id"][1] == ("id", "ms") and lg.ROWS["ar"][1] == ("ar", "ary", "arz")
    assert lg.ROWS["es"] == ("Spanish", ("es",))


def test_a_code_finds_its_row():
    assert lg.key_of("mk") == "bg" and lg.key_of("nn") == "no" and lg.key_of("yue") == "zh"
    assert lg.key_of("wa") == lg.UNKNOWN and lg.key_of("und") == lg.UNKNOWN
    assert lg.key_of("xx") is None and lg.key_of(None) is None
    assert lg.same_row("mk", "bg") and not lg.same_row("mk", "ru")
    assert lg.label("ms") == "Indonesian / Malay" and lg.label("sr") == "Other or unknown"


def test_saved_choices_become_rows():
    # an old session, curation or default holding "mk" ticks the Bulgarian row; once each; a removed language is dropped
    assert lg.known_codes(["mk", "bg", "es", "wa", "xx", "und", "es", "eo", "ht"]) == ["bg", "es", "und"]
    assert lg.choosable("mk") and lg.choosable("und") and not lg.choosable("xx")


def test_the_catalogue_is_the_rows_then_other():
    rows = lg.catalogue()
    assert len(rows) == 47 and rows[-1]["value"] == lg.UNKNOWN
    bg = next(r for r in rows if r["value"] == "bg")
    assert bg["label"] == "Bulgarian" and "Macedonian" in bg["search"]      # typing "Macedonian" still finds it


def test_what_libretranslate_runs_by_default_is_offered():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    loaded = re.search(r"LT_LOAD_ONLY=\$\{LT_LOAD_ONLY:-([a-z,]+)\}", compose).group(1).split(",")
    # every language the site supports, in LibreTranslate's own codes (nb is Norwegian)
    assert ",".join(loaded) == lg.libre_load_only()
    for code in loaded:
        assert lg.LIBRE_ALIASES.get(code, code) in lg.LIBRE_SUPPORTED, code


def test_the_filter_condition_on_sqlite():
    engine = create_engine("sqlite://")
    meta = MetaData()
    posts = Table("posts", meta, Column("id", Integer), Column("language_code", String(5)))
    meta.create_all(engine)
    rows = [(1, "bg"), (2, "mk"), (3, "ru"), (4, None), (5, "wa"), (6, "es")]
    with engine.begin() as db:
        db.execute(posts.insert(), [{"id": i, "language_code": c} for i, c in rows])

        def ids(codes):
            return sorted(r[0] for r in db.execute(select(posts.c.id).where(lg.language_condition(posts.c.language_code, codes))))
        assert ids(["bg"]) == [1, 2]                 # the row: Bulgarian and Macedonian
        assert ids(["mk"]) == [1, 2]                 # an old saved "mk" is the same row
        assert ids(["und"]) == [4, 5]                # untagged, and a language no service translates
        assert ids(["es", "und"]) == [4, 5, 6]
