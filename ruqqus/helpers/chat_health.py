"""Whether the site's database and Synapse's still belong together (chat plan, phase 1).

Matrix ids come from user ids (`@ruqqus_<id>`). Restore one database without the other, or point two site databases at
one Synapse, and the site hands a new member an id Synapse already gave someone else: chat then refuses them
(`OrphanMatrixAccount`), as it did on 2026-11-05. Three things keep that from happening silently:

- **A high-water mark on Synapse**: the highest user id ever given a chat account, in the bot's own account data
  (`matrix_client.ID_MARK_TYPE`), written BEFORE each account is made (`matrix_client.note_chat_id`). It lives in
  Synapse's database, so a restore of the site's database alone can't take it back.
- **New ids start above it**: `check` raises `users_id_seq` past the mark (at start-up, every cycle of
  `scripts/recomputes.py` and when chat is refused), so an id that ever had a chat account is never given out again.
- **A split is reported**: a sample of the chat accounts the site knows is looked up on Synapse; missing ones mean
  Synapse's database is older than the site's, or belongs to another site. `/admin/chat` shows it in red, the start-up
  log prints it, and `scripts/chat_pair_check.sh` checks a backup pair the same way.

The rules here are pure; the Matrix and Redis calls are passed in, so they are tested without a chat server."""
import json
import os

SAMPLE_NEWEST = 10
SAMPLE_RANDOM = 10
REFUSED_KEY = "chat:refused"          # user ids chat was refused to because Synapse had their id (a Redis set)
REFUSED_SECONDS = 30 * 86400          # the set goes if nobody is refused for this long (a flag is also cleared as soon as its cause is gone)
REFUSED_LOOKUPS = 20                  # refused ids asked of the chat server in one run
HEALTH_KEY = "chat:health"            # the last report, for the admin page
HEALTH_SECONDS = 900


def site_key(url=None):
    """Which site database this is, in the mark: the database's name (`postgres`, `ui_preview`). Two site databases on
    one Synapse are told apart by it; a restored copy of one is the same database, older."""
    url = url if url is not None else os.environ.get("DATABASE_URL", "")
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return name or "site"


def high_of(mark):
    try:
        return max(0, int((mark or {}).get("high") or 0))
    except (TypeError, ValueError):
        return 0


def raised_mark(mark, user_id, site, now):
    """The mark after `user_id` was given a chat account by `site`: never lower than it was, overall or for the site."""
    mark = dict(mark or {})
    sites = {k: dict(v) for k, v in (mark.get("sites") or {}).items() if isinstance(v, dict)}
    user_id = int(user_id)
    here = sites.get(site, {})
    if high_of(mark) >= user_id and high_of(here) >= user_id:
        return mark
    here["high"] = max(high_of(here), user_id)
    here["seen_utc"] = int(now)
    sites[site] = here
    return {"high": max(high_of(mark), user_id), "sites": sites}


def must_raise(next_id, high):
    """Whether the next id the site would give out has had a chat account already."""
    return high > 0 and int(next_id) <= int(high)


def next_user_id(db):
    """The id the next account will get (Postgres)."""
    from sqlalchemy import text
    return int(db.execute(text("SELECT CASE WHEN is_called THEN last_value + 1 ELSE last_value END FROM users_id_seq"))
               .scalar())


def raise_user_ids(db, high):
    """New accounts start after `high`. Only ever raises (setval would lower it too)."""
    from sqlalchemy import text
    if not must_raise(next_user_id(db), high):
        return False
    db.execute(text("SELECT setval('users_id_seq', :h, true)"), {"h": int(high)})
    db.commit()
    return True


def _sample(db):
    from sqlalchemy import text
    rows = db.execute(text(
        "(SELECT user_id, matrix_user_id FROM chat_identities ORDER BY user_id DESC LIMIT :n) UNION "
        "(SELECT user_id, matrix_user_id FROM chat_identities ORDER BY random() LIMIT :m)"),
        {"n": SAMPLE_NEWEST, "m": SAMPLE_RANDOM}).fetchall()
    return sorted({(int(a), b) for a, b in rows})


def _known(db):
    from sqlalchemy import text
    row = db.execute(text("SELECT count(*), coalesce(max(user_id), 0) FROM chat_identities")).fetchone()
    return int(row[0]), int(row[1])


