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


def test_forward_to_guilds_is_in_the_options_like_on_create_a_post():
    submit = (TEMPLATES / "submit.html").read_text(encoding="utf-8")
    options = PARTIAL[PARTIAL.index('<div id="ic-options"'):]
    # the first field of Options, with the full editor's label and tooltip
    assert options.index("Forward to guilds (optional)") < options.index('id="ic-url"')
    assert "Forward to guilds (optional)" in submit
    tooltip = re.search(r'title="(Your post always appears on your profile first[^"]+)"', submit).group(1)
    assert tooltip in PARTIAL, "the info tooltip must say the same thing as on Create a post"
    assert "Type a guild name and press Enter" in PARTIAL
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


def test_the_side_panel_composer_gets_it_through_the_same_macro():
    panel = (TEMPLATES / "composer_panel.html").read_text(encoding="utf-8")
    assert "inline_composer(v, none, true)" in panel


def test_it_is_desktop_only_and_loaded_for_members():
    assert "d-none d-lg-flex" in PARTIAL
    assert "inline_composer.js" in (TEMPLATES / "default.html").read_text(encoding="utf-8")


def test_the_wording_is_the_communitys_on_every_page():
    # the same invitation on the feed and on a guild page; and no "post to a guild"
    assert "Share something with the {{ 'SITE_NAME' | app_config }} community..." in PARTIAL
    assert "Forwarding to" in PARTIAL and "'+' + name" in SCRIPT
    assert not re.search(r"post(ed)? (to|in) (a |the |this )?guild", PARTIAL, re.I)
