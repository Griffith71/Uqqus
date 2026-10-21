"""The Create post card (templates/submit.html): the redesign is an arrangement, so every field name and id the
scripts use must still be on the page, the audience pill must keep the real radios, and the new look must be
scoped to the card (Create a guild extends the same page shell)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text()


SUBMIT = read("templates", "submit.html")
SCRIPT = read("assets", "js", "composer_card.js")
STYLES = {name: read("assets", "style", name) for name in ("main.scss", "main_dark.scss")}
FORM = SUBMIT.split('<form id="submitform"')[1].split("</form>")[0]

IDS = [
    "post-title", "post-body", "post-URL", "character-count-title", "character-count-body", "body-write",
    "add-body-link", "title-link-btn", "title-link-chip", "title-link-chip-text", "image-upload-block",
    "file-upload", "filename-show", "image-preview", "gif-title-btn", "emoji-title-btn", "create_button",
    "save-draft", "schedule-open", "drafts-open", "draft-status", "draft-id", "forward_guild_input",
    "forward-guild-chips", "forward_guild_inputs",
]

NAMES = ["formkey", "draft_id", "title", "body", "url", "forward_guilds", "sensitive"]
MACRO_FIELDS = ["audience_field", "comment_permission_field", "disclosure_field", "anonymous_field",
                "poll_field"]


def test_every_id_the_scripts_use_is_still_on_the_page():
    for name in IDS:
        assert re.search(r'id="%s"' % re.escape(name), FORM + SUBMIT), name


def test_every_field_the_form_posts_is_still_there():
    for name in ("formkey", "draft_id", "title", "body", "url", "sensitive"):
        assert re.search(r'name="%s"' % name, FORM), name
    for macro in MACRO_FIELDS:
        assert f"post_options.{macro}(" in FORM, macro
    assert "name = 'forward_guilds'" in SUBMIT or "hidden.name = 'forward_guilds'" in SUBMIT


def test_the_draft_scripts_still_find_the_one_body_block_and_the_form():
    assert FORM.count('class="body composer-card"') == 1           # post_drafts.js puts its banner in form .body
    assert '<form id="submitform"' in SUBMIT


def test_the_head_holds_where_it_goes_and_who_sees_it_as_on_the_feed_composer():
    # the owner (2026-10-21): the audience is the pill at the top right, as on the feed composer, with Post anonymously in
    # its menu; where it goes is a dropdown at the top left with the guild search (the "cards" layout stays for Spaces)
    head = FORM.split('<div class="composer-head">')[1].split('<label for="post-title"')[0]
    assert '<details class="composer-pill composer-audience-pill" id="composer-audience">' in head
    assert 'post_options.audience_field(audience_now, "post-audience", circles_now, circle_choices(v))' in head
    assert '"post-anon", compact=true)' in head and "post_options.anonymous_field(" in head
    assert '<details class="composer-pill composer-where-pill" id="composer-where">' in head
    assert 'post_options.where_profile("post-where", "forward_guild_input", "forward_guild_add", to_profile)' in head
    assert 'id="forward_guild_input"' in head and 'id="forward-guild-chips"' in head and 'id="forward_guild_inputs"' in head
    options = read("templates", "partials", "post_options.html")
    cards = options.split('{% if layout == "cards" %}')[1].split("{% else %}")[0]
    assert 'name="audience" value="0"' in cards and 'name="audience" value="4"' in cards and 'name="circles"' in cards
    assert 'data-circle-name="{{ c.name }}"' in cards and 'href="/circles"' in cards and "| safe" not in cards
    assert 'audience_now = ((draft.option("audience", 0) if draft else request.form.get("audience", 0)) | int)' in FORM


def test_the_options_are_folded_in_the_page_not_removed_from_it():
    more = FORM.split('<details class="composer-more"')[1].split("</details>")[0]
    for macro in ("comment_permission_field", "disclosure_field"):
        assert f"post_options.{macro}(" in more, macro
    # the poll is under the blue row and anonymity in the audience pill, never twice (the owner, 2026-10-21)
    for macro in ("poll_field", "anonymous_field"):
        assert f"post_options.{macro}(" not in more, macro
        assert FORM.count(f"post_options.{macro}(") == 1, macro
    assert 'id="sensitiveCheck"' in more and 'name="sensitive"' in more
    assert "{% if draft or request.form %} open{% endif %}" in FORM         # a draft or a re-render shows them
    # a closed <details> still submits its fields: nothing is disabled to fold them
    assert "disabled" not in more.split('id="sensitiveCheck"')[0]


def test_the_attach_row_calls_the_handlers_that_already_exist():
    tools = FORM.split('<div class="composer-tools"')[1].split("</div>")[0]
    # a GIF is the post's own media (its link, all_js.js insertGIF); the emoji goes into the title
    for call in ("attachLink()", "getGif();commentForm('post-URL')", "commentForm('post-title');loadEmojis()"):
        assert call in tools, call
    assert 'for="file-upload"' in tools and 'id="composer-poll-btn"' in tools
    assert 'data-post-media="video"' in tools and 'data-post-media="audio"' in tools
    assert 'id="post-URL" name="url" class="d-none" value="{{ prefill_url }}" data-attach-link' in FORM


def test_the_blue_row_is_right_under_the_title_and_the_body_shows_at_once():
    title = FORM.index('id="post-title"')
    tools = FORM.index('<div class="composer-tools"')
    body = FORM.index('id="body-write"')
    assert title < tools < FORM.index('class="composer-poll"') < body
    assert '<div id="body-write" class="comment-write post-editor mt-3">' in FORM      # no d-none: Reddit's body box


def test_the_script_only_drives_the_pill_and_the_poll_button():
    assert "document.getElementById('composer-audience')" in SCRIPT
    assert "'input[name=\"audience\"]:checked'" in SCRIPT
    assert ".poll-composer .poll-open" in SCRIPT
    assert "innerHTML" not in SCRIPT
    assert "composer_card.js" in SUBMIT


def test_the_look_is_scoped_to_the_card_and_follows_the_motion_rules():
    for name, css in STYLES.items():
        block = css[css.index("// Create post card (templates/submit.html"):]
        assert ".composer-card {" in block and ".composer-tool {" in block
        # motion only inside no-preference, only transform / opacity / colour, under 300ms, hover where it hovers
        assert "@media (prefers-reduced-motion: no-preference)" in block and "@media (hover: hover)" in block
        assert "transition: all" not in block
        for duration in re.findall(r"(\d*\.?\d+)s\b", block):
            assert float(duration) <= 0.3, (name, duration)
        # no selector styles the page shell itself (Create guild extends it)
        for selector in re.findall(r"^([#.][^{\n]+)\{", block, re.M):
            assert selector.strip().startswith((".composer", ".inline-composer", "#submit .composer", "@")) or "composer" in selector, selector
        assert "@media (pointer: coarse)" in block


def test_the_create_guild_page_does_not_get_the_card():
    make = read("templates", "make_board.html")
    assert "composer-card" not in make and "composer-bar" not in make
