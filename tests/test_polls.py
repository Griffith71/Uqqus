"""Polls (ruqqus/helpers/polls.py): the pure rules, without a database."""
import pytest

from ruqqus.helpers import polls as p


# --- the options -------------------------------------------------------------------------------------

def test_options_are_tidied_blank_ones_dropped_and_each_is_plain_text():
    assert p.clean_options(["  Yes ", "", "No   way", "   "]) == ["Yes", "No way"]
    assert p.clean_options(["Café", "水"]) == ["Café", "水"]          # any language
    assert p.clean_options(None, complete=True) == [] and p.clean_options([]) == []    # no poll at all


def test_a_poll_has_two_to_four_options():
    assert p.MIN_OPTIONS == 2 and p.MAX_OPTIONS == 4
    assert len(p.clean_options(["a", "b", "c", "d"])) == 4
    with pytest.raises(p.PollError) as err:
        p.clean_options(["a", "b", "c", "d", "e"])
    assert err.value.message == "A poll can have 4 options at most."
    with pytest.raises(p.PollError) as err:
        p.clean_options(["only one"])
    assert err.value.message == "A poll needs at least 2 options."


def test_a_draft_may_hold_an_unfinished_poll_but_a_post_may_not():
    assert p.clean_options(["only one"], complete=False) == ["only one"]
    with pytest.raises(p.PollError):
        p.clean_options(["only one"], complete=True)
    # too many is wrong either way
    with pytest.raises(p.PollError):
        p.clean_options(list("abcde"), complete=False)


def test_an_option_has_a_length_limit_no_duplicates_and_no_control_characters():
    assert p.clean_options(["x" * p.OPTION_CHARS, "y"]) == ["x" * p.OPTION_CHARS, "y"]
    with pytest.raises(p.PollError) as err:
        p.clean_options(["x" * (p.OPTION_CHARS + 1), "y"])
    assert "40 characters at most" in err.value.message
    with pytest.raises(p.PollError) as err:
        p.clean_options(["Yes", "yes"])
    assert "is in the poll twice" in err.value.message
    with pytest.raises(p.PollError):
        p.clean_options(["bell\x07", "ok"])
    # the case-fold is the real one, not just lower()
    with pytest.raises(p.PollError):
        p.clean_options(["Straße", "STRASSE"])


def test_the_options_become_plain_text_for_the_word_filter():
    assert p.option_text(["Yes", "No"]) == "Yes\nNo"
    assert p.option_text([]) == "" and p.option_text(None) == ""


# --- the length ----------------------------------------------------------------------------------------

def test_the_length_is_one_of_the_offered_ones_and_blank_means_a_day():
    assert p.HOURS == (1, 6, 24, 72, 168) and p.DEFAULT_HOURS == 24
    assert p.clean_hours(None) == 24 and p.clean_hours("") == 24 and p.clean_hours("  ") == 24
    assert [p.clean_hours(str(h)) for h in p.HOURS] == list(p.HOURS)
    assert p.clean_hours(6) == 6


@pytest.mark.parametrize("raw", ["2", "0", "-1", "abc", "24.0", "1;2", "٢٤", "169", "99999"])
def test_any_other_length_is_refused(raw):
    with pytest.raises(p.PollError) as err:
        p.clean_hours(raw)
    assert err.value.message == "Pick how long the poll runs."


def test_a_poll_closes_after_its_length():
    assert p.closes_at(1000, 1) == 1000 + 3600
    assert p.closes_at(1000.9, 24) == 1000 + 86400
    assert p.is_closed(2000, 2000) and p.is_closed(2000, 2001)
    assert not p.is_closed(2000, 1999)


# --- what is shown ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("closed,voted,author,shown", [
    (False, False, False, False),      # a poll still running, and you haven't voted: no counts to steer you
    (False, True, False, True),
    (False, False, True, True),        # the author always sees them
    (True, False, False, True),        # once it has ended, everyone, a visitor included
    (True, True, True, True),
])
def test_who_is_shown_the_counts(closed, voted, author, shown):
    assert p.results_visible(closed, voted, author) is shown


def test_percentages_add_up_to_a_hundred():
    assert p.percentages([0, 0, 0]) == [0, 0, 0]
    assert p.percentages([1, 1, 1]) == [34, 33, 33]
    assert p.percentages([1, 2]) == [33, 67]
    assert p.percentages([5, 0]) == [100, 0]
    for counts in ([3, 3, 3, 1], [7, 11, 13, 17], [1, 0, 0, 999], [2, 2, 2, 2]):
        assert sum(p.percentages(counts)) == 100, counts


def test_the_time_left_is_in_the_biggest_whole_unit():
    assert p.time_left(1000, 1000) == "Final results" and p.time_left(1000, 5000) == "Final results"
    assert p.time_left(1030, 1000) == "1 minute left"
    assert p.time_left(1000 + 90, 1000) == "2 minutes left"
    assert p.time_left(1000 + 3600, 1000) == "1 hour left"
    assert p.time_left(1000 + 5 * 3600 - 10, 1000) == "5 hours left"
    assert p.time_left(1000 + 47 * 3600, 1000) == "47 hours left"
    assert p.time_left(1000 + 3 * 86400, 1000) == "3 days left"


# --- voting -------------------------------------------------------------------------------------------------

def test_the_reason_a_vote_is_refused_comes_in_a_fixed_order():
    assert p.vote_refusal(gone=False, closed=False, voted=False, option_known=True) is None
    assert p.vote_refusal(True, True, True, False) == "This poll is no longer available."
    assert p.vote_refusal(False, True, True, False) == "This poll has ended."
    assert p.vote_refusal(False, False, True, False) == "You already voted in this poll."
    assert p.vote_refusal(False, False, False, False) == "Pick one of the options."
