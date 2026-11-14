"""What an account's deletion does to everything it wrote (the Log plan's decision 11; slice D3c): it becomes anonymous and **stays on the site**, as
Anonymous, in place of "[deleted user]". Nothing is removed.

`close_account` queues the account here (`account_anonymise_jobs`, one row per account); `close_own_account` runs the first batch before it answers, so an
ordinary account is anonymous at once, and `scripts/recomputes.py` runs what is left each cycle. **It is resumable by construction**: what remains is
always "this author's rows that are not yet anonymous", so a batch that stopped halfway is simply asked again, every batch commits on its own, and a
job that was already done finds nothing and is marked done. Idempotent, so the later slices (the aliases of D3e, a shared post's credit of D3f) add
their steps here and run over accounts that were closed before them.

It does what "Remove my name" does for one item (`helpers/anonymise.py`) without that action's refusals: an account's closing must always be able to
anonymise whatever it wrote, a Circle post and a tipped post included. Plain `text()` SQL, tested on SQLite (tests/test_account_anonymise.py).
The ORM-free statements here clear what would give the author away (a distinguish, a herald) and take the items out of every entry that pairs them
with their author; the caches that list by author are cleared by the caller, which has the app.
"""
from sqlalchemy import text

from ruqqus.helpers import anon_aliases, anonymise

BATCH = 200                     # posts, and as many comments, in one batch (one commit)
JOBS_PER_CYCLE = 5              # accounts one cycle of `scripts/recomputes.py` works on
BATCHES_PER_JOB = 25            # so one cycle does at most 5,000 posts and 5,000 comments of one account, then moves on
KEEP_DONE_DAYS = 90             # a finished job's row is kept for this long, then retention removes it (helpers/retention.py ANONYMISE_JOB_DAYS)

QUEUE_SQL = ("INSERT INTO account_anonymise_jobs (user_id, queued_utc) VALUES (:u, :now) ON CONFLICT (user_id) DO NOTHING")


def tables_ready(db):
    """Has the migration been applied? A database that hasn't reached it queues nothing and the deletion goes on without it."""
    savepoint = db.begin_nested()           # in a savepoint: a request's own transaction is never rolled back by this question
    try:
        db.execute(text("SELECT 1 FROM account_anonymise_jobs LIMIT 1"))
        savepoint.commit()
        return True
    except Exception:
        savepoint.rollback()
        return False


def _marks(prefix, ids):
    names = {f"{prefix}{n}": int(i) for n, i in enumerate(ids)}
    return ", ".join(f":{n}" for n in names), names


def batch(db, user_id, size=BATCH):
    """Anonymise up to `size` posts and `size` comments of this account that are not anonymous yet. Returns `(posts, comments)` changed. Does not
    commit. Every kind of row goes: a post, its forwarded copies and reposts under the account's id, a Space's hidden row, a draft's published post."""
    posts = [r[0] for r in db.execute(text(
        "SELECT id FROM submissions WHERE author_id = :u AND NOT COALESCE(is_anonymous, false) ORDER BY id LIMIT :n"), {"u": int(user_id), "n": size})]
    if posts:
        marks, names = _marks("p", posts)
        db.execute(text(f"UPDATE submissions SET is_anonymous = :yes, distinguish_level = 0, gm_distinguish = 0 WHERE id IN ({marks})"), {**names, "yes": True})
        anonymise.strip_pointers(db, author_id=user_id, post_ids=posts)
        for scope_id in {r[0] for r in db.execute(text(f"SELECT COALESCE(NULLIF(repost_id, 0), id) FROM submissions WHERE id IN ({marks})"), names)}:
            anon_aliases.give(db, (anon_aliases.POST, scope_id), user_id)         # a number in each thread it wrote in, given now (decision 23)
    comments = [r[0] for r in db.execute(text(
        "SELECT id FROM comments WHERE author_id = :u AND NOT COALESCE(is_anonymous, false) ORDER BY id LIMIT :n"), {"u": int(user_id), "n": size})]
    if comments:
        marks, names = _marks("c", comments)
        db.execute(text(f"UPDATE comments SET is_anonymous = :yes WHERE id IN ({marks})"), {**names, "yes": True})
        anonymise.strip_pointers(db, author_id=user_id, comment_ids=comments)
        for scope_id in {r[0] for r in db.execute(text(
                f"SELECT COALESCE(NULLIF(s.repost_id, 0), s.id) FROM comments c JOIN submissions s ON s.id = c.parent_submission WHERE c.id IN ({marks})"), names)}:
            anon_aliases.give(db, (anon_aliases.POST, scope_id), user_id)
    return len(posts), len(comments)


def credits(db, user_id):
    """A number for this account in every thread it co-authored in: its accepted co-author credits are anonymous (`close_account` made them so) and each gets its
    number there now, at the moment it becomes anonymous (decision 23). Idempotent: `give` finds a number already given. Returns how many credits were visited."""
    posts = [r[0] for r in db.execute(text("SELECT post_id FROM post_coauthors WHERE user_id = :u AND status = 'accepted' AND is_anonymous"), {"u": int(user_id)})]
    for post_id in posts:
        anon_aliases.give(db, (anon_aliases.POST, post_id), user_id)
    return len(posts)


def remaining(db, user_id):
    """Is there anything of this account's left to anonymise?"""
    for table in ("submissions", "comments"):
        if db.execute(text(f"SELECT 1 FROM {table} WHERE author_id = :u AND NOT COALESCE(is_anonymous, false) LIMIT 1"), {"u": int(user_id)}).first():
            return True
    return False


def run_one(db, user_id, now, *, batches=BATCHES_PER_JOB, size=BATCH, commit=True):
    """Work one account's job: batches until nothing is left or `batches` ran, each committed on its own (a failed one is rolled back and the job is
    asked again later). Marks the job done when nothing remains. Returns `(posts, comments, done)`."""
    posts_total = comments_total = 0
    credits(db, user_id)
    for _ in range(max(int(batches), 1)):
        posts, comments = batch(db, user_id, size)
        posts_total += posts
        comments_total += comments
        if commit:
            db.commit()
        if posts < size and comments < size:
            break
    done = not remaining(db, user_id)
    db.execute(text("UPDATE account_anonymise_jobs SET updated_utc = :now, tries = tries + 1, "
                    "done_utc = CASE WHEN :done THEN :now ELSE 0 END WHERE user_id = :u"), {"now": int(now), "u": int(user_id), "done": done})
    if commit:
        db.commit()
    return posts_total, comments_total, done


def pending(db, limit=JOBS_PER_CYCLE):
    """The accounts still being anonymised, oldest first."""
    return [r[0] for r in db.execute(text("SELECT user_id FROM account_anonymise_jobs WHERE done_utc = 0 ORDER BY queued_utc, user_id LIMIT :n"),
                                     {"n": int(limit)})]


def run_due(db, now, limit=JOBS_PER_CYCLE):
    """One cycle's work: the oldest pending accounts, each in its own transaction so one that fails is left for the next cycle. Returns
    `(accounts finished, items changed, accounts that failed)`."""
    finished = changed = failed = 0
    for user_id in pending(db, limit):
        try:
            posts, comments, done = run_one(db, user_id, now)
            changed += posts + comments
            finished += 1 if done else 0
        except Exception:
            db.rollback()
            failed += 1
    return finished, changed, failed
