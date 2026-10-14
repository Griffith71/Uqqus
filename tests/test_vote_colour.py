"""The colour of a vote count (ruqqus/helpers/vote_colour.py): blue all up, red all down, eleven steps between."""
import pytest

from ruqqus.helpers.vote_colour import ratio, ratio_class, ratio_label


@pytest.mark.parametrize("ups, downs, cls", [(0, 0, ""), (1, 0, "vote-ratio-10"), (0, 1, "vote-ratio-0"),
                                             (50, 50, "vote-ratio-5"), (96, 4, "vote-ratio-10"), (94, 6, "vote-ratio-9"),
                                             (3, 7, "vote-ratio-3"), (None, None, ""), (-4, 2, "vote-ratio-0")])
def test_ratio_class(ups, downs, cls):
    assert ratio_class(ups, downs) == cls


def test_ratio_and_label():
    assert ratio(0, 0) is None and ratio(3, 1) == 0.75
    assert ratio_label(1234, 56) == "1,234 upvotes · 56 downvotes (96% up)"
    assert ratio_label(1, 1) == "1 upvote · 1 downvote (50% up)"
    assert ratio_label(0, 0) == "0 upvotes · 0 downvotes"
