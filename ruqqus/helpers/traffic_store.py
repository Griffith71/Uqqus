"""The counts of requests (helpers/traffic.py) in Redis and in `site_traffic`. Plain `text()` SQL, tested on SQLite with the
tables built from the migration (tests/test_traffic.py). Nothing here selects or stores who: the only things that ever
reach Redis or the table are a day, a kind of page, a referring host and a country, each with a number.

**In Redis**, per UTC day: a hash (`k:<kind>`, `c:<country>`, `r:direct`, `r:internal`), whose fields can't be more than
the kinds, 676 countries and a few more, and a sorted set of referring hosts trimmed to the `LIVE_HOSTS` largest in the same
transaction as every write, so a bot sending random Referers can't grow it whatever it does between flushes. The set also
holds `TOTAL`, the number of requests from any other site: it can't be trimmed (it is the largest), and at the flush what
it holds beyond the hosts still there is `other`, which is why a host trimmed away is not a request lost. Every key expires
`LIVE_DAYS` + 1 after its last write, so a job that stopped leaves a bounded amount behind and no more.

**The flush** (`flush`, a step of scripts/recomputes.py) moves those keys aside with RENAMENX, so that what arrives during a
flush lands in a new key and is kept for the next one, reads them, and writes them in one transaction with a row in
`site_traffic_flushes` naming the batch. What fails half way:
- before the commit (the database is down, a bad row): nothing is in the table and the batch stays aside. The next flush
  reads it again. Nothing lost, nothing counted twice.
- after the commit, before Redis was tidied: the ledger has the batch, so the next flush only deletes it. Not counted twice.
- Redis fails while moving keys: the batch name stays in `POINTER` and the next flush carries on with it.
- A batch that can't be written for `LIVE_DAYS` + 1 days expires in Redis: those counts are lost, and Redis stays bounded."""
import secrets

from sqlalchemy import text

from ruqqus.helpers import traffic as rules

LIVE_DAYS = 8
POINTER = "traffic:batch"
FLUSH_LOCK = "traffic:flushing"
FLUSH_LOCK_SECONDS = 300
TOTAL = "*"                     # not something rules.referrer_of can ever answer
FIXED = "fixed"
HOSTS = "hosts"
PARTS = (FIXED, HOSTS)

_ADD_COUNT = text("INSERT INTO site_traffic (day, dimension, value, requests) VALUES (:day, :dimension, :value, :n) "
                  "ON CONFLICT (day, dimension, value) DO UPDATE SET requests = site_traffic.requests + :n")
_SET_COUNT = text("INSERT INTO site_traffic (day, dimension, value, requests) VALUES (:day, 'referrer', :value, :n) "
                  "ON CONFLICT (day, dimension, value) DO UPDATE SET requests = :n")
_REFERRERS = text("SELECT value, requests FROM site_traffic WHERE day = :day AND dimension = 'referrer'")
_DROP_REFERRER = text("DELETE FROM site_traffic WHERE day = :day AND dimension = 'referrer' AND value = :value")
_CLAIM_BATCH = text("INSERT INTO site_traffic_flushes (batch, flushed_utc) VALUES (:batch, :now) "
                    "ON CONFLICT (batch) DO NOTHING")
_BATCH_DONE = text("SELECT 1 FROM site_traffic_flushes WHERE batch = :batch")
_LAST_FLUSH = text("SELECT MAX(flushed_utc) FROM site_traffic_flushes")
_PER_DAY = text("SELECT day, SUM(requests) FROM site_traffic WHERE dimension = 'kind' AND day BETWEEN :lo AND :hi GROUP BY day")
_TOTALS = text("SELECT dimension, value, SUM(requests) FROM site_traffic WHERE day BETWEEN :lo AND :hi GROUP BY dimension, value")
# what is counted beside the requests, from the site's own tables: how many, per UTC day, never who. Forwards aren't posts: a
# forwarded post has its repost_id, and a forwarded comment is a post with none, known by its comment_forwardrels row (its
# words are already under Comments)
_SIGNUPS = text("SELECT created_utc / 86400, COUNT(*) FROM users WHERE created_utc >= :since AND created_utc < :until "
                "AND (is_bot IS NULL OR is_bot = :no) GROUP BY created_utc / 86400")