def check(db, *, read_mark, write_mark, account_exists, site, now, refused=(), sample=None, known=None, fix=True,
          raise_ids=None):
    """The report: {"ok", "problems", "notes", "mark", "sites", "known", "missing", "refused", "checked_utc"}.
    `problems` need an admin; `notes` say what was put right or is worth knowing. With `fix`, the mark is raised to the
    chat accounts the site knows (when they are all on Synapse: the first run after this shipped) and new ids are
    raised past the mark. Never fixes a split: that is a restore to make as a pair."""
    report = {"ok": True, "problems": [], "notes": [], "mark": 0, "sites": [], "known": 0, "missing": [],
              "refused": sorted({int(x) for x in refused}), "checked_utc": int(now), "site": site}
    try:
        mark = read_mark()
    except Exception as e:
        report["ok"] = False
        report["problems"].append(f"The chat server could not be asked ({type(e).__name__}): chat is down, and the "
                                  "checks below were not made.")
        return report
    count, high_known = known if known is not None else _known(db)
    report["known"] = count
    pairs = sample if sample is not None else _sample(db)
    missing = []
    for user_id, mxid in pairs:
        try:
            if not account_exists(mxid):
                missing.append(user_id)
        except Exception as e:
            report["ok"] = False
            report["problems"].append(f"The chat server could not be asked about accounts ({type(e).__name__}).")
            return report
    report["missing"] = missing
    if missing:
        report["problems"].append(
            f"{len(missing)} of the {len(pairs)} chat accounts checked are on the site but not on the chat server "
            f"(user ids {', '.join(map(str, missing[:20]))}). Synapse's database is older than the site's, or belongs "
            "to another site: restore the two as a pair (RESTORE.md). Until then those members can't open their chats.")
    elif fix and high_known > high_of((mark.get("sites") or {}).get(site)):
        mark = raised_mark(mark, high_known, site, now)
        write_mark(mark)
        report["notes"].append(f"Recorded on the chat server that ids up to {high_known} have chat accounts.")
    high = high_of(mark)
    report["mark"] = high
    report["sites"] = sorted((mark.get("sites") or {}).keys())
    if fix and raise_ids is not None and raise_ids(high):
        report["notes"].append(
            f"New accounts now start after id {high}: ids up to it were already given chat accounts, by a newer copy of "
            "this database or by another site database on the same chat server.")
    others = [s for s in report["sites"] if s != site]
    if others:
        report["notes"].append(
            f"This chat server is also used by another site database ({', '.join(others)}). That is how a development "
            "machine is set up; on a live site each site has its own chat server.")
    if report["refused"]:
        report["problems"].append(
            f"Chat was refused to {len(report['refused'])} member(s), because the chat server already has an account "
            f"with their id that the site has no record of (user ids {', '.join(map(str, report['refused'][:20]))}). "
            "If the site's database was restored without Synapse's, restore the pair; if each one is the same person "
            "who used chat before, give the account back below.")
    report["ok"] = not report["problems"]
    return report


def settle_refused(refused, have, live, account_exists, mxid_for, limit=REFUSED_LOOKUPS):
    """(still_refused, cleared) from the refused ids. A flag is cleared when its cause is gone, or it would stay a red alarm for ever
    once the databases were restored as a pair or the stray account removed (the review of 2026-11-06, C1): the member now has a chat
    record here (`have`), is no longer an open account (`live` lacks them: deleted, or an id the restored database never had), or the
    chat server no longer has the account (asked for at most `limit` a run; an answer that fails keeps the flag)."""
    kept, cleared, asked = set(), set(), 0
    for user_id in sorted(refused):
        if user_id in have or user_id not in live:
            cleared.add(user_id)
            continue
        if asked < limit:
            asked += 1
            try:
                if not account_exists(mxid_for(user_id)):
                    cleared.add(user_id)
                    continue
            except Exception:
                pass
        kept.add(user_id)
    return kept, cleared


def run(db, redis=None, fix=True, now=None):
    """`check` against the real chat server, cached for the admin page. Returns the report."""
    import time
    import ruqqus.helpers.matrix_client as matrix
    now = int(now if now is not None else time.time())
    refused = set()
    if redis is not None:
        try:
            refused = {int(x) for x in redis.smembers(REFUSED_KEY)}
            if refused:
                from sqlalchemy import text
                ids = sorted(refused)
                have = {int(x) for (x,) in db.execute(text("SELECT user_id FROM chat_identities WHERE user_id = ANY(:ids)"),
                                                       {"ids": ids})}
                live = {int(x) for (x,) in db.execute(text("SELECT id FROM users WHERE id = ANY(:ids) AND is_deleted IS NOT TRUE"),
                                                       {"ids": ids})}
                refused, cleared = settle_refused(refused, have, live, matrix.account_exists, matrix.mxid_for)
                for user_id in cleared:
                    redis.srem(REFUSED_KEY, user_id)
        except Exception:
            pass

    def raise_under_lock(mark):
        """The first run after this shipped records the ids the site already has. Written through the same locked read-modify-write as
        a new account's (matrix_client.note_chat_id re-reads the mark inside the lock and only ever raises it): this run read the mark
        before asking Synapse about up to 20 accounts, and a first chat that raised it meanwhile must not be written over (C2). A busy
        lock or a failure leaves it to the next run."""
        try:
            matrix.note_chat_id(high_of((mark.get("sites") or {}).get(site_key())))
        except Exception:                       # the chat server, Redis or the lock: the next run does it (creating an account fails closed elsewhere)
            pass

    report = check(db, read_mark=matrix.read_id_mark, write_mark=raise_under_lock,
                   account_exists=matrix.account_exists, site=site_key(), now=now, refused=refused, fix=fix,
                   raise_ids=lambda high: raise_user_ids(db, high))
    if redis is not None:
        try:
            redis.set(HEALTH_KEY, json.dumps(report), ex=HEALTH_SECONDS)
        except Exception:
            pass
    return report


def cached(redis):
    if redis is None:
        return None
    try:
        raw = redis.get(HEALTH_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def note_refused(redis, user_id):
    """Remembers that chat was refused to `user_id`. True the first time (so the admins are told once)."""
    if redis is None:
        return False
    try:
        first = bool(redis.sadd(REFUSED_KEY, int(user_id)))
        redis.expire(REFUSED_KEY, REFUSED_SECONDS)
        return first
    except Exception:
        return False
