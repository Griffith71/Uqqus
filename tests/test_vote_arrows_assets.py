"""Vote arrows are drawn the same on a profile as in a feed: a signed-in member used to get a score and no arrows on any
page under /@ (so no way to vote on someone else's posts and comments from their profile)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


CARD = read("ruqqus", "templates", "submission_listing.html")
COMMENTS = read("ruqqus", "templates", "comments.html")


def test_no_template_hides_the_vote_arrows_by_page_path():
    for name, source in (("submission_listing.html", CARD), ("comments.html", COMMENTS)):
        assert "startswith(('/@'" not in source and "startswith('/@'" not in source, name


def test_a_post_card_draws_arrows_for_every_signed_in_viewer():
    voting = CARD.split('<div class="voting my-2 pr-2">')[1].split("</div>\n    {% endif %}")[0]
    signed_in = voting.split("{% if v %}")[1].split("location.href='/login'")[0]      # up to the visitor's arrows
    assert "upvote-button" in signed_in and "downvote-button" in signed_in
    assert "data-id-up" in signed_in and "data-id-down" in signed_in
    assert "p.voted_elsewhere" in signed_in                          # the locked arrows still apply
    assert "location.href='/login'" in voting                        # visitors still go to the login page


def test_a_comment_draws_arrows_for_every_signed_in_viewer_on_desktop_and_phone():
    ups = re.findall(r'\{% if v %\}\s*<li id="comment-\{\{ c\.base36id \}\}-up"[^>]*upvote-button', COMMENTS)
    downs = re.findall(r'\{% if v %\}\s*<li id="comment-\{\{ c\.base36id \}\}-down"[^>]*downvote-button', COMMENTS)
    assert len(ups) == 2 and len(downs) == 2                         # d-md-inline-block and d-md-none rows
