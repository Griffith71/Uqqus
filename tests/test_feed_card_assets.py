"""The feed card after the owner's review (2026-10-23): posts sit on the page itself, divided by hairlines (X,
Reddit), the vote column is centred beside the post, and the card shows the body's first lines (escaped plain text)
with "Show more" only when there is more; how many lines and how tall the media is follow the post and the density."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
CARD = (ROOT / "templates" / "submission_listing.html").read_text(encoding="utf-8")
FEED = (ROOT / "assets" / "style" / "_feed.scss").read_text(encoding="utf-8")
TOKENS = (ROOT / "assets" / "style" / "_tokens.scss").read_text(encoding="utf-8")
JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")


def test_the_snippet_is_escaped_plain_text_with_its_own_id():
    line = next(l for l in CARD.splitlines() if 'class="post-snippet' in l)
    assert 'id="post-snippet-{{ p.base36id }}"' in line and "{{ snippet }}" in line
    assert "| safe" not in line
    assert "{% set snippet, more = excerpt_of(p, has_media) %}" in CARD


def test_show_more_is_drawn_only_when_the_body_holds_more():
    assert "{% if p.body_html and more %}" in CARD
    handler = JS.split("function onTextExpandClick(event){")[1].split("\n}")[0]
    assert "$('#post-snippet-'+id).toggleClass('d-none');" in handler
    assert "$('#post-text-'+id).toggleClass('d-none');" in handler


def test_posts_sit_on_the_page_divided_by_hairlines():
    card = FEED.split("// --- the post")[1].split("#{feed-select(\".card::after\")}")[0]
    assert "background-color: transparent;" in card and "border: 0;" in card
    assert re.search(r'#\{feed-select\("\.card::after"\)\} \{[^}]*height: 1px;[^}]*background-color: var\(--border\);', FEED)
    assert '#{feed-select(".card:has(+ .card:hover)::after")}' in FEED     # the hairline above a pointed-at post fades
    assert "--bg: #ffffff;" in TOKENS and "--bg: #000000;" in TOKENS


def test_only_post_cards_are_styled_as_posts():
    # the full body behind "Show more" is a .card inside the post: never a post itself
    assert 'unquote("#{$feed} > #{$suffix}")' in FEED


def test_the_vote_column_is_centred_beside_the_post():
    voting = FEED.split(".card .voting {")[1].split("}")[0]
    assert "align-self: center;" in voting


def test_how_much_shows_follows_the_post_and_the_density():
    for token in ("--snippet-lines: 4;", "--snippet-lines-media: 2;", "--media-max-h: 512px;",
                  "--media-max-h-with-text: 400px;"):
        assert token in TOKENS, token
    compact = TOKENS.split('html[data-density="compact"] {')[1].split("}")[0]
    for token in ("--snippet-lines: 2;", "--snippet-lines-media: 0;", "--media-max-h: 320px;",
                  "--media-max-h-with-text: 240px;"):
        assert token in compact, token
    assert "-webkit-line-clamp: var(--snippet-lines);" in FEED
    assert "max-height: var(--media-max-h-with-text);" in FEED


def test_the_blurred_backdrop_is_a_decorative_copy_not_a_css_address():
    backdrop = next(l for l in CARD.splitlines() if "post-img-backdrop" in l)
    assert 'alt=""' in backdrop and 'aria-hidden="true"' in backdrop and 'src="{{ p.url }}"' in backdrop
    assert "url(" not in FEED      # no address from a post is ever written into CSS


def test_the_vote_column_votes_from_the_cards_whole_height_with_a_mouse():
    # the owner (2026-10-23): the column's top half upvotes, its bottom half downvotes, card edge to card edge, each
    # half tinted on hover; a mouse only (a tap or a scroll on a touch screen never votes)
    zones = FEED.split("// The vote column votes from the card's whole height")[1]
    assert "@media (hover: hover) and (pointer: fine) {\n    .card .voting {" in zones
    assert "align-self: stretch;" in zones and "justify-content: center;" in zones
    up = zones.split("\n    .card .voting .arrow-up::after {")[1].split("}")[0]
    down = zones.split("\n    .card .voting .arrow-down::after {")[1].split("}")[0]
    assert "top: calc(-1 * var(--vote-reach-top));" in up and "bottom: 50%;" in up
    assert "top: 50%;" in down and "bottom: calc(-1 * var(--vote-reach-bottom));" in down
    # the zone is the arrow's own ::after: a click there is a click on the arrow, which carries the vote's data
    assert "data-id-up=" in CARD and "data-id-down=" in CARD
    # the score keeps its clicks above the zones, also once a vote swapped .score for .score-up / .score-down
    assert ".card .voting .score, .card .voting .score-up, .card .voting .score-down {" in zones
    assert "color-mix(in srgb, var(--upvote) 10%, transparent)" in zones
    assert "color-mix(in srgb, var(--downvote) 10%, transparent)" in zones
