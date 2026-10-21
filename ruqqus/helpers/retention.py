"""How long records that grow with every keystroke or view are kept (audit D-08). A step of scripts/recomputes.py's
cycle; plain `text()` SQL, each rule in its own savepoint (tests/test_retention.py).

- `collab_ops`, the record of every edit to a shared draft (text deleted before publishing included). The text and
  who wrote each part live on the draft itself (`collab_docs`: the text and its authorship runs), so an op is only
  needed by a browser catching up. A browser more than `RESYNC_AFTER` revisions behind reloads the text instead, so
  older ops are never read; a draft nobody has edited for `QUIET_DRAFT_DAYS` keeps none (a browser left open that
  long is at the current revision or reloads), and a closed draft keeps none.
- `live_updates` deleted by their writer: the text goes after `DELETED_UPDATE_DAYS`, as a deleted post's does.
- `post_view_days`: insights show the last 28 days, so days older than `VIEW_DAYS_KEPT` go.
- `bell_rings`: which things already rang the bells (helpers/bell.py), kept `BELL_DAYS`.
- `login_events`: the address and the browser text of a sign-in go after `LOGIN_ADDRESS_DAYS` (the 90 days the address
  checks look back, helpers/guild_limits.py), the row after `LOGIN_EVENT_DAYS`. **Not sooner than 400 days**: the
  region logic (helpers/regions.py `LOOKBACK_DAYS`) reads a year of country and region to decide whether an account
  has moved, and pruning earlier would stop regions ever migrating (tests/test_retention.py checks the two agree).
"""
from sqlalchemy import text

from ruqqus.helpers.collab_rules import RESYNC_AFTER

QUIET_DRAFT_DAYS = 30
DELETED_UPDATE_DAYS = 90
VIEW_DAYS_KEPT = 400
BELL_DAYS = 90                  # helpers/bell.py KEEP_DAYS
LOGIN_ADDRESS_DAYS = 90         # helpers/guild_limits.py WINDOW
GUILD_COUNTER_DAYS = 400         # helpers/guild_insights.py KEEP_DAYS
LOGIN_EVENT_DAYS = 410          # more than helpers/regions.py LOOKBACK_DAYS (400)
DAY = 86400

RULES = (
    ("old shared-draft edits", "DELETE FROM collab_ops WHERE doc_id IN (SELECT id FROM collab_docs) AND revision <= "
                               "(SELECT d.revision FROM collab_docs d WHERE d.id = collab_ops.doc_id) - :window"),
    ("edits of quiet or closed drafts", "DELETE FROM collab_ops WHERE doc_id IN (SELECT id FROM collab_docs "
                                        "WHERE deleted_utc > 0 OR updated_utc < :quiet)"),
    ("edits of drafts that are gone", "DELETE FROM collab_ops WHERE doc_id NOT IN (SELECT id FROM collab_docs)"),
    ("deleted live updates", "UPDATE live_updates SET body = '', body_html = '' "
                             "WHERE deleted_utc > 0 AND deleted_utc < :deleted AND body <> ''"),
    ("old view counts", "DELETE FROM post_view_days WHERE day < :day"),
    ("old bell rings", "DELETE FROM bell_rings WHERE created_utc < :bells"),
    ("old guild counters", "DELETE FROM guild_day_stats WHERE day < :guild_day"),
    ("old sign-in addresses", "UPDATE login_events SET ip = NULL, user_agent = NULL "
                              "WHERE created_utc < :addresses AND (ip IS NOT NULL OR user_agent IS NOT NULL)"),
    ("old sign-ins", "DELETE FROM login_events WHERE created_utc < :sign_ins"),
)


def prune(db, now):
    """Applies every rule and commits; returns {rule: rows} (-1 for a rule that failed, e.g. a missing table)."""
    params = {"window": RESYNC_AFTER, "quiet": now - QUIET_DRAFT_DAYS * DAY,
              "deleted": now - DELETED_UPDATE_DAYS * DAY, "day": now // DAY - VIEW_DAYS_KEPT,
              "bells": now - BELL_DAYS * DAY, "addresses": now - LOGIN_ADDRESS_DAYS * DAY,
              "sign_ins": now - LOGIN_EVENT_DAYS * DAY,
              "guild_day": now // DAY - GUILD_COUNTER_DAYS}
    done = {}
    for name, sql in RULES:
        savepoint = db.begin_nested()
        try:
            done[name] = db.execute(text(sql), params).rowcount
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            done[name] = -1
    db.commit()
    return done
