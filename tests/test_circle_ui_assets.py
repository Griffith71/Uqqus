"""The circle controls in the browser: one audience field (partials/post_options.html `audience_field`) drawn by the
composers, the "Who can see this" sheet and the story sheet, kept in step by assets/js/composer_card.js; the "Add to
circles" sheet on a profile; the styles in both stylesheets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


CARD = read("ruqqus", "assets", "js", "composer_card.js")
SHEET_JS = read("ruqqus", "assets", "js", "circle_audience.js")
MEMBERS_JS = read("ruqqus", "assets", "js", "circle_members.js")
STORIES_JS = read("ruqqus", "assets", "js", "stories.js")
INLINE_JS = read("ruqqus", "assets", "js", "inline_composer.js")
STYLES = {name: read("ruqqus", "assets", "style", f"{name}.scss") for name in ("main", "main_dark")}


def test_one_field_keeps_the_radio_and_the_ticks_in_step_before_anyone_reads_it():
    assert "window.AudienceField = { read: read, set: set, label: label };" in CARD
    # the capture phase: the pill (listening on its way up) must see the field already in step
    sync = CARD.split("document.addEventListener('change', function (event) {")[1].split("var pill")[0]
    assert sync.rstrip().endswith("}, true);     // the capture phase: the field is in step before anything that listens on its way up reads it (the pill)")
    assert "input.name === 'circles' && input.checked" in sync and "!read(field).circles.length" in sync
    assert "if (audience === '4')" in CARD.split("function read(field)")[1].split("function label")[0]   # Public sends no circles


def test_the_pill_names_what_is_chosen_and_stays_open_while_ticking():
    assert "return name + ' +1';" in CARD and "return chosen.circles.length + ' circles';" in CARD
    assert "if (input.name === 'audience' && input.value === '0') window.setTimeout(function () { pill.open = false; }, 120);" in CARD
    assert "audience.dispatchEvent(new Event('change', { bubbles: true }));" in INLINE_JS          # after a post, the pill is redrawn


def test_every_place_that_sends_an_audience_sends_the_circles_with_it():
    assert "var tokens = (item.getAttribute('data-circles') || '')" in SHEET_JS
    assert "if (Array.isArray(value)) value.forEach(function (one) { body.append(k, one); });" in SHEET_JS
    assert "Choose at least one circle, or make it public." in SHEET_JS
    assert "var fields = { kind: kind, audience: chosen.audience, circles: chosen.circles };" in STORIES_JS
    for name in ("submission.html", "submission_listing.html"):
        html = read("ruqqus", "templates", name)
        assert html.count('data-circles="{{ p.circle_tokens() }}"') == html.count('data-audience="{{ p.audience or 0 }}"') >= 2, name
    # the story sheet's video is public only: the circles are switched off for it
    assert "input[name=\"circles\"], input[name=\"audience\"][value=\"4\"]" in STORIES_JS


def test_the_sheets_draw_the_same_field_with_the_members_own_circles():
    modal = read("ruqqus", "templates", "partials", "audience_modal.html")
    assert '{{ audience_field(0, "audience-option", (), circle_choices(v)) }}' in modal
    stories = read("ruqqus", "templates", "partials", "story_sheets.html")
    assert '{{ audience_field(0, "story-audience", (), circle_choices(v)) }}' in stories
    options = read("ruqqus", "templates", "partials", "post_options.html")
    field = options.split("{% macro audience_field(")[1].split("{% endmacro %}")[0]
    assert 'data-circle-name="{{ c.name }}"' in field and "| safe" not in field and 'href="/circles"' in field


def test_the_add_to_circles_sheet_sits_above_its_backdrop_and_writes_no_html():
    assert "if (modal.parentNode !== document.body) document.body.appendChild(modal);" in MEMBERS_JS
    assert "innerHTML" not in MEMBERS_JS and "label.textContent = circle.name;" in MEMBERS_JS
    assert "box.checked = !adding;" in MEMBERS_JS                                    # a refused tick is put back
    sheet = read("ruqqus", "templates", "partials", "circle_member_modal.html")
    assert "asset_url('js/circle_members.js')" in sheet and 'role="status"' in sheet


def test_the_styles_are_in_both_sheets_before_the_composer_and_need_none_of_its_variables():
    for name, css in STYLES.items():
        start = css.index('// "Who can see this?" (partials/post_options.html audience_field)')
        composer = css.index("// Create post card (templates/submit.html")
        block = css[start:composer]
        assert start < composer, name
        assert "$cc-" not in block, name                         # those are defined inside the composer's section
        assert "$audience-border:" in block and "$audience-hover:" in block, name
        for rule in (".audience-circles {", '.audience[data-mode="0"] .audience-circles {', ".circle-mark {", "a.circle-row:hover"):
            assert rule in block, (name, rule)
        assert "transition: opacity 0.15s ease-out;" in block.split("@media (prefers-reduced-motion: no-preference)")[1], name
        assert "#composer-audience-label {" in css[composer:] and "text-overflow: ellipsis;" in css[composer:], name
