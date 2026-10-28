"""The colour of a vote count (no I/O): how much of an item's or an account's votes are upvotes, from blue (all up,
`$upvote`) to red (all down, `$downvote`), in eleven steps drawn by `.vote-ratio-0` ... `.vote-ratio-10` in both
stylesheets. Used for an account's Rep (helpers/rep_store.py) and, for viewers who read scores as upvotes, for the
score of a post or comment (partials/score.html)."""

from ruqqus.helpers.i18n_runtime import number_text, tr, trn

STEPS = 10


def ratio(ups, downs):
    """The share of upvotes, 0.0 to 1.0, or None with no votes at all."""
    ups, downs = max(int(ups or 0), 0), max(int(downs or 0), 0)
    total = ups + downs
    return None if total == 0 else ups / total


def ratio_class(ups, downs):
    """`vote-ratio-N` (0 all down ... 10 all up), or "" with no votes (the number stays the page's own colour)."""
    share = ratio(ups, downs)
    if share is None:
        return ""
    return f"vote-ratio-{int(round(share * STEPS))}"


def ratio_label(ups, downs):
    """What hovering a count says: '1,234 upvotes · 56 downvotes (96% up)'."""
    ups, downs = max(int(ups or 0), 0), max(int(downs or 0), 0)
    upvotes = trn("%(count)s upvote", "%(count)s upvotes", ups, count=number_text(ups))
    downvotes = trn("%(count)s downvote", "%(count)s downvotes", downs, count=number_text(downs))
    # Translators: an account's or an item's votes, such as "1,234 upvotes · 56 downvotes"
    text = tr("%(upvotes)s · %(downvotes)s", upvotes=upvotes, downvotes=downvotes)
    share = ratio(ups, downs)
    # Translators: the votes, then the share of upvotes, such as "1,234 upvotes · 56 downvotes (96% up)"
    return text if share is None else tr("%(votes)s (%(share)s%% up)", votes=text, share=round(share * 100))
