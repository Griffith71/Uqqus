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
- `translations`: kept so a text is translated once per language (helpers/translation_store.py), for
  `TRANSLATION_DAYS`; one asked for after that is made again.
- `site_feedback_images`: a screenshot sent with feedback (helpers/site_feedback.py) goes `FEEDBACK_IMAGE_DAYS` after
  the admins closed the item (fixed or declined). An item nobody has closed keeps its screenshot.
- `signup_reviews`: what the signup guard thought of a sign-up (helpers/signup_review_store.py) goes
  `SIGNUP_REVIEW_DAYS` after it was decided (or let through). A row whose account is still held stays.
- `site_events` (helpers/site_events.py) that ended more than `EVENT_DAYS` ago go with their change log: a score, an alert, a
  countdown. (Reloading the seed file (helpers/happening_seed.json) puts back any entry still in it, so an entry that is over is
  taken out of the file when the file is reviewed.)
- `log_snapshots`: the private copy of what was deleted, hidden or removed (helpers/action_log.py) loses its text on its date
  (`SNAPSHOT_DAYS` after the action, a year for illegal content); the row and the Log entry stay. The delete dialogs promise 90 days.
- `site_traffic`: the daily counts of requests (helpers/traffic.py) go after `TRAFFIC_DAYS`; `site_traffic_flushes`, which
  only stops a flush being counted twice, after `TRAFFIC_BATCH_DAYS`.
- `ended_sign_ins`: sign-ins that ended (helpers/sign_in_ends.py) go after `ENDED_SIGN_IN_DAYS`, once no cookie written back
  can still be carrying one.
- `chat_device_signouts`: a chat sign-out (helpers/chat_signouts.py) goes `CHAT_SIGNOUT_DAYS` after it was done; one still pending
  stays, however old (it is retried for ever).
"""
import time

from sqlalchemy import text

from ruqqus.helpers.collab_rules import RESYNC_AFTER

QUIET_DRAFT_DAYS = 30
DELETED_UPDATE_DAYS = 90
VIEW_DAYS_KEPT = 400
BELL_DAYS = 90                  # helpers/bell.py KEEP_DAYS
LOGIN_ADDRESS_DAYS = 90         # helpers/guild_limits.py WINDOW
GUILD_COUNTER_DAYS = 400         # helpers/guild_insights.py KEEP_DAYS
LOGIN_EVENT_DAYS = 410          # more than helpers/regions.py LOOKBACK_DAYS (400)
TRANSLATION_DAYS = 90           # helpers/translation_store.py: a kept translation is made again when asked after
FEEDBACK_IMAGE_DAYS = 30        # helpers/site_feedback.py: a month after the item was closed
EVENT_DAYS = 400                # helpers/site_events.py: an event that ended over a year ago
SIGNUP_REVIEW_DAYS = 90         # helpers/signup_review_store.py KEEP_DAYS: what the signup guard thought, once decided
TRAFFIC_DAYS = 400              # helpers/traffic.py KEEP_DAYS
TRAFFIC_BATCH_DAYS = 14         # more than helpers/traffic_store.py LIVE_DAYS: a batch can wait in Redis that long
ENDED_SIGN_IN_DAYS = 400        # helpers/sign_in_ends.py KEEP_DAYS: longer than a cookie written back can last (365 days)
CHAT_SIGNOUT_DAYS = 30          # helpers/chat_signouts.py KEEP_DAYS: after it was done
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
    ("old translations", "DELETE FROM translations WHERE created_utc < :translations"),
    ("change log of old events", "DELETE FROM site_event_log WHERE event_id IN (SELECT id FROM site_events WHERE "
                                 "(date_kind = 'instant' AND COALESCE(ends_utc, starts_utc) < :events) OR "
                                 "(date_kind = 'civil' AND COALESCE(ends_date, starts_date) < :events_day))"),
    ("old events", "DELETE FROM site_events WHERE (date_kind = 'instant' AND COALESCE(ends_utc, starts_utc) < :events) OR "
                   "(date_kind = 'civil' AND COALESCE(ends_date, starts_date) < :events_day)"),
    ("screenshots of closed feedback", "DELETE FROM site_feedback_images WHERE feedback_id IN "
                                       "(SELECT id FROM site_feedback WHERE resolved_utc > 0 AND resolved_utc < :feedback_images)"),
    ("decided sign-up reviews", "DELETE FROM signup_reviews WHERE decision <> '' AND "
                                "CASE WHEN decided_utc > 0 THEN decided_utc ELSE created_utc END < :signup_reviews"),
    ("private copies past their date", "UPDATE log_snapshots SET title = NULL, url = NULL, body = NULL, body_html = NULL, "
                                       "scrubbed_utc = :now WHERE scrubbed_utc = 0 AND expires_utc > 0 AND expires_utc <= :now"),
    ("old traffic counts", "DELETE FROM site_traffic WHERE day < :traffic_day"),
    ("old traffic batches", "DELETE FROM site_traffic_flushes WHERE flushed_utc < :traffic_batches"),
    ("old ended sign-ins", "DELETE FROM ended_sign_ins WHERE ended_utc < :ended_sign_ins"),
    ("finished chat sign-outs", "DELETE FROM chat_device_signouts WHERE done_utc > 0 AND done_utc < :chat_signouts"),
)


def prune(db, now):
    """Applies every rule and commits; returns {rule: rows} (-1 for a rule that failed, e.g. a missing table)."""
    params = {"now": now, "window": RESYNC_AFTER, "quiet": now - QUIET_DRAFT_DAYS * DAY,
              "deleted": now - DELETED_UPDATE_DAYS * DAY, "day": now // DAY - VIEW_DAYS_KEPT,
              "bells": now - BELL_DAYS * DAY, "addresses": now - LOGIN_ADDRESS_DAYS * DAY,
              "sign_ins": now - LOGIN_EVENT_DAYS * DAY,
              "guild_day": now // DAY - GUILD_COUNTER_DAYS, "translations": now - TRANSLATION_DAYS * DAY,
              "feedback_images": now - FEEDBACK_IMAGE_DAYS * DAY,
              "signup_reviews": now - SIGNUP_REVIEW_DAYS * DAY,
              "traffic_day": now // DAY - TRAFFIC_DAYS, "traffic_batches": now - TRAFFIC_BATCH_DAYS * DAY,
              "ended_sign_ins": now - ENDED_SIGN_IN_DAYS * DAY,
              "chat_signouts": now - CHAT_SIGNOUT_DAYS * DAY,
              "events": now - EVENT_DAYS * DAY, "events_day": time.strftime("%Y-%m-%d", time.gmtime(now - EVENT_DAYS * DAY))}
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
