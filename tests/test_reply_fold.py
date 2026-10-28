"""Comments under a comment that score under a third of it wait behind "Show N more comments" (helpers/reply_fold.py,
the owner's rule of 2026-10-23), by the score the reader sees, only under a comment scoring at least 3, and never the
reader's own or the one a link points to."""
from types import SimpleNamespace as NS

from ruqqus.helpers.reply_fold import FLOOR, fold


def comments(*scores):
    return [NS(id=i, score=s) for i, s in enumerate(scores)]


def score_of(c):
    return c.score


def ids(items):
    return [c.id for c in items]


def test_a_third_of_the_parent_is_the_line():
    under = comments(30, 21, 22, 8, 2)            # parent 64: a third is 21.33
    shown, folded = fold(64, under, score_of)
    assert ids(shown) == [0, 2] and ids(folded) == [1, 3, 4]
    shown, folded = fold(63, comments(21, 20), score_of)     # exactly a third stays
    assert ids(shown) == [0] and ids(folded) == [1]


def test_the_page_order_is_kept_on_both_sides():
    shown, folded = fold(90, comments(1, 50, 2, 40, 3), score_of)
    assert ids(shown) == [1, 3] and ids(folded) == [0, 2, 4]


def test_a_low_scoring_parent_folds_nothing():
    for parent in (FLOOR - 1, 0, -5, None):
        shown, folded = fold(parent, comments(0, -3, 1), score_of)
        assert folded == [] and ids(shown) == [0, 1, 2], parent


def test_net_scores_below_zero_fold_under_a_positive_parent():
    shown, folded = fold(9, comments(-2, 3, 2), score_of)       # upvotes minus downvotes can be negative
    assert ids(shown) == [1] and ids(folded) == [0, 2]


def test_what_the_reader_keeps_is_never_folded():
    under = comments(1, 1, 50)
    shown, folded = fold(60, under, score_of, keep=lambda c: c.id == 1)
    assert ids(shown) == [1, 2] and ids(folded) == [0]


def test_nothing_and_everything():
    assert fold(10, [], score_of) == ([], [])
    assert fold(10, None, score_of) == ([], [])
    shown, folded = fold(300, comments(1, 2, 3), score_of)
    assert shown == [] and ids(folded) == [0, 1, 2]
