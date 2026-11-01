"""The post editor toolbar (assets/js/post_editor.js) and the things it has to
agree with: the fixed formatting names, the stylesheets and the two forms."""
import re
from pathlib import Path

import pytest

from ruqqus.helpers import post_formatting as pf
from stylesheets import SHEETS, expanded

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
JS = (ROOT / "assets" / "js" / "post_editor.js").read_text(encoding="utf-8")
ALL_JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
RAIL = (ROOT / "assets" / "style" / "_post_rail.scss").read_text(encoding="utf-8")
LIGHT = (ROOT / "assets" / "style" / "_light.scss").read_text(encoding="utf-8")


def js_list(name):
    m = re.search(rf"var {name} = \[(.*?)\];", JS, re.S)
    assert m, name
    return re.findall(r"'([^']+)'", m.group(1))


def test_the_toolbar_offers_exactly_the_names_the_server_accepts():
    assert tuple(js_list("TEXT_COLORS")) == pf.TEXT_COLORS
    assert tuple(js_list("HIGHLIGHTS")) == pf.HIGHLIGHT_COLORS
    assert tuple(js_list("ALIGNMENTS")) == pf.ALIGNMENTS


@pytest.mark.parametrize("sheet", ["main.scss", "main_dark.scss"])
def test_every_toolbar_class_the_script_uses_is_styled_in_both_stylesheets(sheet):
    css = expanded(sheet)      # the sheet with its partials written in: the rail's rules are in _post_rail.scss
    used = set(re.findall(r"'((?:post-tb-|post-)[a-z-]+)", JS)) | {"post-toolbar", "post-preview"}
    # class names the script writes (post-tb-* and the two panes), not file or url fragments
    used = {c for c in used if c.startswith(("post-tb-", "post-toolbar", "post-preview"))}
    missing = sorted(c for c in used if not re.search(rf"\.{re.escape(c)}\b", css))
    assert not missing, f"{sheet} has no rule for {missing}"


