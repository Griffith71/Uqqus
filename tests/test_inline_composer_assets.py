"""The composer at the top of a feed (partials/inline_composer.html + assets/js/inline_composer.js):
the script, the markup, the two pages that show it and the wording rules must agree."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATES = ROOT / "templates"
PARTIAL = (TEMPLATES / "partials" / "inline_composer.html").read_text(encoding="utf-8")
SCRIPT = (ROOT / "assets" / "js" / "inline_composer.js").read_text(encoding="utf-8")
HOME = (TEMPLATES / "home.html").read_text(encoding="utf-8")
BOARD = (TEMPLATES / "board.html").read_text(encoding="utf-8")


def test_every_element_the_script_uses_exists_in_the_composer():
    used = set(re.findall(r"el\('([\w-]+)'\)", SCRIPT)) | set(re.findall(r"getElementById\('([\w-]+)'\)", SCRIPT))
    used.discard("posts")   # the feed's own list, not part of the composer
    used.discard("draft-banner")   # made by the script when a draft is saved (post_drafts.js has the same one)
    assert used, "the script looks up no elements?"
    for element_id in used:
        assert f'id="{element_id}"' in PARTIAL, f"inline_composer.html has no #{element_id}"


def test_the_form_sends_what_the_submit_route_reads():
    for field in ('name="title"', 'name="body"', 'name="url"', 'name="formkey"',
                  'name="sensitive"', 'name="file"'):
        assert field in PARTIAL, field
    # the shared option macros (comment permission, disclosure, anonymity) provide the rest
    for macro in ("comment_permission_field(", "disclosure_field(", "anonymous_field("):
        assert macro in PARTIAL, macro
    assert "/api/vue/submit" in SCRIPT
    # the guilds are written by the script, one hidden field each
    assert "forward_guilds" in SCRIPT


def test_a_guild_page_starts_with_its_guild_and_the_feed_with_none():
    # the page's own guild is a preset (a removable chip), never a hidden field that cannot be taken off
    assert re.search(r'\{% if board %\} data-guild="\{\{ board\.name \}\}"\{% endif %\}>', PARTIAL)
    assert 'id="ic-forward"' not in PARTIAL
    assert "inline_composer(v)" in HOME
    assert "inline_composer(v, b)" in BOARD
    assert "b.can_forward(v)" in BOARD, "only guilds you may forward to get a composer"
    # the new card on a guild page is the forwarded copy, not the profile post
    assert "?guild=" in SCRIPT and "hasGuild(presetGuild)" in SCRIPT


def test_where_to_post_is_the_dropdown_in_the_head_like_on_create_a_post():
    submit = (TEMPLATES / "submit.html").read_text(encoding="utf-8")
    head = PARTIAL[PARTIAL.index('<div id="ic-head"'):PARTIAL.index('<textarea id="ic-title"')]
    # the owner (2026-10-21): a dropdown at the top left with the guild search, usable before anything is typed
    assert '<details class="composer-pill composer-where-pill" id="composer-where">' in head
    assert "Where to post" in head and "Where to post" in submit
    assert '<div id="ic-head" class="ic-head d-flex align-items-center justify-content-between">' in PARTIAL   # no hidden
    assert '<div id="ic-bar" class="ic-bar d-flex align-items-center mt-2">' in PARTIAL
    tooltip = re.search(r'title="(Your post is listed on your profile either way[^"]+)"', submit).group(1)
    assert tooltip in PARTIAL, "the info tooltip must say the same thing as on Create a post"
    assert "Search guilds to post to" in PARTIAL and "Search guilds to post to" in submit
    # My profile is one place beside the guilds; on a guild's page the guild is the place and the profile is off
    assert 'where_profile("ic-where", "ic-forward-input", "ic-forward-add", not board)' in PARTIAL
    assert "<div data-where-comment>" in PARTIAL and "<div data-where-comment>" in submit        # Who can comment follows My profile
    for element in ('id="ic-forward-input"', 'id="ic-forward-add"', 'id="ic-forward-chips"', 'id="ic-forward-chip-list"', 'id="ic-forward-inputs"'):
        assert element in PARTIAL, element


def test_the_script_sends_one_forward_guilds_field_per_guild_with_the_editors_limits():
    assert "field.name = 'forward_guilds'" in SCRIPT
    drafts = (ROOT / "helpers" / "post_drafts.py").read_text(encoding="utf-8")
    max_guilds = int(re.search(r"FORWARD_GUILDS_MAX = (\d+)", drafts).group(1))
    max_name = int(re.search(r"GUILD_NAME_MAX = (\d+)", drafts).group(1))
    assert f"FORWARD_MAX = {max_guilds}" in SCRIPT
    assert f'maxlength="{max_name}"' in PARTIAL
    # a leading + is dropped, a repeat (any case) is ignored, and Enter adds instead of posting
    assert r"replace(/^\+/, '')" in SCRIPT and "toLowerCase() === name.toLowerCase()" in SCRIPT
    assert re.search(r"event\.key !== 'Enter'\) return;\s*event\.preventDefault\(\);", SCRIPT)


def test_the_navbar_create_post_button_opens_this_composer_and_there_is_no_panel_for_it():
    default = (TEMPLATES / "default.html").read_text(encoding="utf-8")
    assert 'href="/submit" data-compose' in default and 'data-side-panel="post"' not in default
    assert "[data-compose]" in SCRIPT and "title.focus({ preventScroll: true });" in SCRIPT
    assert "(min-width: 992px)" in SCRIPT                       # narrow screens and modifier clicks follow the link
    assert not (TEMPLATES / "composer_panel.html").exists()
    assert "panel" not in PARTIAL.split("{% macro inline_composer(")[1].split("%}")[0]


def test_it_is_desktop_only_and_loaded_for_members():
    assert "d-none d-lg-flex" in PARTIAL
    assert "inline_composer.js" in (TEMPLATES / "default.html").read_text(encoding="utf-8")


def test_the_wording_is_the_communitys_on_every_page():
    # the same invitation on the feed and on a guild page; and no "post to a guild"
    assert "Share something with the {{ 'SITE_NAME' | app_config }} community..." in PARTIAL
    assert "Forwarding to" in PARTIAL and "'+' + name" in SCRIPT
    assert not re.search(r"post(ed)? (to|in) (a |the |this )?guild", PARTIAL, re.I)


def test_the_composer_has_what_the_full_editor_has():
    # toolbar and preview (post_editor.js builds the toolbar for this textarea)
    assert 'class="post-toolbar" data-target="ic-body"' in PARTIAL
    assert 'class="post-preview d-none"' in PARTIAL
    # from the blue row a GIF is the post's link and the emoji goes into the title; the body toolbar has its own
    assert "commentForm('ic-url');getGif()" in PARTIAL and "commentForm('ic-title');loadEmojis()" in PARTIAL
    assert 'id="ic-url" name="url" class="form-control" placeholder="Link" aria-label="Link" data-attach-link' in PARTIAL
    # the poll is under the blue row, and anonymity in the audience pill: never under More options
    options = PARTIAL[PARTIAL.index('<div id="ic-options"'):]
    assert "poll_field(" not in options and "anonymous_field(" not in options
    assert '{{ anonymous_field(false, "ic-anonymous", compact=true) }}' in PARTIAL
    # the audience pill and the poll button are composer_card.js's
    assert 'id="composer-audience"' in PARTIAL and 'id="composer-poll-btn"' in PARTIAL
    # a picture for the text is the field the post route reads, offered only to who may upload one
    assert 'name="body_image"' in PARTIAL and "v.can_upload_comment_image" in PARTIAL
    assert "Formatting help" in PARTIAL
    # its upload goes to the site's bucket: never offered where the post is meant to stay in a Circle guild
    assert "{% if v.can_upload_comment_image and not circle_guild %}" in PARTIAL


def test_drafts_controls_are_in_the_composer_but_never_in_a_circle_guild_composer():
    # a draft keeps no circle_guild, so a scheduled copy would be published as a public profile post
    assert re.search(r'\{% if not circle_guild %\}\s*<div class="ic-later', PARTIAL)
    block = PARTIAL.split('<div class="ic-later')[1].split("{% endif %}")[0]
    for control in ('id="save-draft"', 'id="schedule-open"', 'id="drafts-open"'):
        assert control in block, control
    for control in ('id="save-draft"', 'id="schedule-open"', 'id="drafts-open"'):
        assert PARTIAL.count(control) == 1, control        # nowhere else
    assert '{% if not circle_guild %}<input type="hidden" name="draft_id" id="draft-id" value="">{% endif %}' in PARTIAL


def test_the_page_flag_brings_the_drafts_panel_and_the_scripts_to_every_composer_page():
    default = (TEMPLATES / "default.html").read_text(encoding="utf-8")
    assert '{% if v and composer_drafts %}{% include "partials/drafts_ui.html" %}{% endif %}' in default
    for script in ("post_editor.js", "post_drafts.js", "composer_card.js"):
        assert f"asset_url('js/{script}')" in default, script
    assert "{% set composer_drafts = true %}" in (TEMPLATES / "home.html").read_text(encoding="utf-8")
    assert BOARD.startswith('{% extends "home.html" %}')     # a guild page gets the flag through home.html


def test_more_options_and_the_pills_work_before_anything_is_typed():
    # More options used to show nothing until the title had been focused (#ic-options sits inside #ic-more, which only
    # open() reveals): every control opens the composer first now
    show = SCRIPT.split("function showOptions(show) {", 1)[1].split("\n  }", 1)[0]
    assert "if (show) open();" in show
    assert "wherePill.addEventListener('toggle'" in SCRIPT and "audiencePill.addEventListener('toggle'" in SCRIPT
    assert "head.hidden" not in SCRIPT and "bar.hidden" not in SCRIPT
