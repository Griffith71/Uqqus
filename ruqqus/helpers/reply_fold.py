"""Which comments under a comment are shown in the thread and which wait behind "Show N more comments" (the owner's
rule, 2026-10-23). Pure.

A comment that scores less than a third of the comment it answers is folded: it is still on the page, in a hidden
group at the end of that branch, opened in place. The score is the one the reader sees (their score mode: upvotes, or
upvotes minus downvotes), so the rule follows what is on screen. Guards (the owner's choice):
- only comments on comments: top-level comments always show;
- only under a comment scoring at least FLOOR, so a 1- or 2-point comment never folds what answers it (and in the
  upvotes-minus-downvotes mode a parent at 0 or below folds nothing);
- never a comment `keep` says to keep (the reader's own, the one a link points to and the comments above it).
The order of the thread (the page's sort) is kept on both sides.
"""

FLOOR = 3


def fold(parent_score, comments, score_of, keep=lambda c: False):
    """(shown, folded) out of `comments`, in their order."""
    comments = list(comments or ())
    if parent_score is None or parent_score < FLOOR:
        return comments, []
    shown, folded = [], []
    for c in comments:
        if not keep(c) and score_of(c) * 3 < parent_score:
            folded.append(c)
        else:
            shown.append(c)
    return shown, folded
