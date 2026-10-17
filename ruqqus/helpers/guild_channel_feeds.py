"""Bot accounts feeding an announcement channel (rules in helpers/guild_channels.py, SQL in helpers/guild_channel_store.py).

A guildmaster (config) links an approved bot account to one of the guild's announcement channels. From then on the bot's
**new** public posts (never its history) are put into the channel as messages, each its title and link, by the bot's own
Matrix account: the same signed, rated and counted message as a person's, so the word filter, reports and removals apply
and the webhook accepts it. At most FEED_ITEMS_PER_RUN a run and FEED_PER_DAY a bot a channel a day. One step of
scripts/recomputes.py (`run`); the Matrix and Redis calls are passed in, so it is tested with stand-ins.
"""
from ruqqus.helpers import guild_channel_store as store
from ruqqus.helpers import guild_channels as rules

FEED_ITEMS_PER_RUN = 3


def message_for(title, link):
    """The text a post becomes: its title, then its link (the site's own links get a card in the chat)."""
    body = " ".join((title or "").split())
    link = (link or "").strip()
    return f"{body}\n{link}".strip()[:rules.MESSAGE_MAX]


def run(db, r, now, *, provision, put_in_room, set_writers, send, mxid_for, secret, rate, link_of, new_nonce=None, log=None):
    """Sends the new items of every feed. Returns how many messages went. `provision(bot id)` makes sure the bot has a
    chat account (raises if it can't), `put_in_room(room, mxid)`, `set_writers(room, [mxid])`, `send(room, mxid, content)`
    returns the event id, `rate(text)` the word-filter rating, `link_of(post id, url)` the link to put in the message."""
    sent = 0
    make_nonce = new_nonce or (lambda: store.new_nonce(r))
    for cid, room, board_id, bot_id, last in store.all_feeds(db):
        items = store.bot_posts_after(db, bot_id, last, FEED_ITEMS_PER_RUN)
        if not items:
            continue
        if store.posts_since(db, cid, bot_id, now - 86400) >= rules.FEED_PER_DAY:
            continue
        mxid = mxid_for(bot_id)
        try:
            if not store.in_room(db, cid, bot_id):
                provision(bot_id)
                put_in_room(room, mxid)
                store.join(db, cid, bot_id, now)
                db.flush()
            set_writers(room, [mxid_for(uid) for uid in store.writer_ids(db, cid, board_id)])
        except Exception as e:
            if log:
                log(f"guild channel {cid}: feed bot {bot_id} not ready: {e}")
            continue
        for post_id, title, url in items:
            body = message_for(title, link_of(post_id, url))
            if not body:
                store.set_feed_cursor(db, cid, bot_id, post_id)
                continue
            try:
                severity = rate(body)
                nonce = make_nonce()
                content = {"msgtype": "m.text", "body": body, rules.SEVERITY_KEY: severity, rules.NONCE_KEY: nonce,
                           rules.SITE_KEY: rules.site_mark(secret, room, mxid, severity, nonce, body)}
                event_id = send(room, mxid, content)
            except Exception as e:
                if log:
                    log(f"guild channel {cid}: feed item {post_id} not sent: {e}")
                break                                 # the cursor stays: this item is tried again next time
            store.record_post(db, cid, bot_id, event_id, body, severity, now)
            store.set_feed_cursor(db, cid, bot_id, post_id)
            sent += 1
    return sent


def provision_bot(db, bot_id, now, provision_user):
    """Makes sure the bot account has a chat account on the site's own books (it is never offered to a person: nobody logs
    into a bot). An account Synapse already holds with no record here is not adopted (audit D-07)."""
    from sqlalchemy import text
    if db.execute(text("SELECT 1 FROM chat_identities WHERE user_id = :u"), {"u": bot_id}).fetchone():
        return
    mxid = provision_user(bot_id, adopt_existing=False)
    db.execute(text("INSERT INTO chat_identities (user_id, matrix_user_id, provisioned_utc) VALUES (:u, :m, :now)"),
               {"u": bot_id, "m": mxid, "now": now})


def post_link(domain, https, post_id, url):
    """What follows a post's title: its own link when it has one, else the post's page on this site."""
    from ruqqus.helpers.base36 import base36encode
    if url:
        return url
    return f"{'https' if https else 'http'}://{domain}/post/{base36encode(post_id)}"
