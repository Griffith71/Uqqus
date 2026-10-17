"""Shared drafts in the database (rules in helpers/collab_rules.py): `collab_docs`, `collab_authors`, `collab_ops`,
`collab_publishes`, `collab_proposals`, `collab_votes`. Plain `text()` SQL with bound values only, tested on SQLite
(tests/test_collab_store.py). Every function that touches one draft's ops, revisions, proposals or authors takes the
draft's id, so another draft is never reached. Nothing here commits.

`commit_op` is the one place the text changes: it locks the draft's row, brings the op in past every op committed
since the browser's base revision (helpers/collab_ot.py), applies it to the text and to who typed what
(helpers/collab_authorship.py), and stores it stamped with the caller's id.
"""
import json

from sqlalchemy import text

from ruqqus.helpers import collab_authorship as authorship
from ruqqus.helpers import collab_ot as ot
from ruqqus.helpers import collab_rules as rules

_DOC = ("id, creator_id, title, body, revision, authorship, rules, post_id, published_revision, forked_from_doc_id, "
        "forked_from_post_id, forked_at_publish_id, created_utc, updated_utc, deleted_utc, options")
_PROPOSAL = ("id, doc_id, kind, subject_user_id, payload, proposed_by, created_utc, closes_utc, decided_utc, outcome, "
             "invitee_answer")
FIELDS = ("title", "body")
LIMITS = {"title": rules.TITLE_MAX, "body": rules.BODY_MAX}


class Resync(Exception):
    """The browser is too far behind (or its base is in the future): it reloads the draft."""


