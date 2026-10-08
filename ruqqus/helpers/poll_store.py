"""Polls: storage and what a page is shown (the rules are helpers/polls.py).

Everything resolves through the PRIMARY post (`primary_post_id`: a forwarded copy points at it with
`repost_id`, a repost is the same post), so a poll is created once, voted once and counted once, however
many places show it. A page of posts gets its polls in a handful of queries (`attach`), not one per card."""
import time

from flask import g
from sqlalchemy import func

from ruqqus.classes.poll import Poll, PollOption, PollVote
from ruqqus.helpers import polls as rules
from ruqqus.helpers.coauthors import primary_post_id


def create(db, post, options, hours, now=None):
    """Add the poll to `post` (a primary post, just made). `options` is already clean."""
    now = int(now or time.time())
    poll = Poll(post_id=post.id, closes_utc=rules.closes_at(now, hours), created_utc=now)
    db.add(poll)
    db.flush()
    for ordinal, label in enumerate(options):
        db.add(PollOption(poll_id=poll.id, ordinal=ordinal, label=label))
    db.flush()
    return poll


def option_text(db, primary_id):
    """The options of the primary post's poll as plain text for the word filter, "" with no poll."""
    return option_texts(db, [primary_id]).get(primary_id, "")


def option_texts(db, primary_ids):
    ids = list({int(i) for i in primary_ids if i})
    if not ids:
        return {}
    rows = (db.query(Poll.post_id, PollOption.label)
            .join(PollOption, PollOption.poll_id == Poll.id)
            .filter(Poll.post_id.in_(ids)).order_by(Poll.post_id, PollOption.ordinal).all())
    labels = {}
    for post_id, label in rows:
        labels.setdefault(post_id, []).append(label)
    return {post_id: rules.option_text(items) for post_id, items in labels.items()}


class _Found:
    """What the database holds for a set of primary posts, for one viewer."""

    def __init__(self, db, primary_ids, viewer_id):
        self.polls = {}
        self.options = {}
        self.counts = {}
        self.mine = {}
        ids = list({int(i) for i in primary_ids if i})
        if not ids:
            return
        self.polls = {poll.post_id: poll for poll in db.query(Poll).filter(Poll.post_id.in_(ids)).all()}
        if not self.polls:
            return
        poll_ids = [poll.id for poll in self.polls.values()]
        for option in (db.query(PollOption).filter(PollOption.poll_id.in_(poll_ids))
                       .order_by(PollOption.poll_id, PollOption.ordinal).all()):
            self.options.setdefault(option.poll_id, []).append(option)
        self.counts = dict(db.query(PollVote.option_id, func.count(PollVote.id))
                           .filter(PollVote.poll_id.in_(poll_ids)).group_by(PollVote.option_id).all())
        if viewer_id:
            self.mine = dict(db.query(PollVote.poll_id, PollVote.option_id)
                             .filter(PollVote.poll_id.in_(poll_ids), PollVote.user_id == viewer_id).all())

    def view(self, post, viewer, now):
        """What the templates draw for `post` (a primary post or any copy of it), or None without a poll."""
        poll = self.polls.get(primary_post_id(post))
        if poll is None:
            return None

        options = self.options.get(poll.id, [])
        my_option = self.mine.get(poll.id)
        closed = rules.is_closed(poll.closes_utc, now)
        voted = my_option is not None
        is_author = viewer is not None and viewer.id == post.author_id
        results = rules.results_visible(closed, voted, is_author)
        totals = [self.counts.get(option.id, 0) for option in options]
        percents = rules.percentages(totals)

        return {
            "poll_id": poll.id,
            "closed": closed,
            "voted": voted,
            "results": results,
            "can_vote": viewer is not None and not closed and not voted,
            "total": sum(totals) if results else None,
            "time_left": rules.time_left(poll.closes_utc, now),
            "closes_utc": poll.closes_utc,
            "options": [{
                "id": option.id,
                "label": option.label,
                "votes": totals[i] if results else None,
                "percent": percents[i] if results else None,
                "mine": option.id == my_option,
            } for i, option in enumerate(options)],
        }


def attach(db, posts, viewer):
    """Give every post of a page its `poll_data` (None when it has no poll) in a few queries."""
    posts = list(posts)
    if not posts:
        return
    found = _Found(db, [primary_post_id(post) for post in posts], viewer.id if viewer else None)
    now = int(time.time())
    for post in posts:
        post.poll_data = found.view(post, viewer, now)


def load_one(db, post, viewer):
    found = _Found(db, [primary_post_id(post)], viewer.id if viewer else None)
    return found.view(post, viewer, int(time.time()))


def json_of(post):
    """The `poll` of a post's JSON: the same visibility as the page (counts are null while hidden)."""
    from flask import has_app_context

    from ruqqus.helpers import anonymity

    data = post.__dict__.get("poll_data", False)
    if data is False and not has_app_context():
        return None                      # a script reading .json outside a request
    if data is False:
        data = load_one(g.db, post, anonymity.current_viewer())
    if data is None:
        return None
    return {
        "closes_utc": data["closes_utc"],
        "closed": data["closed"],
        "voted": data["voted"],
        "total": data["total"],
        "options": [{"id": o["id"], "label": o["label"], "votes": o["votes"], "mine": o["mine"]} for o in data["options"]],
    }
