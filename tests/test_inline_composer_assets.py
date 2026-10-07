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
    for field in ('name="title"', 'name="body"', 'name="url"', 'name="formkey"', 'name="forward_guilds"',
                  'name="sensitive"', 'name="file"'):
        assert field in PARTIAL, field
    # the shared option macros (comment permission, disclosure, anonymity) provide the rest
    for macro in ("comment_permission_field(", "disclosure_field(", "anonymous_field("):
        assert macro in PARTIAL, macro
    assert "/api/vue/submit" in SCRIPT


def test_a_guild_page_forwards_and_the_feed_does_not():
    # forwarding is a hidden field that only exists when the page is a guild's
    assert re.search(r"\{% if board %\}<input type=\"hidden\" id=\"ic-forward\" name=\"forward_guilds\" value=\"\{\{ board\.name \}\}\">", PARTIAL)
    assert "inline_composer(v)" in HOME
    assert "inline_composer(v, b)" in BOARD
    assert "b.can_forward(v)" in BOARD, "only guilds you may forward to get a composer"
    # the new card on a guild page is the forwarded copy, not the profile post
    assert "?guild=" in SCRIPT


def test_it_is_desktop_only_and_loaded_for_members():
    assert "d-none d-lg-flex" in PARTIAL
    assert "inline_composer.js" in (TEMPLATES / "default.html").read_text(encoding="utf-8")


def test_the_wording_is_the_communitys_on_every_page():
    # the same invitation on the feed and on a guild page; and no "post to a guild"
    assert "Share something with the {{ 'SITE_NAME' | app_config }} community..." in PARTIAL
    assert "Forwarding to +" in PARTIAL
    assert not re.search(r"post(ed)? (to|in) (a |the |this )?guild", PARTIAL, re.I)
