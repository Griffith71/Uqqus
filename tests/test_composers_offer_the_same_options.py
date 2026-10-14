"""The full editor (templates/submit.html) and the feed composer / Create post panel
(templates/partials/inline_composer.html) are two forms for the same route. They drifted once (the panel had no
toolbar, GIF, emoji, drafts or picture-in-text); this keeps the field names in step, with the differences written
down."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus" / "templates"
PAGE = (ROOT / "submit.html").read_text(encoding="utf-8")
SUBMIT = PAGE.split('<form id="submitform"')[1].split("</form>")[0]      # the form, not the page's <meta> tags
SCRIPTS = ROOT.parent / "assets" / "js"
INLINE = (ROOT / "partials" / "inline_composer.html").read_text(encoding="utf-8")
OPTIONS = (ROOT / "partials" / "post_options.html").read_text(encoding="utf-8")

# the macros of partials/post_options.html both pages call, and the field names each draws
MACRO_FIELDS = {
    "audience_field": {"audience"},
    "comment_permission_field": {"comment_permission"},
    "disclosure_field": {"paid_partnership", "made_with_ai"},
    "anonymous_field": {"anonymous"},
    "coauthors_field": {"coauthors"},
    "poll_field": {"poll_option", "poll_hours"},
}


def names(page):
    found = set(re.findall(r'\bname="(\w+)"', page))
    for macro, fields in MACRO_FIELDS.items():
        if f"{macro}(" in page:
            found |= fields
    return found


# what only one of them has, on purpose (none today: write the reason here when there is one)
ONLY_FULL = set()
ONLY_INLINE = set()


def test_every_option_of_the_full_editor_is_in_the_feed_composer():
    full, inline = names(SUBMIT), names(INLINE)
    # the full editor submits the guilds from script (forward_guilds) and the inline composer does the same
    # both write one hidden `forward_guilds` field per guild from script
    assert "forward_guilds" in PAGE and "forward_guilds" in (SCRIPTS / "inline_composer.js").read_text(encoding="utf-8")
    missing = sorted(full - inline - ONLY_FULL)
    assert not missing, f"the feed composer has no {missing}: add them to partials/inline_composer.html (and to post_drafts.js collect() if they belong in a draft)"


def test_the_feed_composer_has_nothing_the_full_editor_lacks():
    assert not (names(INLINE) - names(SUBMIT) - ONLY_INLINE - {"forward_guilds"}), "a field on one composer only"


def test_the_saved_fields_are_the_same_for_both():
    """Whatever a draft keeps is read by name from either form (assets/js/post_drafts.js `collect()`)."""
    js = (SCRIPTS / "post_drafts.js").read_text(encoding="utf-8")
    kept = set(re.findall(r"data\.append\('(\w+)'", js)) | {"paid_partnership", "made_with_ai", "anonymous", "poll_option", "poll_hours"}
    for name in kept - {"formkey", "publish_utc"}:
        assert name in names(INLINE) | {"forward_guilds"}, f"a draft keeps {name}, the feed composer has no such field"
        assert name in names(SUBMIT) | {"forward_guilds"}, name
