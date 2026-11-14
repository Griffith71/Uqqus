"""The Log's tables from the migrations AFTER 2026-12-09_action_log.sql, in SQLite's words (the migrations themselves are Postgres),
for the SQLite tests of helpers/action_log.py and helpers/log_appeals.py. `tests/test_log_paper_trail.py` checks the columns against
schema.sql so this cannot drift from it."""
from sqlalchemy import text

LATER = (
    # 2026-12-21_log_snapshot_kinds.sql
    "ALTER TABLE log_snapshots ADD COLUMN kind VARCHAR(12) NOT NULL DEFAULT 'hide'",
    "ALTER TABLE log_snapshots ADD COLUMN hider_id INTEGER",
    # 2026-12-30_log_paper_trail.sql
    "ALTER TABLE log_entries ADD COLUMN until_utc BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE log_entries ADD COLUMN linked_entry_id BIGINT",
    "CREATE TABLE log_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, created_utc BIGINT NOT NULL DEFAULT 0, "
    "action VARCHAR(32) NOT NULL DEFAULT '', detail VARCHAR(300) NOT NULL DEFAULT '')",
    "CREATE TABLE log_appeals (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id BIGINT NOT NULL, user_id INTEGER NOT NULL, "
    "board_id INTEGER, reason VARCHAR(500) NOT NULL DEFAULT '', created_utc BIGINT NOT NULL DEFAULT 0, "
    "status VARCHAR(10) NOT NULL DEFAULT 'open', decided_by INTEGER, decided_utc BIGINT NOT NULL DEFAULT 0, "
    "decision_note VARCHAR(500) NOT NULL DEFAULT '', reversal_entry_id BIGINT, "
    "CHECK (status IN ('open', 'upheld', 'reversed', 'dismissed')))",
    "CREATE UNIQUE INDEX log_appeals_once_idx ON log_appeals (entry_id, user_id)",
    # 2026-12-31_log_d4.sql
    "ALTER TABLE log_entries ADD COLUMN reason_severity SMALLINT NOT NULL DEFAULT 0",
    "CREATE INDEX log_entries_post_idx ON log_entries (target_submission_id) WHERE target_submission_id IS NOT NULL",
    "CREATE INDEX log_entries_comment_idx ON log_entries (target_comment_id) WHERE target_comment_id IS NOT NULL",
    "CREATE INDEX log_entries_order_idx ON log_entries (created_utc DESC, id DESC)",
    # the word filter's list, which rates a reason when it is written (empty: the built-in list). schema.sql `word_filter_entries`
    "CREATE TABLE word_filter_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, word VARCHAR(100), severity SMALLINT, mode VARCHAR(10), "
    "variants VARCHAR(500), suffixes VARCHAR(500), language VARCHAR(8), enabled BOOLEAN DEFAULT 1)",
)

# the guild mod log and the rows the after-the-fact copy reads (schema.sql `modactions`)
MODACTIONS = ("CREATE TABLE modactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, board_id INTEGER, "
              "target_user_id INTEGER, target_submission_id INTEGER, target_comment_id INTEGER, created_utc INTEGER DEFAULT 0, "
              "kind VARCHAR(32), _note VARCHAR(256))")


def migration_statements(sql):
    """The CREATE statements of 2026-12-09_action_log.sql (comments stripped) in SQLite's words, with the role CHECK as
    2026-12-30_log_paper_trail.sql leaves it (SQLite cannot replace a constraint, and Postgres does)."""
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    for statement in (part.strip() for part in sql.split(";")):
        if statement.startswith("CREATE"):
            yield (statement.replace("public.", "").replace("IF NOT EXISTS ", "")
                   .replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
                   .replace("('guildmaster', 'admin', 'system')", "('guildmaster', 'admin', 'system', 'author')"))


def apply(conn, *, modactions=False):
    """Add the later migrations' columns and tables to a database built from 2026-12-09_action_log.sql (and the guild mod log's
    own table when a test reads it)."""
    for statement in LATER:
        conn.execute(text(statement))
    if modactions:
        conn.execute(text(MODACTIONS))