_POSTS = text("SELECT created_utc / 86400, COUNT(*) FROM submissions WHERE created_utc >= :since AND created_utc < :until "
              "AND COALESCE(repost_id, 0) = 0 AND is_space = :no AND (is_bot IS NULL OR is_bot = :no) "
              "AND NOT EXISTS (SELECT 1 FROM comment_forwardrels f WHERE f.promoted_submission_id = submissions.id) "
              "GROUP BY created_utc / 86400")
_COMMENTS = text("SELECT created_utc / 86400, COUNT(*) FROM comments WHERE created_utc >= :since AND created_utc < :until "
                 "AND parent_submission IS NOT NULL AND (is_bot IS NULL OR is_bot = :no) GROUP BY created_utc / 86400")


def live_key(day, part):
    return f"traffic:live:{day}:{part}"


def held_key(batch, day, part):
    return f"traffic:held:{batch}:{day}:{part}"


# ---- counting: Redis only ----

def record(r, hits):
    """Adds a batch of `Hit`s in one transaction: one round trip however many there are, and the set of hosts is back within
    its cap before anyone else can see it."""
    pipe = r.pipeline(transaction=True)
    days = set()
    for hit in hits:
        days.add(hit.day)
        fixed, hosts = live_key(hit.day, FIXED), live_key(hit.day, HOSTS)
        pipe.hincrby(fixed, f"k:{hit.kind}", 1)
        pipe.hincrby(fixed, f"c:{hit.country}", 1)
        if hit.referrer in (rules.DIRECT, rules.INTERNAL):
            pipe.hincrby(fixed, f"r:{hit.referrer}", 1)
        elif hit.referrer == rules.OTHER:
            pipe.zincrby(hosts, 1, TOTAL)
        else:
            pipe.zincrby(hosts, 1, hit.referrer)
            pipe.zincrby(hosts, 1, TOTAL)
    for day in days:
        # keeps the largest LIVE_HOSTS and TOTAL, which every request from another site adds to
        pipe.zremrangebyrank(live_key(day, HOSTS), 0, -(rules.LIVE_HOSTS + 2))
        pipe.expire(live_key(day, FIXED), (LIVE_DAYS + 1) * rules.DAY)
        pipe.expire(live_key(day, HOSTS), (LIVE_DAYS + 1) * rules.DAY)
    pipe.execute()


def counts_of(fixed, hosts):
    """What one day's two Redis keys hold, as {"kind": {...}, "country": {...}, "referrer": {...}}. `hosts` is
    [(member, score)]; the requests from other sites that aren't under a host any more are `other`."""
    out = {"kind": {}, "country": {}, "referrer": {}}
    names = {"k": "kind", "c": "country", "r": "referrer"}
    for field, n in fixed.items():
        prefix, _, value = rules.text_of(field).partition(":")
        if prefix in names and value and int(n) > 0:
            out[names[prefix]][value] = int(n)
    named = 0
    total = 0
    for member, score in hosts:
        member = rules.text_of(member)
        if member == TOTAL:
            total = int(score)
        elif int(score) > 0:
            out["referrer"][member] = int(score)
            named += int(score)
    if total > named:
        out["referrer"][rules.OTHER] = total - named
    return out


def _move_aside(r, live, held):
    """RENAMENX: `held` left over from a flush that failed is not written over, and what is live waits for the next one."""
    if not r.exists(live):
        return
    try:
        r.renamenx(live, held)
    except Exception as error:      # it expired between the two calls
        if "no such key" not in str(error).lower():
            raise


def _forget(r, batch, days):
    r.delete(*[held_key(batch, day, part) for day in days for part in PARTS])
    r.delete(POINTER)


