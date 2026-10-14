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
                "coauthors_field", "poll_field"]


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


def test_the_audience_pill_wraps_the_real_radios_and_works_without_script():
    pill = FORM.split('<details class="composer-pill" id="composer-audience">')[1].split("</details>")[0]
    assert "post_options.audience_field(" in pill                   # name="audience" radios, as before
    assert "<summary" in pill and 'id="composer-audience-label"' in pill
    # the label is drawn by the server from the saved value, so the pill is right before any script
    assert 'audience_now = ((draft.option("audience", 0) if draft else request.form.get("audience", 0)) | int)' in FORM


def test_the_options_are_folded_in_the_page_not_removed_from_it():
    more = FORM.split('<details class="composer-more"')[1].split("</details>")[0]
    for macro in ("comment_permission_field", "disclosure_field", "anonymous_field", "coauthors_field", "poll_field"):
        assert f"post_options.{macro}(" in more, macro
    assert 'id="sensitiveCheck"' in more and 'name="sensitive"' in more
    assert "{% if draft or request.form %} open{% endif %}" in FORM         # a draft or a re-render shows them
    # a closed <details> still submits its fields: nothing is disabled to fold them
    assert "disabled" not in FORM.split('<details class="composer-more"')[1].split("poll_field")[0]


def test_the_attach_row_calls_the_handlers_that_already_exist():
    tools = FORM.split('<div class="composer-tools"')[1].split("</div>")[0]
    for call in ("attachLink()", "getGif();commentForm('post-title')", "commentForm('post-title');loadEmojis()"):
        assert call in tools, call
    assert 'for="file-upload"' in tools and 'id="composer-poll-btn"' in tools


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
