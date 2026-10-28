"""The reviewed events that ship with the site (helpers/happening_seed.json, helpers/happening_seed.py). The promises: every entry
passes the same rules an admin's form does (so the table never refuses the seed), links are https, keys are unique, loading it
twice adds nothing, and a reload never undoes an admin."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import happening_seed as seed
from ruqqus.helpers import site_event_store as store
from ruqqus.helpers import site_events as ev

ROOT = Path(__file__).resolve().parent.parent
NOW = 1_793_000_000


def rate(value):
    return 0


@pytest.fixture
def db():
    sql = (ROOT / "scripts" / "migrations" / "2026-12-03_site_events.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for s in (s.strip() for s in sql.split(";")):
            if s.startswith("CREATE"):
                conn.execute(text(s.replace("public.", "").replace("IF NOT EXISTS ", "")
                                  .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")))
    with Session(engine) as session:
        yield session


def test_every_entry_passes_the_admin_rules_and_links_out_on_https():
    entries = seed.entries()
    assert len(entries) >= 5 and len({e["key"] for e in entries}) == len(entries)
    for entry in entries:
        fields = ev.clean_event(entry["form"], NOW)                    # raises Refused when the table would refuse it
        assert entry["source_note"] and (not fields["link_url"] or fields["link_url"].startswith("https://"))
        assert fields["kind"] in ("event", "election") and fields["date_kind"] == "civil"
    elections = [e for e in entries if e["form"]["kind"] == "election"]
    assert elections and all(e["form"]["link_url"].startswith("https://") for e in elections)       # an election links to its source
    assert any(e["key"] == "us-midterm-elections-2026" and e["form"]["starts"] == "2026-11-03" for e in entries)


def test_loading_it_twice_adds_nothing_and_a_reload_never_undoes_an_admin(db):
    first = seed.sync(db, NOW, rate)
    assert first == {"inserted": len(seed.entries()), "updated": 0, "kept": 0}
    assert seed.sync(db, NOW + 60, rate) == {"inserted": 0, "updated": len(seed.entries()), "kept": 0}
    assert db.execute(text("SELECT COUNT(*) FROM site_events")).scalar() == len(seed.entries())
    row = db.execute(text("SELECT id FROM site_events WHERE source_key = 'us-midterm-elections-2026'")).scalar()
    db.execute(text("UPDATE site_events SET hidden_utc = :n, priority = 9, link_url = 'https://official.example/x' WHERE id = :i"),
               {"n": NOW, "i": row})
    seed.sync(db, NOW + 120, rate)
    after = store.get(db, row)
    assert (after["hidden_utc"], after["priority"], after["link_url"]) == (NOW, 9, "https://official.example/x")