def _loads(value, default):
    try:
        out = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def _dumps(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def runs_of(doc):
    """{"title": runs, "body": runs} of a draft row."""
    data = _loads(doc[5], {})
    return {f: authorship.clean(data.get(f)) for f in FIELDS}


def rules_of(doc):
    return rules.clean_rules(_loads(doc[6], {}))


def options_of(doc):
    """The post options kept with the draft (a link, who can comment, disclosure, sensitive, guilds to forward to)."""
    return rules.clean_options(_loads(doc[15], {}))


# --- drafts -------------------------------------------------------------------------------------------------------

def create(db, creator_id, now, *, title="", body="", runs=None, forked_from=None):
    """A new draft with its creator as the one author. `runs` keeps who typed a fork's text."""
    runs = runs or {"title": authorship.for_text(title, creator_id), "body": authorship.for_text(body, creator_id)}
    fork = forked_from or {}
    db.execute(text(f"""INSERT INTO collab_docs (creator_id, title, body, revision, authorship, rules, post_id,
                          published_revision, forked_from_doc_id, forked_from_post_id, forked_at_publish_id, created_utc,
                          updated_utc, deleted_utc)
                        VALUES (:c, :t, :b, 0, :a, :r, NULL, 0, :fd, :fp, :fx, :now, :now, 0)"""),
               {"c": creator_id, "t": title, "b": body, "a": _dumps(runs), "r": _dumps(rules.DEFAULT_RULES),
                "fd": fork.get("doc_id"), "fp": fork.get("post_id"), "fx": fork.get("publish_id"), "now": now})
    doc_id = db.execute(text("SELECT MAX(id) FROM collab_docs WHERE creator_id = :c"), {"c": creator_id}).scalar()
    db.execute(text("INSERT INTO collab_authors (doc_id, user_id, joined_utc, left_utc) VALUES (:d, :u, :now, 0)"),
               {"d": doc_id, "u": creator_id, "now": now})
    return doc_id


def doc(db, doc_id, lock=False):
    sql = f"SELECT {_DOC} FROM collab_docs WHERE id = :d AND deleted_utc = 0"
    if lock and db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    return db.execute(text(sql), {"d": doc_id}).fetchone()


def doc_for_post(db, post_id):
    return db.execute(text(f"SELECT {_DOC} FROM collab_docs WHERE post_id = :p"), {"p": post_id}).fetchone()


def open_doc_for_post(db, post_id):
    """The OPEN draft a post is published from, or None. Only while it is open does the post change through it alone:
    once its last author leaves, the post is an ordinary post of the profile it is on again (audit C-01), so it is never
    left that nobody can edit or delete."""
    return db.execute(text(f"SELECT {_DOC} FROM collab_docs WHERE post_id = :p AND deleted_utc = 0"),
                      {"p": post_id}).fetchone()


def post_ids(db):
    """Every published collaborative post's id (cached by routes/collab.py for menus and pages)."""
    return [r[0] for r in db.execute(text("SELECT post_id FROM collab_docs WHERE post_id IS NOT NULL"))]


def docs_of(db, user_id, limit=100):
    """The drafts this person is an author of, last changed first."""
    return db.execute(text(f"""SELECT {', '.join('d.' + c.strip() for c in _DOC.split(','))} FROM collab_docs d
                               JOIN collab_authors a ON a.doc_id = d.id
                               WHERE a.user_id = :u AND a.left_utc = 0 AND d.deleted_utc = 0
                               ORDER BY d.updated_utc DESC LIMIT :n"""), {"u": user_id, "n": limit}).fetchall()


def made_since(db, user_id, since):
    return db.execute(text("SELECT COUNT(*) FROM collab_docs WHERE creator_id = :u AND created_utc >= :s"),
                      {"u": user_id, "s": since}).scalar() or 0


def forks_since(db, user_id, since):
    return db.execute(text("""SELECT COUNT(*) FROM collab_docs WHERE creator_id = :u AND created_utc >= :s
                              AND forked_from_post_id IS NOT NULL"""), {"u": user_id, "s": since}).scalar() or 0


def fork_count(db, post_id):
    return db.execute(text("SELECT COUNT(*) FROM collab_docs WHERE forked_from_post_id = :p"), {"p": post_id}).scalar() or 0


def set_published(db, doc_id, post_id, revision):
    db.execute(text("UPDATE collab_docs SET post_id = :p, published_revision = :r WHERE id = :d"),
               {"p": post_id, "r": revision, "d": doc_id})


def set_options(db, doc_id, options, now):
    db.execute(text("UPDATE collab_docs SET options = :o, updated_utc = :now WHERE id = :d"),
               {"o": _dumps(rules.clean_options(options)), "now": now, "d": doc_id})


def set_rules(db, doc_id, new_rules, now):
    db.execute(text("UPDATE collab_docs SET rules = :r, updated_utc = :now WHERE id = :d"),
               {"r": _dumps(rules.clean_rules(new_rules)), "now": now, "d": doc_id})


def close(db, doc_id, now):
    db.execute(text("UPDATE collab_docs SET deleted_utc = :now WHERE id = :d AND deleted_utc = 0"), {"now": now, "d": doc_id})


# --- authors ------------------------------------------------------------------------------------------------------

def authors(db, doc_id):
    """Current authors with when they were last active: [(user_id, last_active_utc, joined_utc)], earliest first."""
    return db.execute(text("""SELECT a.user_id, COALESCE(u.last_active_utc, 0), a.joined_utc FROM collab_authors a
                              LEFT JOIN users u ON u.id = a.user_id
                              WHERE a.doc_id = :d AND a.left_utc = 0 ORDER BY a.joined_utc, a.user_id"""),
                      {"d": doc_id}).fetchall()


def voters(db, doc_id):
    """The authors who can take part in a decision: [(user_id, last_active_utc)], leaving out deleted accounts and
    accounts banned for good (audit C-04: they used to count, so one deleted author could hold a unanimous decision
    for six months, or for ever under "never")."""
    return [(r[0], r[1]) for r in db.execute(text("""
        SELECT a.user_id, COALESCE(u.last_active_utc, 0) FROM collab_authors a JOIN users u ON u.id = a.user_id
        WHERE a.doc_id = :d AND a.left_utc = 0 AND COALESCE(u.is_deleted, :no) = :no
          AND NOT (COALESCE(u.is_banned, 0) <> 0 AND COALESCE(u.unban_utc, 0) = 0)
        ORDER BY a.joined_utc, a.user_id"""), {"d": doc_id, "no": False})]


def author_ids(db, doc_id):
    return [r[0] for r in authors(db, doc_id)]


def everyone(db, doc_id):
    """Everyone who was ever an author (their uploads stay in the text after they leave)."""
    return [r[0] for r in db.execute(text("SELECT user_id FROM collab_authors WHERE doc_id = :d"), {"d": doc_id})]


def is_author(db, doc_id, user_id):
    if not user_id:
        return False
    return db.execute(text("SELECT 1 FROM collab_authors WHERE doc_id = :d AND user_id = :u AND left_utc = 0"),
                      {"d": doc_id, "u": user_id}).first() is not None


def add_author(db, doc_id, user_id, now, proposal_id=None):
    """Joins (or joins again after leaving)."""
    db.execute(text("DELETE FROM collab_authors WHERE doc_id = :d AND user_id = :u"), {"d": doc_id, "u": user_id})
    db.execute(text("""INSERT INTO collab_authors (doc_id, user_id, joined_utc, left_utc, added_by_proposal_id)
                       VALUES (:d, :u, :now, 0, :p)"""), {"d": doc_id, "u": user_id, "now": now, "p": proposal_id})


def remove_author(db, doc_id, user_id, now):
    """Leaves, or is removed. Their text stays credited to them."""
    db.execute(text("UPDATE collab_authors SET left_utc = :now WHERE doc_id = :d AND user_id = :u AND left_utc = 0"),
               {"now": now, "d": doc_id, "u": user_id})


# --- ops ------------------------------------------------------------------------------------------------------------

def ops_after(db, doc_id, revision, limit=1000):
    """[(revision, user_id, field, op)] committed after `revision`, in order."""
    rows = db.execute(text("""SELECT revision, user_id, field, op FROM collab_ops WHERE doc_id = :d AND revision > :r
                              ORDER BY revision LIMIT :n"""), {"d": doc_id, "r": revision, "n": limit}).fetchall()
    return [(r[0], r[1], r[2], _loads(r[3], [])) for r in rows]


def ops_since(db, user_id, since):
    return db.execute(text("SELECT COUNT(*) FROM collab_ops WHERE user_id = :u AND created_utc >= :s"),
                      {"u": user_id, "s": since}).scalar() or 0


def commit_op(db, doc_id, user_id, field, base, op, now, client_key=""):
    """Brings `op` (made on revision `base` of `field`) in, applies it and stores it. Returns the new revision, or
    None when it changes nothing. Raises Resync (the browser reloads), ot.OpError or rules.Refused. `client_key` is the
    browser's own name for the op: an op sent again (its answer was lost) answers the revision it already has."""
    if field not in FIELDS:
        raise ot.OpError("no such field")
    op = ot.check(op)
    row = doc(db, doc_id, lock=True)
    if row is None:
        raise Resync()
    if client_key:
        seen = db.execute(text("""SELECT revision FROM collab_ops WHERE doc_id = :d AND user_id = :u
                                  AND client_key = :k"""), {"d": doc_id, "u": user_id, "k": client_key}).scalar()
        if seen is not None:
            return seen
    revision = row[4]
    if base > revision or revision - base > rules.RESYNC_AFTER:
        raise Resync()
    for _, _, other_field, other in ops_after(db, doc_id, base, rules.RESYNC_AFTER + 1):
        if other_field == field:
            op = ot.transform(op, other)[0]
    current = row[2] if field == "title" else row[3]
    new_text = ot.apply(current, op)             # also refuses an op that doesn't fit the text
    if ot.is_noop(op):
        return None
    if ot.units(new_text) > LIMITS[field]:
        raise rules.Refused(f"The {field} can be at most {LIMITS[field]:,} characters.")
    runs = runs_of(row)
    runs[field] = authorship.apply(runs[field], op, user_id)
    revision += 1
    db.execute(text("""INSERT INTO collab_ops (doc_id, revision, user_id, field, op, client_key, created_utc)
                       VALUES (:d, :r, :u, :f, :o, :k, :now)"""),
               {"d": doc_id, "r": revision, "u": user_id, "f": field, "o": _dumps(op), "k": client_key or "", "now": now})
    db.execute(text(f"UPDATE collab_docs SET {field} = :t, authorship = :a, revision = :r, updated_utc = :now WHERE id = :d"),
               {"t": new_text, "a": _dumps(runs), "r": revision, "now": now, "d": doc_id})
    return revision


# --- public revisions -----------------------------------------------------------------------------------------------

def add_publish(db, doc_id, revision, title, body, runs, published_by, now):
    number = (db.execute(text("SELECT COALESCE(MAX(number), 0) FROM collab_publishes WHERE doc_id = :d"),
                         {"d": doc_id}).scalar() or 0) + 1
    db.execute(text("""INSERT INTO collab_publishes (doc_id, number, revision, title, body, authorship, published_by, published_utc)
                       VALUES (:d, :n, :r, :t, :b, :a, :u, :now)"""),
               {"d": doc_id, "n": number, "r": revision, "t": title, "b": body, "a": _dumps(runs), "u": published_by, "now": now})
    return number


def publishes(db, doc_id):
    """[(id, number, revision, title, published_by, published_utc)] newest first."""
    return db.execute(text("""SELECT id, number, revision, title, published_by, published_utc FROM collab_publishes
                              WHERE doc_id = :d ORDER BY number DESC"""), {"d": doc_id}).fetchall()


def publish_row(db, doc_id, number):
    """(id, number, revision, title, body, runs, published_by, published_utc) or None."""
    row = db.execute(text("""SELECT id, number, revision, title, body, authorship, published_by, published_utc
                             FROM collab_publishes WHERE doc_id = :d AND number = :n"""), {"d": doc_id, "n": number}).fetchone()
    if row is None:
        return None
    data = _loads(row[5], {})
    return (row[0], row[1], row[2], row[3], row[4], {f: authorship.clean(data.get(f)) for f in FIELDS}, row[6], row[7])


def latest_publish(db, doc_id):
    number = db.execute(text("SELECT MAX(number) FROM collab_publishes WHERE doc_id = :d"), {"d": doc_id}).scalar()
    return publish_row(db, doc_id, number) if number else None


def publish_by_id(db, publish_id):
    row = db.execute(text("SELECT doc_id, number FROM collab_publishes WHERE id = :i"), {"i": publish_id}).fetchone()
    return publish_row(db, row[0], row[1]) if row else None


# --- proposals and votes -----------------------------------------------------------------------------------------

def propose(db, doc_id, kind, proposed_by, now, closes_utc, *, subject=None, payload=None):
    db.execute(text("""INSERT INTO collab_proposals (doc_id, kind, subject_user_id, payload, proposed_by, created_utc,
                         closes_utc, decided_utc, outcome, invitee_answer)
                       VALUES (:d, :k, :s, :p, :by, :now, :closes, 0, 'open', '')"""),
               {"d": doc_id, "k": kind, "s": subject, "p": _dumps(payload or {}), "by": proposed_by, "now": now,
                "closes": closes_utc})
    return db.execute(text("SELECT MAX(id) FROM collab_proposals WHERE doc_id = :d AND proposed_by = :by"),
                      {"d": doc_id, "by": proposed_by}).scalar()


def proposal(db, doc_id, proposal_id):
    return db.execute(text(f"SELECT {_PROPOSAL} FROM collab_proposals WHERE id = :i AND doc_id = :d"),
                      {"i": proposal_id, "d": doc_id}).fetchone()


def proposal_by_id(db, proposal_id):
    return db.execute(text(f"SELECT {_PROPOSAL} FROM collab_proposals WHERE id = :i"), {"i": proposal_id}).fetchone()


def proposals(db, doc_id, open_only=False, limit=50):
    sql = f"SELECT {_PROPOSAL} FROM collab_proposals WHERE doc_id = :d"
    if open_only:
        sql += " AND outcome = 'open'"
    return db.execute(text(sql + " ORDER BY id DESC LIMIT :n"), {"d": doc_id, "n": limit}).fetchall()


def payload_of(row):
    return _loads(row[4], {})


def open_like(db, doc_id, kind, subject=None):
    """An open proposal of this kind (about this person), if there is one: one at a time."""
    sql = "SELECT id FROM collab_proposals WHERE doc_id = :d AND kind = :k AND outcome = 'open'"
    params = {"d": doc_id, "k": kind}
    if subject is not None:
        sql += " AND subject_user_id = :s"
        params["s"] = subject
    return db.execute(text(sql), params).scalar()


def open_by(db, doc_id, user_id):
    """How many open proposals `user_id` has in this draft."""
    return db.execute(text("""SELECT COUNT(*) FROM collab_proposals WHERE doc_id = :d AND proposed_by = :u
                              AND outcome = 'open'"""), {"d": doc_id, "u": user_id}).scalar() or 0


def proposals_since(db, user_id, since):
    return db.execute(text("SELECT COUNT(*) FROM collab_proposals WHERE proposed_by = :u AND created_utc >= :s"),
                      {"u": user_id, "s": since}).scalar() or 0


def vote(db, proposal_id, user_id, yes, now):
    db.execute(text("DELETE FROM collab_votes WHERE proposal_id = :p AND user_id = :u"), {"p": proposal_id, "u": user_id})
    db.execute(text("INSERT INTO collab_votes (proposal_id, user_id, vote, created_utc) VALUES (:p, :u, :v, :now)"),
               {"p": proposal_id, "u": user_id, "v": bool(yes), "now": now})


def votes(db, proposal_id):
    """{user_id: True/False}."""
    return {r[0]: bool(r[1]) for r in db.execute(text("SELECT user_id, vote FROM collab_votes WHERE proposal_id = :p"),
                                                 {"p": proposal_id})}


def decide(db, proposal_id, outcome, now):
    """Closes an open proposal; True only for the caller that closed it (a race decides once)."""
    result = db.execute(text("""UPDATE collab_proposals SET outcome = :o, decided_utc = :now
                                WHERE id = :i AND outcome = 'open'"""), {"o": outcome, "now": now, "i": proposal_id})
    return result.rowcount == 1


def answer_invite(db, proposal_id, answer):
    result = db.execute(text("""UPDATE collab_proposals SET invitee_answer = :a
                                WHERE id = :i AND kind = 'add_author' AND outcome = 'passed' AND invitee_answer = ''"""),
                        {"a": answer, "i": proposal_id})
    return result.rowcount == 1


def invitations_for(db, user_id, since):
    """Passed add_author proposals waiting for this person's answer, newer than `since`."""
    return db.execute(text(f"""SELECT {_PROPOSAL} FROM collab_proposals WHERE subject_user_id = :u AND kind = 'add_author'
                               AND outcome = 'passed' AND invitee_answer = '' AND decided_utc >= :s ORDER BY id DESC"""),
                      {"u": user_id, "s": since}).fetchall()


def open_proposal_ids(db, limit=500):
    """(id, doc_id) of every open proposal (the hourly job re-counts them)."""
    return db.execute(text("SELECT id, doc_id FROM collab_proposals WHERE outcome = 'open' ORDER BY id LIMIT :n"),
                      {"n": limit}).fetchall()
