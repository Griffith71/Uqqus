"""Translations kept (table `translations`; migration 2026-11-26_languages_and_translation.sql; its `translation_usage`
table counted Google's characters until Google was removed on 2026-10-24 and is no longer used). Plain `text()` SQL,
tested on SQLite (tests/test_translation.py). Nothing here commits: the route does. The rules are
helpers/translation.py.
"""
from sqlalchemy import bindparam, text


def kept(db, kind, item_id, target, engine, source_hash):
    """(title, text_html, the language it was translated from) of a kept translation, or None."""
    row = db.execute(text(
        "SELECT title, text_html, source_lang FROM translations WHERE kind = :k AND item_id = :i AND target = :t "
        "AND engine = :e AND source_hash = :h"),
        {"k": kind, "i": item_id, "t": target, "e": engine, "h": source_hash}).fetchone()
    return (row[0], row[1], row[2]) if row else None


def keep(db, kind, item_id, target, engine, source_hash, source_lang, title, text_html, now):
    """Keeps a translation (a second press of the same button finds it; two at once keep the first)."""
    db.execute(text(
        "INSERT INTO translations (kind, item_id, source_hash, source_lang, target, engine, title, text_html, created_utc) "
        "VALUES (:k, :i, :h, :s, :t, :e, :title, :html, :now) "
        "ON CONFLICT (kind, item_id, target, engine, source_hash) DO NOTHING"),
        {"k": kind, "i": item_id, "h": source_hash, "s": source_lang, "t": target, "e": engine,
         "title": title or "", "html": text_html or "", "now": int(now)})


def forget(db, kind, item_ids):
    """Drops every kept translation of these items: a purge or an obliteration leaves no copy of the erased text."""
    ids = sorted({int(i) for i in item_ids})
    if ids:
        db.execute(text("DELETE FROM translations WHERE kind = :k AND item_id IN :ids")
                   .bindparams(bindparam("ids", expanding=True)), {"k": kind, "ids": ids})
