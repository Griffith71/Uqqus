"""What scripts/run_bots.py does with one due source, kept apart from Flask so it can be tested with stand-ins.

For each source: fetch it (conditional GET), parse it, pick the items not seen yet, and post them through the real
submit route as the bot (the caller's `submit`, which goes through POST /api/vue/submit in-process: every posting
rule, the throttle and bans apply as for a person; nothing here repeats submit_post). Then record what happened.

* The first read of a source marks its whole backlog seen and posts only its newest FIRST_READ items.
* After that, new items are posted oldest first, at most PER_RUN a run, within the bot's daily cap.
* A source that fails FAILS_BEFORE_PAUSE times in a row is paused and its owner told; failures back off.
* An answer that means the bot itself can't post (suspended, not approved any more) pauses the bot and tells the owner.
"""
from collections import namedtuple

from ruqqus.helpers import bot_store, bots, feed_items
from ruqqus.helpers.safe_fetch import FetchError

# kind: posted, duplicate, wait (try again next run), guild (a guild refused the forward: post again without it),
# refused (this item can't be posted: skip it), stop (the bot can't post at all: pause it)
Outcome = namedtuple("Outcome", "kind message post_id")


def outcome(kind, message="", post_id=None):
    return Outcome(kind, message, post_id)


def classify(status, location, data, forwarded, post_id=None):
    """What an answer of the submit route means for the bot."""
    data = data if isinstance(data, dict) else {}
    error = str(data.get("error") or "")[:300]
    if status == 200 and post_id:
        return outcome("posted", post_id=post_id)
    if status == 409:
        return outcome("duplicate", error)
    if status in (301, 302, 303, 307, 308):
        if "/notifications" in location:
            return outcome("stop", "The bot was suspended for posting too much similar content.")
        if "/help/terms" in location or "/login" in location:
            return outcome("stop", "The site refused the bot's session.")
        return outcome("duplicate", "Already posted.")
    if status == 403 and data.get("bot_refusal") == "wait":
        return outcome("wait", error)
    if status == 403 and data.get("bot_refusal") == "stop":
        return outcome("stop", error)
    if status in (403, 404) and forwarded and "+" in error:
        return outcome("guild", error)
    if status in (429, 503) or status >= 500:
        return outcome("wait", error or f"The site answered {status}; trying again later.")
    if status == 401:
        return outcome("stop", "The site refused the bot's session.")
    return outcome("refused", error or f"The site answered {status}.")


def run_source(db, source_id, now, fetch, submit, notify, owner_ok):
    """Read one claimed source and post what is new. Returns a short word for the log. Commits as it goes: the
    submit route commits the same session, so nothing of ours may be left pending across a submit."""
    src = bot_store.source(db, source_id)
    if src is None or src.status != bots.APPROVED:
        return "skipped"
    bot = bot_store.bot_of(db, src.bot_id)
    if bot is None or bot.status != bots.APPROVED:
        return "skipped"
    # a site bot belongs to nobody (the site runs it); a member bot stops while its member can't run it
    if bot.kind == bots.MEMBER and not owner_ok(bot.owner_id):
        bot_store.record_check(db, src.id, now, now + 3600, fail_count=src.fail_count,
                               error="The bot's owner can't run it right now.")
        db.commit()
        return "owner"

    try:
        answer = fetch(src.url, src.etag, src.last_modified)
        feed = feed_items.parse(answer.body, answer.content_type) if answer.changed else None
    except (FetchError, feed_items.FeedError) as error:
        return _failed(db, src, bot, now, error.message, notify)

    if feed is None:
        bot_store.record_check(db, src.id, now, bots.next_check(now, src.check_minutes, 0),
                               etag=answer.etag, last_modified=answer.last_modified)
        db.commit()
        return "unchanged"

    seen = bot_store.seen_keys(db, src.id, [item.key for item in feed.items])
    fresh = feed_items.newest_first([item for item in feed.items if item.key not in seen])
    if not src.read_once:
        for item in fresh[bots.FIRST_READ:]:                  # the backlog is never posted
            bot_store.mark_seen(db, src.id, item.key, None, now)
        fresh = fresh[:bots.FIRST_READ]
    to_post = list(reversed(fresh))[:bots.PER_RUN]            # oldest first
    db.commit()

    guilds = bot_store.guilds_of(bot)
    posted, note, result = 0, "", "read"
    for item in to_post:
        if bot_store.posts_today(db, bot.user_id, bots.utc_day(now)) >= bots.DAILY_CAP:
            note = f"Reached the limit of {bots.DAILY_CAP} posts a day."
            break
        answer_of = submit(bot, item, guilds)
        if answer_of.kind == "guild" and guilds:
            note = f"Posted without forwarding: {answer_of.message}"
            answer_of = submit(bot, item, [])
        if answer_of.kind == "posted":
            bot_store.mark_seen(db, src.id, item.key, answer_of.post_id, now)
            posted += 1
        elif answer_of.kind in ("duplicate", "refused", "guild"):
            bot_store.mark_seen(db, src.id, item.key, None, now)
            if answer_of.kind != "duplicate":
                note = answer_of.message
        elif answer_of.kind == "wait":
            note = answer_of.message
            break
        elif answer_of.kind == "stop":
            bot_store.set_running(db, bot.user_id, False, answer_of.message)
            db.commit()
            if bot.owner_id:                           # a site bot's state is on the admins' page
                notify(bot.owner_id, f"Your bot @{bot.username} has been paused: {answer_of.message}")
            note, result = answer_of.message, "stopped"
            break
        db.commit()

    bot_store.record_check(db, src.id, now, bots.next_check(now, src.check_minutes, 0), fail_count=0, error=note,
                           title=feed.title or None, etag=answer.etag, last_modified=answer.last_modified,
                           read_once=True, posted=posted)
    db.commit()
    return f"{result}: {posted} posted"


def _failed(db, src, bot, now, message, notify):
    fails = (src.fail_count or 0) + 1
    bot_store.record_check(db, src.id, now, bots.next_check(now, src.check_minutes, fails), fail_count=fails,
                           error=message)
    if fails >= bots.FAILS_BEFORE_PAUSE:
        bot_store.set_source_paused(db, src.id, True, message)
        db.commit()
        if bot.owner_id:
            notify(bot.owner_id, f"Your bot @{bot.username} stopped reading {src.url} after {fails} failed tries: "
                                 f"{message} Start it again from Settings > Bots once it works.")
        return "paused"
    db.commit()
    return "failed"