def _write_day(db, day, counts):
    for dimension in ("kind", "country"):
        for value, n in counts[dimension].items():
            db.execute(_ADD_COUNT, {"day": day, "dimension": dimension, "value": value, "n": n})
    if not counts["referrer"]:
        return
    existing = {value: int(n) for value, n in db.execute(_REFERRERS, {"day": day}).all()}
    merged = rules.merge_referrers(existing, counts["referrer"])
    for value in set(existing) - set(merged):
        db.execute(_DROP_REFERRER, {"day": day, "value": value})
    for value, n in merged.items():
        db.execute(_SET_COUNT, {"day": day, "value": value, "n": n})


def flush(db, r, now):
    """Moves what Redis holds into the table; answers how many requests that was (None when another flush holds the lock).
    Raises on a failure, having changed nothing that the next flush can't finish: see the top of this file."""
    if not r.set(FLUSH_LOCK, "1", nx=True, ex=FLUSH_LOCK_SECONDS):
        return None
    try:
        return _flush(db, r, now)
    finally:
        r.delete(FLUSH_LOCK)


def _flush(db, r, now):
    today = rules.day_of(now)
    days = range(today - LIVE_DAYS, today + 1)
    batch = rules.text_of(r.get(POINTER))
    if batch and db.execute(_BATCH_DONE, {"batch": batch}).first():
        # the last flush committed and could not tidy up. Checked BEFORE moving anything new: what was moved into a batch
        # that is already in the table would be deleted with it
        _forget(r, batch, days)
        batch = None
    if not batch:
        batch = secrets.token_hex(8)
        r.set(POINTER, batch)
    for day in days:
        for part in PARTS:
            _move_aside(r, live_key(day, part), held_key(batch, day, part))
    read = {}
    for day in days:
        counts = counts_of(r.hgetall(held_key(batch, day, FIXED)),
                           r.zrange(held_key(batch, day, HOSTS), 0, -1, withscores=True))
        if any(counts.values()):
            read[day] = counts
    requests = sum(sum(counts["kind"].values()) for counts in read.values())
    if read:
        try:
            if db.execute(_CLAIM_BATCH, {"batch": batch, "now": int(now)}).rowcount:
                for day, counts in read.items():
                    _write_day(db, day, counts)
            db.commit()
        except Exception:
            db.rollback()
            raise
    _forget(r, batch, days)
    return requests


# ---- the report ----

def _per_day(db, statement, lo, hi):
    return {int(day): int(n) for day, n in db.execute(statement, {"since": lo * rules.DAY, "until": (hi + 1) * rules.DAY,
                                                                  "no": False}).all()}


def report(db, now, days):
    """The admin page's numbers for the last `days` UTC days."""
    lo, hi = rules.window(now, days)
    requests = {int(day): int(n) for day, n in db.execute(_PER_DAY, {"lo": lo, "hi": hi}).all()}
    signups = _per_day(db, _SIGNUPS, lo, hi)
    posts = _per_day(db, _POSTS, lo, hi)
    comments = _per_day(db, _COMMENTS, lo, hi)
    sums = {"kind": {}, "referrer": {}, "country": {}}
    for dimension, value, n in db.execute(_TOTALS, {"lo": lo, "hi": hi}).all():
        if dimension in sums:
            sums[dimension][value] = int(n)
    span = range(lo, hi + 1)
    heights = rules.bar_heights([requests.get(day, 0) for day in span])
    series = [{"day": day, "label": rules.day_label(day), "requests": requests.get(day, 0), "signups": signups.get(day, 0),
               "posts": posts.get(day, 0), "comments": comments.get(day, 0), "height": height}
              for day, height in zip(span, heights)]
    total = sum(row["requests"] for row in series)
    last = db.execute(_LAST_FLUSH).scalar()
    return {"days": days, "series": series, "requests": total, "signups": sum(row["signups"] for row in series),
            "posts": sum(row["posts"] for row in series), "comments": sum(row["comments"] for row in series),
            "kinds": sums["kind"], "referrers": sums["referrer"], "countries": sums["country"],
            "last_flush_utc": int(last) if last else 0}
