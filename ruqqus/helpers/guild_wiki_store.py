"""Guild wikis and pinned resources in the database (rules in helpers/guild_wiki.py): `guild_wiki_pages`,
`guild_wiki_revisions`, `guild_resources`. Plain `text()` SQL with bound values only, tested on SQLite
(tests/test_guild_wiki.py). Every function takes the guild's id, so a page or resource of another guild is never
reached. Nothing here commits.
"""
from sqlalchemy import text

_PAGE = "id, board_id, slug, title, edit_level, current_revision_id, created_utc, locked, title_severity"
_REV = "id, page_id, editor_id, body, body_html, word_severity, reason, created_utc, removed_utc"
_RES = "id, board_id, label, kind, target, position, label_severity"


def pages_of(db, board_id):
    """The guild's pages, `index` first, then by title."""
    return db.execute(text(f"""SELECT {_PAGE} FROM guild_wiki_pages WHERE board_id = :b
                               ORDER BY CASE WHEN slug = 'index' THEN 0 ELSE 1 END, lower(title)"""),
                      {"b": board_id}).fetchall()


def page(db, board_id, slug):
    return db.execute(text(f"SELECT {_PAGE} FROM guild_wiki_pages WHERE board_id = :b AND slug = :s"),
                      {"b": board_id, "s": slug}).fetchone()


def page_count(db, board_id):
    return db.execute(text("SELECT COUNT(*) FROM guild_wiki_pages WHERE board_id = :b"), {"b": board_id}).scalar() or 0


def create_page(db, board_id, slug, title, level, now):
    db.execute(text("""INSERT INTO guild_wiki_pages (board_id, slug, title, edit_level, current_revision_id, created_utc, locked)
                       VALUES (:b, :s, :t, :l, 0, :now, :no)"""),
               {"b": board_id, "s": slug, "t": title, "l": level, "now": now, "no": False})
    return page(db, board_id, slug)


def save(db, board_id, page_id, editor_id, title, body, body_html, severity, reason, now):
    """A new revision, which becomes the page's current text. Returns its id."""
    db.execute(text("""INSERT INTO guild_wiki_revisions (page_id, editor_id, body, body_html, word_severity, reason, created_utc, removed_utc)
                       VALUES (:p, :u, :body, :html, :sev, :reason, :now, 0)"""),
               {"p": page_id, "u": editor_id, "body": body, "html": body_html, "sev": severity, "reason": reason, "now": now})
    rid = db.execute(text("SELECT MAX(id) FROM guild_wiki_revisions WHERE page_id = :p"), {"p": page_id}).scalar()
    db.execute(text("UPDATE guild_wiki_pages SET current_revision_id = :r, title = :t WHERE id = :p AND board_id = :b"),
               {"r": rid, "t": title, "p": page_id, "b": board_id})
    return rid


def set_title_severity(db, board_id, page_id, severity):
    db.execute(text("UPDATE guild_wiki_pages SET title_severity = :s WHERE id = :p AND board_id = :b"),
               {"s": severity, "p": page_id, "b": board_id})


def revision(db, page_id, revision_id):
    return db.execute(text(f"SELECT {_REV} FROM guild_wiki_revisions WHERE page_id = :p AND id = :r"),
                      {"p": page_id, "r": revision_id}).fetchone()


def revisions(db, page_id, limit=100):
    """The page's revisions, newest first."""
    return db.execute(text(f"SELECT {_REV} FROM guild_wiki_revisions WHERE page_id = :p ORDER BY id DESC LIMIT :n"),
                      {"p": page_id, "n": limit}).fetchall()


def edits_since(db, board_id, editor_id, since):
    return db.execute(text("""SELECT COUNT(*) FROM guild_wiki_revisions r JOIN guild_wiki_pages p ON p.id = r.page_id
                              WHERE p.board_id = :b AND r.editor_id = :u AND r.created_utc >= :since"""),
                      {"b": board_id, "u": editor_id, "since": since}).scalar() or 0


def set_settings(db, board_id, page_id, level, locked):
    db.execute(text("UPDATE guild_wiki_pages SET edit_level = :l, locked = :k WHERE id = :p AND board_id = :b"),
               {"l": level, "k": bool(locked), "p": page_id, "b": board_id})


def delete_page(db, board_id, page_id):
    db.execute(text("DELETE FROM guild_wiki_revisions WHERE page_id IN (SELECT id FROM guild_wiki_pages WHERE id = :p AND board_id = :b)"),
               {"p": page_id, "b": board_id})
    db.execute(text("DELETE FROM guild_wiki_pages WHERE id = :p AND board_id = :b"), {"p": page_id, "b": board_id})


def remove_revision(db, page_id, revision_id, now):
    """An admin takes a revision's text down (it stays in the history, its text is not shown)."""
    db.execute(text("UPDATE guild_wiki_revisions SET removed_utc = :now WHERE page_id = :p AND id = :r"),
               {"now": now, "p": page_id, "r": revision_id})


# --- pinned resources ---------------------------------------------------------------------------------------------

def resources_of(db, board_id):
    return db.execute(text(f"SELECT {_RES} FROM guild_resources WHERE board_id = :b ORDER BY position, id"),
                      {"b": board_id}).fetchall()


def add_resource(db, board_id, label, kind, target, label_severity=0):
    position = db.execute(text("SELECT COALESCE(MAX(position), 0) + 1 FROM guild_resources WHERE board_id = :b"),
                          {"b": board_id}).scalar()
    db.execute(text("""INSERT INTO guild_resources (board_id, label, kind, target, position, label_severity)
                       VALUES (:b, :label, :kind, :target, :pos, :sev)"""),
               {"b": board_id, "label": label, "kind": kind, "target": target, "pos": position, "sev": label_severity})


def delete_resource(db, board_id, resource_id):
    db.execute(text("DELETE FROM guild_resources WHERE id = :r AND board_id = :b"), {"r": resource_id, "b": board_id})


def move_resource(db, board_id, resource_id, step):
    ids = [row[0] for row in resources_of(db, board_id)]
    if resource_id not in ids:
        return
    i = ids.index(resource_id)
    j = i + (1 if step > 0 else -1)
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    for position, rid in enumerate(ids, start=1):
        db.execute(text("UPDATE guild_resources SET position = :p WHERE id = :r AND board_id = :b"),
                   {"p": position, "r": rid, "b": board_id})
