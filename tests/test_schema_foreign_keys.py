"""schema.sql is a file a fresh database is built from (audit D-01: ten foreign keys had no REFERENCES, so loading it
failed on them and a fresh install had none of those keys)."""
import re
from pathlib import Path

SCHEMA = (Path(__file__).resolve().parent.parent / "schema.sql").read_text(encoding="utf-8")


def test_every_foreign_key_names_what_it_references():
    for statement in re.findall(r"FOREIGN KEY \([^)]*\)[^;]*;", SCHEMA):
        assert "REFERENCES" in statement, statement