def test_all_three_forms_have_a_toolbar_a_preview_pane_and_the_script():
    submit = (ROOT / "templates" / "submit.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="post-body"' in submit
    assert 'class="post-preview d-none"' in submit and "post_editor.js" in submit

    inline = (ROOT / "templates" / "partials" / "inline_composer.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="ic-body"' in inline and 'class="post-preview d-none"' in inline
    default = (ROOT / "templates" / "default.html").read_text(encoding="utf-8")
    assert "asset_url('js/post_editor.js')" in default       # the feed composer's page loads it
    assert "if (window.PostEditor) return;" in JS             # and it is safe to load twice (the edit form's own tag)

    page = (ROOT / "templates" / "submission.html").read_text(encoding="utf-8")
    assert 'class="post-toolbar" data-target="post-edit-box-{{ p.base36id }}"' in page
    assert 'class="post-preview d-none"' in page and "post_editor.js" in page


def test_the_placeholder_buttons_can_be_switched_on_by_an_uploader():
    for kind in ("image", "audio", "video"):
        assert f"uploadStub('{kind}'" in JS
    assert "register: function (kind, handler)" in JS


def test_the_toolbar_never_builds_html_from_user_text():
    # the only innerHTML is the server-rendered preview; names and labels go through textContent
    assert JS.count("innerHTML") == 1 and "node.innerHTML = html;" in JS
    assert JS.count("setServerHtml(") == 3        # its definition, the in-place Preview's text and the overlay's card


def test_undo_redo_then_what_goes_into_the_text_then_the_folded_formatting():
    # the owner (2026-10-21): Reddit's order, the formatting folded behind "Aa" at the top of the box
    order = [JS.index(x) for x in ("tbButton(tr('Undo')", "tbButton(tr('Redo')", "uploadStub('image'", "uploadStub('video'",
                                   "uploadStub('audio'", "tbButton(tr('GIF')", "tbButton(tr('Emoji')",
                                   "'post-tb-toggle'", "tbButton(tr('Link (Ctrl+K)')", "dropdown(tr('Text style')")]
    assert order == sorted(order)
    # the link in the text is formatting, first behind "Aa", as in a comment (the owner, 2026-10-30), and only once
    assert JS.count("tbButton(tr('Link (Ctrl+K)')") == 1
    assert "extras.push(group(tbButton(tr('Link (Ctrl+K)'), 'fas fa-link', linkDialog)));" in JS
    assert "extras.forEach(function (g) { g.classList.add('post-tb-extra'); });" in JS
    assert "setFolded(!startOpen, false);" in JS and "localStorage" in JS and "try {" in JS
    more = JS.split("dropdown(tr('More')", 1)[1].split("})\n    ));", 1)[0]
    assert "'Emoji'" not in more and "'GIF'" not in more          # moved to the front, not twice
    assert "use: function (kind, textarea)" in JS and "'posteditor:register'" in JS


def test_a_gif_from_the_blue_row_is_the_posts_link():
    js = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
    gif = js.split("function insertGIF(url,form) {", 1)[1].split("\n  }", 1)[0]
    assert "target.hasAttribute('data-attach-link')" in gif and "target.value = url;" in gif


# --- the right-hand rail (the owner, 2026-10-30): the toolbar beside the text box, bigger, following the writer down the page


POSTED = "// Create a post looks like the post it makes"


def rail_rules():
    """The rail's rules: the first 992px media query (the partial opens with the tab icon's default and ends with the posted-width block)."""
    rail = RAIL[:RAIL.index(POSTED)]
    return rail[rail.index("@media (min-width: 992px)"):]


@pytest.mark.parametrize("sheet", SHEETS)
def test_the_rail_is_imported_last_by_both_sheets(sheet):
    imports = re.findall(r'@import "\{ruqquspath\}/assets/style/(_[\w-]+)";', (ROOT / "assets" / "style" / sheet).read_text(encoding="utf-8"))
    assert imports[-1] == "_post_rail" and imports[-2] == "_text_size", (sheet, imports[-3:])


def test_the_rail_is_a_sticky_column_beside_the_text_box_from_992px_only():
    rules = rail_rules()
    assert RAIL[:RAIL.index(POSTED)].count("@media") == 1                          # narrower screens keep the row at the top
    assert RAIL[RAIL.index(POSTED):].count("@media (min-width: 992px)") == 1
    toolbar = rules[rules.index(".post-editor > .post-toolbar {"):]
    for declaration in ("--post-rail: 1;", "grid-column: 2;", "position: sticky;", "top: var(--post-rail-top);",
                        "align-self: start;", "overflow-y: auto;", "flex-flow: column nowrap;"):
        assert declaration in toolbar, declaration
    # the wrapper is a grid with a column of fixed width and does not clip: overflow would make it the sticky column's scroller
    wrapper = rules[rules.index(".composer-card .post-editor,\n    .inline-composer .post-editor {"):].split("}")[0]
    assert "display: grid;" in wrapper and "var(--post-rail-w)" in wrapper and "overflow: visible;" in wrapper
    # the box (border, radius, ring) is the text area and the preview, so the column is outside it
    assert ".post-editor > .input-group" in rules and ".post-editor > .post-preview" in rules
    assert "min-height: calc(var(--post-rail-h) - 2px);" in rules                 # the text is the column's height less the box's borders
    # no scrollbar of its own: a classic one would take a quarter of the 36px buttons' width and clip them
    assert "scrollbar-width: none;" in toolbar and "&::-webkit-scrollbar {\n            display: none;" in toolbar
    # with the formatting open the column takes the window's height (the box stretches to match)
    assert "&:not(.post-tb-collapsed) {\n            max-height: calc(100vh - var(--post-rail-top) - var(--post-rail-bottom));" in rules
    # libsass cannot pass a CSS min() through: the cap is written as a string, as the zoom guard's max() is
    assert 'unquote("min(calc(100vh - var(--post-rail-top) - var(--post-rail-bottom)), var(--post-rail-h))")' in rules


def test_the_rail_uses_tokens_and_bigger_buttons():
    rules = rail_rules()
    assert "width: var(--control-h);" in rules and "height: var(--control-h);" in rules      # 36px, up from about 26
    # 14px symbols and 12px words, as the comment box's tools in the same 36px (the owner, 2026-10-30: 16px read bigger)
    assert "font-size: var(--text-base);" in rules and "font-size: px2rem(16px);" not in rules
    assert ".post-tb-toggle-text {\n            font-size: var(--text-xs);" in rules
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", re.sub(r"//[^\n]*", "", RAIL)), "colours come from the tokens"
    assert not re.search(r"(?<![\w-])\d+(\.\d+)?px[;, )]", re.sub(r"//[^\n]*", "", rules.replace("1px", "").replace("2px", "").replace("47px", "").replace("52px", "").replace("56px", "").replace("62px", "").replace("72px", "").replace("16px", "").replace("15px", ""))), "sizes come from the tokens"


def test_the_feed_composer_clears_the_sticky_header_and_create_a_post_its_bottom_bar():
    assert re.search(r"\.inline-composer \{\s*--post-rail-top: calc\(var\(--navbar-height, 56px\) \+ 62px \+ var\(--space-4\)\);", RAIL)
    assert re.search(r"#submit \.composer-card \{\s*--post-rail-bottom: calc\(var\(--space-4\) \+ 72px\);", RAIL)


def test_the_focus_ring_is_on_the_text_box_and_not_on_the_toolbar_that_sits_outside_it():
    rule = LIGHT[LIGHT.index("// From 992px the toolbar is a column outside the text box"):]
    assert "@media (min-width: 992px)" in rule
    assert ".composer-card .post-editor > .input-group:focus-within" in rule and ".inline-composer .post-editor > .input-group:focus-within" in rule
    assert "box-shadow: 0 0 0 3px var(--focus-ring), var(--bloom-sm);" in rule
    # ...and the wrapper's own ring from the narrow layout is switched off in the rail
    off = rail_rules().split(".inline-composer .post-editor:focus-within {", 1)[1].split("}")[0]
    assert "border-color: transparent;" in off and "box-shadow: none;" in off


def test_menus_dialog_and_status_are_fixed_beside_the_rail_because_the_column_scrolls():
    # the column scrolls by itself, which would clip an absolutely placed menu: the script places them in the window
    assert "function isRail(bar)" in JS and "getPropertyValue('--post-rail')" in JS
    place = JS.split("function placeBeside(node, bar, anchor) {", 1)[1].split("\n  }", 1)[0]
    assert "node.style.position = 'fixed';" in place and "document.documentElement.dir === 'rtl'" in place
    assert "getElementById('navbar')" in place and "querySelector('.composer-bar')" in place   # never under either bar
    assert "node.style.right = Math.max(margin, document.documentElement.clientWidth - rect.left + margin) + 'px';" in place   # grows away from the rail
    assert "if (isRail(bar)) placeBeside(menu, bar, button); else clearPlacement(menu);" in JS
    assert "if (isRail(bar)) placeBeside(statusNode, bar); else clearPlacement(statusNode);" in JS
    assert "placeBeside(form, bar);" in JS and "window.removeEventListener('scroll', dialogMover);" in JS
    assert "window.addEventListener('scroll', closeFloatingMenus, { passive: true });" in JS      # a menu would drift from the sticky column
    for selector in (".post-tb-menu {", ".post-tb-status,\n        .post-tb-dialog {"):
        assert "position: fixed;" in rail_rules().split(selector, 1)[1].split("}")[0], selector


def test_write_and_preview_are_icons_in_the_rail_and_words_elsewhere():
    assert "'fas fa-pen post-tb-tab-icon'" in JS and "'fas fa-eye post-tb-tab-icon'" in JS
    assert "make('span', 'post-tb-tab-text', tr('Write'))" in JS and "make('span', 'post-tb-tab-text', tr('Preview'))" in JS
    # a top toolbar shows the words; the icon needs two classes, or Font Awesome's `.fas { display: inline-block }` (loaded later) wins
    assert re.search(r"^\.post-toolbar \.post-tb-tab-icon \{\s*display: none;", RAIL, re.M)
    assert ".post-tb-tab-icon {\n            display: inline-block;" in rail_rules()


def test_the_body_grows_with_the_text_so_the_page_scrolls_and_the_rail_follows():
    assert "function growEditorBody(field, follow)" in ALL_JS
    grow = ALL_JS.split("function growEditorBody(field, follow) {", 1)[1].split("\n}\n", 1)[0]
    assert "field.style.minHeight = Math.ceil(" in grow and "style.height" not in grow       # by min-height, so the toolbar can stretch the box
    assert "if (event.target.matches(EDITOR_BODIES)) { growEditorBody(event.target); return; }" in ALL_JS
    assert "event.target.id === 'post-title' || event.target.id === 'ic-title'" in ALL_JS      # the title still does not grow
    assert "'post-body'" not in ALL_JS.split("document.addEventListener('input', function (event) {", 1)[1].split("}, false);", 1)[0]
    # a body filled by script (a draft, the title's overflow, another author's text) is sized too, since no key was pressed
    assert "growEditorBodies();" in ALL_JS and "if (bodyChanged) growEditorBody(body, false);" in ALL_JS
    collab = (ROOT / "assets" / "js" / "collab_editor.js").read_text(encoding="utf-8")
    assert collab.count("growEditorBody(") == 2


# --- Create a post looks like the post it makes (the owner, 2026-10-30): the box's text is as wide as the posted text ----------

SUBMIT = (ROOT / "templates" / "submit.html").read_text(encoding="utf-8")
COLLAB_EDITOR = (ROOT / "templates" / "collab" / "editor.html").read_text(encoding="utf-8")


def test_the_two_columns_that_mirror_the_posted_text_carry_posted_width_and_nothing_else_does():
    assert 'class="col col-md-6 p-3 py-md-0 submit-main-col posted-width"' in SUBMIT
    assert 'class="col col-md-6 p-3 py-md-0 submit-main-col posted-width"' in COLLAB_EDITOR
    for other in (("templates", "make_board.html"), ("templates", "collab", "list.html")):
        assert "posted-width" not in (ROOT.joinpath(*other)).read_text(encoding="utf-8"), other      # Create a guild, the drafts list
    assert "posted-width" not in (ROOT / "templates" / "partials" / "inline_composer.html").read_text(encoding="utf-8")


def test_the_member_s_folded_sidebar_reaches_the_page_as_a_body_class():
    # the post page they will see has the 65px rail; html.sidebar-folded would also move the navbar's alignment rules
    assert """<body id="submit"{% if request.cookies.get('sidebar_pref') == 'collapsed' %} class="posted-left-rail"{% endif %}>""" in SUBMIT


def test_the_page_tells_the_stylesheet_its_scrollbar_and_the_posted_text_s_exact_width():
    script = SUBMIT.split('<div id="posted-width-probe" aria-hidden="true"></div>', 1)[1].split("</script>", 1)[0]
    assert "root.style.setProperty('--sbw', (window.innerWidth - root.clientWidth) + 'px');" in script
    # the post page's flex row (card, minus the two 15px gutters) shrinks the text and its 28px vote column (margins -4, 8) together
    assert "probe.getBoundingClientRect().width - 30" in script
    assert "Math.round((28 - 896 / (card + 28)) * 64) / 64" in script          # the 1/64 px the engines lay out in
    assert "root.style.setProperty('--posted-text-js', (card - 4 - vote) + 'px');" in script
    assert "window.addEventListener('resize', measure);" in script and "window.addEventListener('load', measure);" in script
    assert SUBMIT.index("posted-width-probe") < SUBMIT.index('{% include "header.html" %}')       # before anything is painted


def test_the_stylesheet_mirrors_the_posted_column():
    rules = RAIL[RAIL.index("body#submit {"):]
    # the column the post page has: the feed's 782px or what the window leaves beside the sidebar (272px open, 65px rail) and the 316px one
    assert "--posted-col: min(#{$layout-feed}, calc(100vw - var(--sbw, 0px) - var(--posted-left) - #{$layout-right}));" in rules
    assert "--posted-left: 65px;" in rules and "--posted-left: #{$layout-left};" in rules
    assert "body#submit:not(.posted-left-rail)" in rules and "@media (min-width: 1199.98px)" in rules
    # the script's exact figure first, then the middle of the range it covers (60.5 to 60.8px less than the column)
    assert "--posted-text-w: var(--posted-text-js, calc(var(--posted-col) - 60.6px));" in rules
    # the page's column is the text, the box's padding and borders, the gap, the toolbar column, the card's padding and border, its own padding
    assert re.search(r"--create-col-w: calc\(var\(--posted-text-w\) \+ 2 \* var\(--box-inset\) \+ 2px \+ var\(--space-3\) \+ var\(--post-rail-w\) \+ "
                     r"2 \* var\(--create-card-pad\) \+ 2px \+ 2 \* var\(--create-col-pad\)\);", rules)
    assert "flex: 0 1 var(--create-col-w);" in rules and "max-width: var(--create-col-w);" in rules
    assert "#submit:has(.submit-main-col.posted-width) .container {" in rules       # the bar along the bottom keeps under the card's edges
    # the post page is not edited: the box takes the posted width, explicit padding, and the posted wrapping
    for declaration in ("padding-left: var(--box-inset);", "padding-right: var(--box-inset);", "word-break: break-word;"):
        assert declaration in rules, declaration


def test_a_narrow_window_gives_padding_back_in_pixels_so_a_larger_text_size_does_not_take_it_again():
    rules = RAIL[RAIL.index("body#submit {"):]
    squeeze = rules.split("@media (max-width: 1259.98px) {", 1)[1].split("}", 1)[0]
    assert "--create-col-pad: 0.25rem;" in squeeze and "--create-card-pad: var(--space-4);" in squeeze
    assert "padding-left: var(--create-col-pad) !important;" in rules            # Bootstrap's p-3 is !important


def test_the_posted_text_is_the_post_pages_own_numbers():
    # the arithmetic above rests on these: the card is the column less two 15px gutters, the vote column's margins are -4px and 8px
    # and its content (the arrows) is 28px, and the post page's text and vote column are one shrinking flex row
    css = (ROOT / "assets" / "style" / "_feed.scss").read_text(encoding="utf-8")
    voting = css.split(".card .voting {", 1)[1].split("}", 1)[0]
    assert "margin: 0 var(--space-3) 0 calc(-1 * var(--space-2)) !important;" in voting       # 8px and -4px
    page = (ROOT / "templates" / "submission.html").read_text(encoding="utf-8")
    assert 'd-flex flex-row-reverse flex-nowrap justify-content-end' in page and 'class="card-block w-100 my-md-auto"' in page


# --- Preview shows the post as the post page does, and in the feed composer at the posted width (the owner, 2026-10-30) ------------

def test_preview_shows_the_title_then_the_text_as_the_post_page_does():
    fn = JS.split("function setPreview(on) {", 1)[1].split("\n    // --- build the buttons", 1)[0]
    assert "make('div', 'post-preview-title')" in fn and "heading.textContent = title;" in fn      # the member's own words, as text
    assert "make('div', 'post-preview-text')" in fn
    assert fn.count("setAttribute('dir', 'auto')") == 2
    assert "if (!title && !text.textContent.trim() && !text.querySelector('img'))" in fn            # a title alone still previews
    # the title is the composer's own field; the edit box, the wiki and the event editors have none and preview the text alone
    field = JS.split("function titleField(bar) {", 1)[1].split("\n  }", 1)[0]
    assert "bar.closest('.composer-card, .inline-composer')" in field and "'.composer-title-input, .ic-title'" in field
    # typed like the post page's title (_feed.scss .post-title), at every width
    title = RAIL.split(".post-preview-title {", 1)[1].split("}", 1)[0]
    for declaration in ("font-size: var(--text-title);", "font-weight: var(--weight-semibold);", "letter-spacing: var(--tracking-title);",
                        "line-height: var(--leading-tight);", "word-break: break-word;"):
        assert declaration in title, declaration
    feed = (ROOT / "assets" / "style" / "_feed.scss").read_text(encoding="utf-8")
    post_title = feed.split(".post-title, #frontpage .post-title {", 1)[1].split("}", 1)[0]
    for token in ("--text-title", "--weight-semibold", "--tracking-title", "--leading-tight"):
        assert f"var({token})" in post_title, token


# --- the Preview overlay (the owner, 2026-10-30): from 992px both composers preview in a pop-up that looks like the post --------------


def fn(name, source=None):
    """The body of one function declaration in the toolbar script, up to the next function at the same indentation."""
    source = JS if source is None else source
    return source.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def test_the_editor_script_no_longer_works_out_the_posted_width_or_sizes_an_in_place_feed_pane():
    for gone in ("postedTextWidth", "sizePreview", "--preview-w", "marginInlineStart"):
        assert gone not in JS, gone
    # the box's own width on Create a post is still the page's script (templates/submit.html), held to the post page by the spec
    assert "Math.round((28 - 896 / (card + 28)) * 64) / 64" in SUBMIT


def test_the_overlay_is_one_bootstrap_modal_built_once_on_the_body_from_existing_strings():
    build = fn("previewOverlay")
    assert "if (overlay) return overlay;" in build and "document.body.appendChild(modal);" in build
    assert "modal.id = 'postPreviewModal';" in build and "'modal-dialog modal-dialog-centered post-preview-dialog'" in build
    assert "tr('Preview')" in build and "tr('Close')" in build and "'data-dismiss', 'modal'" in build
    assert "hidden.bs.modal" in build and "overlay.token++;" in build and "overlay.field.focus();" in build      # writing carries on
    # no string the catalogues do not have (a new one would lower every language's share until it is translated)
    used = set(re.findall(r"tr\('([^']+)'", fn("previewOverlay") + fn("openPreviewOverlay")))
    assert used == {"Preview", "Close", "Loading preview...", "Could not load the preview."}, used
    for msgid in used:
        assert f'msgid "{msgid}"' in (ROOT / "translations" / "fr" / "LC_MESSAGES" / "js.po").read_text(encoding="utf-8"), msgid


def test_the_overlay_asks_the_server_for_the_card_with_what_the_composer_holds():
    fields = fn("previewFields")
    assert "bar.closest('.composer-card, .inline-composer')" in fields
    for name in ("formkey", "body", "title", "url"):
        assert f"form.append('{name}'" in fields, name
    assert "['anonymous', 'paid_partnership', 'made_with_ai', 'sensitive']" in fields and "input[type=\"checkbox\"][name=\"' + name + '\"]" in fields
    opening = fn("openPreviewOverlay")
    assert "fetch('/api/post_preview'" in opening and "credentials: 'same-origin'" in opening
    assert "--preview-card-w" in opening and "postedCardWidth(bar)" in opening
    assert "if (ticket !== o.token) return;" in opening                     # an answer that comes after it closed is let go
    assert "setServerHtml(o.sheet, res.data.html || '');" in opening
    # the card's width: the feed composer's own card is the post page's; Create a post's is its probe less the card's gutters
    width = fn("postedCardWidth")
    assert "bar.closest('.inline-composer-card')" in width and "getElementById('posted-width-probe')" in width and "- 30" in width


def test_from_992px_the_composers_preview_opens_the_overlay_and_elsewhere_it_is_the_in_place_toggle():
    assert "return isRail(bar) && !!bar.closest('.composer-card, .inline-composer');" in fn("overlayWanted")
    click = JS.split("tabs.preview = tbButton(tr('Preview'), 'fas fa-eye post-tb-tab-icon', function () {", 1)[1].split("}, 'post-tb-tab');", 1)[0]
    assert "if (overlayWanted(bar)) {" in click and "openPreviewOverlay(bar, ta());" in click
    assert "if (previewing) setPreview(false);" in click and "setPreview(true);" in click           # a window widened while previewing in place
    assert "'post-tb-tab post-tb-tab-write active'" in JS
    rail = rail_rules()
    assert ".post-tb-tab-write {\n            display: none;" in rail                               # Write is not there to go back to
    assert "9 * var(--control-h)" in rail                                                            # one folded button fewer


def test_the_overlay_is_the_post_pages_card_at_the_post_pages_width_and_inert():
    css = RAIL.split(".post-preview-text {", 1)[1]
    assert ".post-preview-dialog {\n    max-width: calc(var(--preview-card-w, 40rem) + 2 * var(--space-6));" in css
    assert ".post-preview-sheet {\n    width: var(--preview-card-w);" in css
    assert ".post-preview-card .voting {\n    min-width: 15px;\n    overflow-wrap: normal;" in css          # the post page's #thread rules
    assert "pointer-events: none;" in css.split(".post-preview-card .voting,", 1)[1].split("}", 1)[0]
    # the in-place feed Preview and Create a post's in-place padding are gone from the partial
    for gone in ("is-previewing", "> .post-preview {\n        grid-column: 1 / -1;", "padding-top: 7px;"):
        assert gone not in RAIL, gone
