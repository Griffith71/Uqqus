"""Quote cards in the pages (partials/quote_card.html, helpers/jinja2.py `quote_of`, both stylesheets): the card sits
under the member's own words at the size of their text, nothing from the quoted site is loaded, and a post on this site
is drawn for each reader by the rules of opening it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"
PARTIAL = (TEMPLATES / "partials" / "quote_card.html").read_text(encoding="utf-8")
LISTING = (TEMPLATES / "submission_listing.html").read_text(encoding="utf-8")
PAGE = (TEMPLATES / "submission.html").read_text(encoding="utf-8")
JINJA = (ROOT / "ruqqus" / "helpers" / "jinja2.py").read_text(encoding="utf-8")
STYLES = {name: (ROOT / "ruqqus" / "assets" / "style" / name).read_text(encoding="utf-8") for name in ("main.scss", "main_dark.scss")}


def _block(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


def test_the_feed_card_and_the_post_page_draw_the_quote_card():
    for page in (LISTING, PAGE):
        assert '{% from "partials/quote_card.html" import quote_card %}' in page
        assert "{% set quote = quote_of(p, v) %}" in page
    assert "{{ quote_card(quote, p, v) }}" in LISTING
    # on the post page: right under the member's own words, before the community note, with the whole text
    body = PAGE.index("{{ p.body_html | safe }}")
    card = PAGE.index("{{ quote_card(quote, p, v, true) }}")
    assert body < card < PAGE.index("{{ community_note(p) }}", body)
    # a quoted post is not drawn twice: no link-preview box, no domain template, no old post embed
    assert "(p.url and not quote and (not p.embed_url or p.embed_kind == 'widget'))" in PAGE
    assert "p.domain_obj.embed_template and not quote" in PAGE
    assert "post_embed" not in PAGE and "post-link-embed" not in PAGE


def test_nothing_from_the_quoted_site_is_loaded():
    for page in (LISTING, PAGE, PARTIAL):
        assert "widget.html" not in page and "widget_script" not in page
        assert "<iframe" not in PARTIAL and "<script" not in PARTIAL
    assert not (TEMPLATES / "site_embeds" / "widget.html").exists()
    assert not (TEMPLATES / "site_embeds" / "ruqqus_post.html").exists()
    assert 'template_global("widget_script")' not in JINJA and 'template_filter("post_embed")' not in JINJA
    # the card's own picture is the post's thumbnail, fetched and served by this site
    assert '<img class="quote-card-media" src="{{ p.preview_image_url }}"' in PARTIAL


def test_the_card_is_escaped_text_and_opens_what_it_quotes():
    social = _block(PARTIAL, "{% if q.kind == 'social' %}", "{% elif q.kind == 'post' and q.notice %}")
    assert '<a class="quote-card-cover" href="{{ q.url }}" target="_blank" rel="nofollow noopener noreferrer"' in social
    assert '{{ q.text | urlize(48, true, target="_blank") }}' in social
    # the only stored HTML drawn is a post's own title, sanitized when it was written
    assert re.findall(r"\|\s*safe", PARTIAL) == ["| safe"] and "{{ quoted.title | safe }}" in PARTIAL
    assert '{{ quoted.body_html | striptags | truncate(2000 if full else 400, False, "…", 0) }}' in PARTIAL


def test_a_post_of_this_site_never_names_an_anonymous_author():
    post = PARTIAL[PARTIAL.index("{% elif q.kind == 'post' %}"):]
    assert "{% set who = author_of(quoted, v) %}" in post
    assert "{{ who.shown_name }}</span>{{ handle_tag(who) }}" in post
    assert ".author" not in post.replace("author_of(", "")
    assert "username" not in post


def test_a_post_of_this_site_is_read_by_the_rules_of_opening_it():
    quote = JINJA[JINJA.index("def _post_quote("):JINJA.index('@app.template_global("may_share")')]
    assert "get_post(b36, v=v, graceful=True)" in quote                 # a Circle post a reader may not see: None
    assert "post.in_private_guild_for(v)" in quote
    assert "filtered=post_hidden(post, v)" in quote and "v.any_block_exists(post.author)" in quote
    assert "muted=is_muted(post, v)" in quote and "anonymous=bool(post.is_anonymous)" in quote
    # asked once per post, reader and level in a request, and a hidden post's object never reaches the template
    assert "key = (b36, v.id if v else None, depth)" in quote
    assert '"post": None if notice else post' in quote
    # what it quotes is drawn inside it by the same rules, one level only (a hidden post shows nothing it quotes)
    assert "inner = None if notice or depth else _quote_for(post, v, 1)" in quote
    quote_for = JINJA[JINJA.index("def _quote_for("):JINJA.index("def _post_quote(")]
    assert "return None if quoted == p.base36id else _post_quote(quoted, v, depth)" in quote_for
    assert "return quote_cards.inside(card) if depth else card" in quote_for


def test_a_card_draws_what_its_post_quotes_inside_it_one_level_only():
    social = _block(PARTIAL, "{% if q.kind == 'social' %}", "{% elif q.kind == 'post' and q.notice %}")
    post = PARTIAL[PARTIAL.index("{% elif q.kind == 'post' %}"):]
    for branch in (social, post):
        assert "{% if q.inner %}{{ quote_card(q.inner, p, v, full) }}" in branch
        assert "{{ q.quoting }}" in branch
    for name, scss in STYLES.items():
        nested = _block(scss, ".quote-card .quote-card {", "}")
        assert "font-size: inherit;" in nested, name                    # the size of the card around it
        assert "-webkit-line-clamp: 4;" in _block(scss, ".quote-card .quote-card .quote-card-text {", "}"), name
        assert ".quote-card-full .quote-card .quote-card-text {" in scss, name


def test_both_stylesheets_draw_the_card_at_the_size_of_the_members_text():
    for name, scss in STYLES.items():
        section = scss[scss.index("// Quote cards (partials/quote_card.html"):scss.index("// Create post card (templates/submit.html")]
        assert "$quote-hover:" in section, name
        card = _block(section, ".quote-card {", "}")
        # the feed card's title is 16px (14px on a phone), the post page's body text 14px / 21px
        assert "font-size: 16px;" in card and "z-index: 3;" in card, name
        assert "font-size: 14px;" in _block(section, ".quote-card-full {", "}") and "line-height: 21px;" in _block(section, ".quote-card-full {", "}"), name
        phone = section[section.index("@media (max-width: 767.98px)"):]
        assert "font-size: 14px;" in _block(phone, ".quote-card {", "}"), name
        for rule in (".quote-card-cover {", ".quote-card-head {", ".quote-card-name {", ".quote-card-text {", ".quote-card-foot {",
                     ".quote-card-notice {", ".quote-card-media {", ".quote-card-text a,"):
            assert rule in section, (name, rule)
        assert "@media (hover: hover)" in section and "@media (prefers-reduced-motion: no-preference)" in section, name
