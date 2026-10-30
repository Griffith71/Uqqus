"""Glass (the owner, 2026-10-29: "Instagram uses glass-like overlays ... apply that same principle where possible"; CLAUDE.md "Glass").

What floats above the content (bars, menus, dialogs and sheets, toasts, chips over media) is frosted; the content never is. These tests keep
the rules of `_glass.scss` in step: additive (opaque first, translucent inside @supports), AA by construction (the tint, not the blur,
carries the contrast), switched off only by POSITIVE media queries, never inside a card, nothing mangled by the right-to-left mirror, the
sensitive-content cover a button, and the owner's example (Block) asking in the frosted sheet."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
STYLE = APP / "assets" / "style"
GLASS = (STYLE / "_glass.scss").read_text(encoding="utf-8")
SUPPORTS = "@supports (backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))"
SOLID = "@media (prefers-reduced-transparency: reduce), (prefers-contrast: more), (forced-colors: active)"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def compile_sheet(name, colour="23a9f2"):
    sass = pytest.importorskip("sass")
    scss = (STYLE / name).read_text(encoding="utf-8")
    scss = scss.replace("{boardcolor}", colour).replace("{maincolor}", "23a9f2").replace("{ruquqspath}", "").replace("{ruqquspath}", str(APP))
    return sass.compile(string=scss)


def compile_tokens(theme, colour="23a9f2"):
    sass = pytest.importorskip("sass")
    src = (f'$theme: "{theme}"; $primary: #{colour}; $purple: #23a9f2; $upvote: #23a9f2; $downvote: #e8452c;\n'
           f'@import "{APP}/assets/style/_tokens";')
    css = sass.compile(string=src)
    start = css.index(":root {\n  --font-sans")
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", css[start:css.index("}", start)]))


def block_at(text, start):
    depth = 0
    for i in range(text.index("{", start), len(text)):
        depth += (text[i] == "{") - (text[i] == "}")
        if depth == 0:
            return text[start:i + 1]


def at_rule_blocks(css, prelude):
    """The bodies of every `prelude { ... }` in the compiled sheet."""
    out, pos = [], 0
    while True:
        i = css.find(prelude, pos)
        if i < 0:
            return out
        b = block_at(css, i)
        out.append(b[b.index("{") + 1:-1])
        pos = i + len(b)


def rules(body):
    """(selectors, declarations) of the plain rules in an at-rule body (no nesting)."""
    return [([s.strip() for s in m.group(1).split(",")], m.group(2)) for m in re.finditer(r"([^{};]+)\{([^{}]*)\}", body)]


# ---- contrast ----------------------------------------------------------------------------------------------------

def luminance(rgb):
    lin = [c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def rgb_of(value):
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    nums = [float(x) for x in re.findall(r"[\d.]+", value)]
    return tuple(int(n) for n in nums[:3]), (nums[3] if len(nums) > 3 else 1.0)


def over(tint, backdrop):
    (rgb, alpha) = tint
    return tuple(alpha * c + (1 - alpha) * b for c, b in zip(rgb, backdrop))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_on_every_glass_surface_is_aa_over_the_worst_backdrop_without_the_blur(theme):
    """The blur can silently not be drawn (an ancestor with opacity, filter or mask; an old GPU), so the TINT alone must carry AA: over pure
    white and pure black behind (every other backdrop is between them, since compositing is monotone), for the primary text, the muted
    grey, links and danger red."""
    t = compile_tokens(theme)
    text = {name: rgb_of(t[name])[0] for name in ("--text", "--text-muted", "--link", "--danger")}
    for surface in ("--glass-bg", "--glass-bar"):
        tint = rgb_of(t[surface])
        for backdrop in ((255, 255, 255), (0, 0, 0)):
            composed = over(tint, backdrop)
            for name, colour in text.items():
                assert contrast(colour, composed) >= 4.5, (theme, surface, name, backdrop, contrast(colour, composed))
    # a toast is the inverse of the page: its own text colour over its own tint
    toast = rgb_of(t["--glass-toast"])
    fg = rgb_of({"light": "#ffffff", "dark": "#121212"}[theme])[0]
    for backdrop in ((255, 255, 255), (0, 0, 0)):
        assert contrast(fg, over(toast, backdrop)) >= 4.5, (theme, backdrop)
    # a chip over a picture: white text on black .6 over a white picture
    chip = rgb_of(t["--glass-chip"])
    assert contrast((255, 255, 255), over(chip, (255, 255, 255))) >= 4.5
    # nothing is thinner than the research allows for text (Instagram's own 50% snackbar gives 2.9:1)
    for surface in ("--glass-bg", "--glass-bar", "--glass-toast"):
        assert rgb_of(t[surface])[1] >= 0.8, surface


def test_the_blur_values_stay_inside_the_budget():
    t = compile_tokens("light")
    radii = [float(x) for token in ("--glass-fx", "--glass-fx-bar", "--glass-fx-chip", "--glass-fx-scrim") for x in re.findall(r"blur\(([\d.]+)px\)", t[token])]
    assert max(radii) <= 24, radii
    assert float(re.search(r"blur\(([\d.]+)px", t["--glass-fx-bar"]).group(1)) <= 14      # the bars repaint on every scroll frame
    for theme in ("light", "dark"):
        assert "contrast(" not in "".join(compile_tokens(theme).values())


# ---- structure ---------------------------------------------------------------------------------------------------

def test_the_partial_is_imported_after_light_and_before_text_size_by_both_sheets():
    for sheet in ("main.scss", "main_dark.scss"):
        imports = re.findall(r'@import "\{ruqquspath\}/assets/style/(_\w+)";', read("ruqqus", "assets", "style", sheet))
        assert imports.index("_glass") == imports.index("_light") + 1 and imports.index("_text_size") == imports.index("_glass") + 1, (sheet, imports[-4:])


def test_the_old_bar_blurs_live_only_in_the_glass_partial():
    for old in ("_nav.scss", "_mobile.scss", "_components.scss", "_light.scss"):
        assert "backdrop-filter" not in (STYLE / old).read_text(encoding="utf-8"), old
    # (one source of truth; the legacy sheets and templates have none either)
    for sheet in ("main.scss", "main_dark.scss"):
        assert "backdrop-filter" not in read("ruqqus", "assets", "style", sheet).replace("@import", "")


def test_every_translucent_surface_is_additive_and_every_one_has_its_solid_fallback():
    css = compile_sheet("main.scss")
    glass, solid = set(), set()
    for body in at_rule_blocks(css, SUPPORTS):
        for selectors, decls in rules(body):
            if re.search(r"backdrop-filter:\s*var\(--glass", decls):
                glass.update(selectors)
                assert "-webkit-backdrop-filter" in decls, selectors            # both spellings, always (iOS before 18)
    # (libsass merges a nested media query into its parent's: `@media (max-width: 767.98px) and (prefers-reduced-transparency: reduce), ...`)
    for m in re.finditer(r"@media [^{]*prefers-reduced-transparency: reduce[^{]*\{", css):
        body = block_at(css, m.start())
        body = body[body.index("{") + 1:-1]
        for selectors, decls in rules(body):
            if "backdrop-filter: none" in decls or "display: none" in decls:
                solid.update(selectors)
    assert glass, "no glass found"
    exempt = {".modal-content .dropdown-menu", "#navbar-icon-row #accountDropdownMenu"}      # solid on purpose: glass on glass / a top sheet on a page blurred behind it
    assert glass - solid - exempt == set(), glass - solid - exempt
    # the base rule of the surfaces exists OUTSIDE @supports (an opaque surface first), for the ones that set a background
    for needle in ("#navbar.navbar-light, #navbar {\n  background-color: var(--surface) !important; }", "#mobile-bottom-navigation-bar {\n  background-color: var(--surface) !important; }"):
        assert needle in css, needle


def test_glass_is_switched_off_by_positive_queries_only():
    assert "no-preference" not in re.sub(r"//[^\n]*", "", GLASS).split("@mixin")[-1].replace("prefers-reduced-motion: no-preference", "")
    for use in re.findall(r"prefers-reduced-transparency:\s*([\w-]+)", GLASS):
        assert use == "reduce", use
    assert "not all and" not in GLASS and "@media not" not in GLASS
    assert "@media (prefers-reduced-transparency: reduce), (prefers-contrast: more), (forced-colors: active) {" in GLASS


def test_nothing_is_glass_inside_a_card_a_feed_or_another_glass_surface():
    css = compile_sheet("main.scss")
    for body in at_rule_blocks(css, SUPPORTS):
        for selectors, decls in rules(body):
            if "backdrop-filter" not in decls:
                continue
            for selector in selectors:
                assert not re.search(r"\.card\b|\.posts\b|\.comment\b|\.sidebar|\.form-control|tooltip", selector), selector
    # the two glass bars: the blur sits on a layer BEHIND the top bar, so the bar is neither a backdrop root nor a containing block
    assert not any(re.fullmatch(r"#navbar(\.navbar-light)?", s) for body in at_rule_blocks(css, SUPPORTS) for sel, d in rules(body) if "backdrop-filter" in d for s in sel)
    assert "#navbar::before {" in GLASS and "isolation: isolate;" in GLASS


def test_a_dialogs_glass_card_is_never_inside_an_ancestor_that_fades():
    """Bootstrap fades .modal (opacity) and the legacy sheet fades .modal-dialog: the page would show through sharp until the end of the fade and
    the blur would snap in. The scrim fades; the card fades by its OWN opacity; a phone sheet only slides."""
    assert ".modal.fade, .modal.fade.show, .modal.fade:not(.show) {\n    opacity: 1;" in GLASS
    assert ".modal.fade .modal-dialog {\n    opacity: 1;" in GLASS
    own = GLASS[GLASS.index("@media (prefers-reduced-motion: no-preference) {\n    .modal.fade .modal-content {"):]
    assert "transition: opacity 150ms" in own[:own.index("}")]
    sheet = GLASS[GLASS.index("@media (max-width: 767.98px) {\n    .modal.fade.modal-sm-bottom .modal-content {"):]
    assert "opacity: 1;" in sheet[:sheet.index("}")] and "transition: none;" in sheet[:sheet.index("}")]
    # the blur is never animated
    assert not re.search(r"transition:[^;]*(backdrop|blur)", GLASS)


@pytest.mark.parametrize("name", ["main.scss", "main_dark.scss"])
@pytest.mark.parametrize("colour", ("ffd60a", "fcd5ce", "1a1a2e", "e85d04", "2c7be5", "805ad5", "07c20c", "ffffff", "000000"))
def test_the_nine_awkward_guild_colours_compile_with_glass(name, colour):
    css = compile_sheet(name, colour)
    assert SUPPORTS in css and ".modal-backdrop.show" in css and "#navbar::before" in css
    # the brand colour never tints glass: the tokens are neutral
    t = compile_tokens("dark" if "dark" in name else "light", colour)
    assert t["--glass-bg"] == compile_tokens("dark" if "dark" in name else "light")["--glass-bg"]


def test_the_right_to_left_mirror_leaves_glass_alone():
    from ruqqus.helpers import css_flip
    css = compile_sheet("main.scss")
    flipped = css_flip.flip(css)
    pick = lambda text: [l.strip() for l in text.splitlines() if "backdrop-filter" in l or "--glass" in l]
    assert pick(css) == pick(flipped) and len(pick(css)) > 20


# ---- the covers and chips ------------------------------------------------------------------------------------------

def test_the_sensitive_cover_blurs_pixels_not_the_backdrop_and_is_a_button():
    code = re.sub(r"//[^\n]*", "", GLASS)
    cover = code[code.index(".sensitive-overlay {"):]
    assert "rgba(0, 0, 0, 0.6)" in cover[:cover.index("}")]                      # white text over a white picture: 5.7:1 (it was .45: 3.4:1)
    assert "backdrop-filter" not in cover                                          # never: it would vanish under reduced transparency and show the image sharp
    legacy = read("ruqqus", "assets", "style", "main.scss")
    assert "filter: blur(24px);" in legacy[legacy.index(".sensitive-content-wrap .sensitive-blur {"):][:120]
    covers = 0
    for name in ("submission_listing.html", "submission.html", "comments.html"):
        page = read("ruqqus", "templates", name)
        for m in re.finditer(r'<div class="sensitive-overlay"[^>]*>', page):
            tag = m.group(0)
            assert 'role="button"' in tag and 'tabindex="0"' in tag and "event.key === 'Enter'" in tag and "event.key === ' '" in tag, tag
            covers += 1
        for m in re.finditer(r'<div class="(?:\{% if p\.is_sensitive %\})?sensitive-blur[^>]*>', page):
            assert "inert" in page[m.start():m.start() + 200], page[m.start():m.start() + 120]
    assert covers == 6
    js = read("ruqqus", "assets", "js", "all_js.js")
    reveal = js[js.index("function revealSensitive(wrapId) {"):][:700]
    assert 'removeAttribute("inert")' in reveal and "wrap.focus(" in reveal


def test_chips_over_media_carry_the_stronger_tint_and_the_one_off_ones_the_blur():
    code = re.sub(r"//[^\n]*", "", GLASS)
    assert ".post-img-overlay {\n    background-color: var(--glass-chip);" in code      # repeated per card: tint only, no blur
    assert "backdrop-filter" not in code[code.index(".post-img-overlay {"):code.index(".post-img-overlay {") + 120]
    assert ".story-caption, .story-note {\n        -webkit-backdrop-filter: var(--glass-fx-chip);" in code


# ---- the owner's example: Block ---------------------------------------------------------------------------------------

def test_every_block_button_asks_in_the_sheet_first():
    for name in ("submission_listing.html", "submission.html", "comments.html", "partials/profile_header.html"):
        page = read("ruqqus", "templates", name)
        assert "post_toast('/settings/block" not in page, name
    for name, count in (("submission_listing.html", 2), ("submission.html", 2), ("comments.html", 2)):
        assert read("ruqqus", "templates", name).count("blockUser('{{ author_of(") == count, name
    # a comment's menu used to pass no callback and throw after the toast: it swaps the comment for the placeholder now, and a callback is optional
    assert read("ruqqus", "templates", "comments.html").count("commentBlocked('{{ c.base36id }}', '{{ author_of(c, v).username }}')") == 2
    assert read("ruqqus", "assets", "js", "all_js.js").count("if (callback) callback(xhr)") == 2
    profile = read("ruqqus", "assets", "js", "profile_actions.js")
    assert "window.confirm" not in profile and "blockUser(name," in profile


def test_the_confirm_sheet_is_in_every_members_page_and_writes_only_text():
    page = read("ruqqus", "templates", "default.html")
    assert '{% if v %}{% include "partials/confirm_sheet.html" %}{% endif %}' in page
    assert page.index("asset_url('js/all_js.js')") < page.index("asset_url('js/confirm_sheet.js')")
    sheet = read("ruqqus", "templates", "partials", "confirm_sheet.html")
    assert 'class="modal fade modal-sm-bottom" id="confirmSheet"' in sheet and 'role="alertdialog"' in sheet and 'id="toast-undo"' in sheet
    js = read("ruqqus", "assets", "js", "confirm_sheet.js")
    assert "innerHTML" not in js and js.count("textContent") >= 6
    assert "window.confirm(plain)" in js                                           # where the sheet isn't in the page, an action is never lost
    assert "document.querySelector('.modal.show:not(#confirmSheet)')" in js         # Bootstrap 4 can't stack dialogs: it waits for the open one
    assert "confirm-over-overlay" in js and "body.confirm-over-overlay #confirmSheet" in read("ruqqus", "assets", "style", "_components.scss")


def test_a_block_can_be_taken_back_from_where_it_is_shown():
    assert 'id="unblock-button"' in read("ruqqus", "templates", "userpage_blocking.html")
    comments = read("ruqqus", "templates", "comments.html")
    assert "post_toast('/settings/unblock?username={{ author_of(c, v).username }}'" in comments            # the blocked comment's own line, as muted ones have Unmute
    js = read("ruqqus", "assets", "js", "confirm_sheet.js")
    assert "'/settings/unblock?username='" in js and "tr('Undo')" not in js         # the toast's label is the template's own marked string
    assert 'id="toast-undo-button">{{ _("Undo") }}' in read("ruqqus", "templates", "partials", "confirm_sheet.html")


def test_the_native_popups_for_deleting_are_gone():
    for name, needles in (("assets/js/stories.js", ("window.confirm(tr('Delete this story", "window.confirm(tr('Delete this highlight")),
                          ("assets/js/live_thread.js", ("window.confirm(tr('Delete this update",)),
                          ("assets/js/bookmark_folders.js", ("window.confirm(tr('Delete this folder",)),
                          ("templates/comments.html", ("if(confirm(",)),
                          ("templates/submission.html", ("return confirm('This permanently",)),
                          ("templates/submission_deleted.html", ("return confirm('This permanently",))):
        text = read("ruqqus", *name.split("/"))
        for needle in needles:
            assert needle not in text, (name, needle)
    assert "confirmForm(event," in read("ruqqus", "templates", "submission.html") and "confirmForm(event," in read("ruqqus", "templates", "submission_deleted.html")
    # the sheet is asked from inside the story viewer (z-index 2000, above Bootstrap's dialogs): it is lifted over it
    assert "z-index: 2100;" in read("ruqqus", "assets", "style", "_components.scss")


# ---- what the independent review of the glass work found (2026-10-29) ----------------------------------------------------------------

def test_the_story_viewer_holds_still_while_the_sheet_is_open_and_deletes_the_story_that_was_asked_about():
    """The browser's confirm() froze the page; the page's own sheet doesn't, so the viewer used to keep advancing behind it and a confirmed
    delete removed whichever card was then on show (the server deleted A, the viewer dropped B). And the viewer's own Tab handler pulled focus
    back to its first button, so the sheet's buttons could not be reached from the keyboard, and Escape closed the viewer as well."""
    js = read("ruqqus", "assets", "js", "stories.js")
    remove = js[js.index("function removeStory(story) {"):js.index("function reportStory")]
    assert remove.index("pause();") < remove.index("confirmSheet(") and "else resume();" in remove
    assert "stories.findIndex(function (s) { return s.id === story.id; })" in remove and "state.index" not in remove      # by id, never "the card now on show"
    assert "if (!state) return;" in remove                                                                              # the viewer may have been closed meanwhile
    highlight = js[js.index("function deleteHighlight() {"):js.index("// ------------------------------------------------------------------ opening one")]
    assert highlight.index("pause();") < highlight.index("confirmSheet(") and "else resume();" in highlight
    keys = js[js.index("document.addEventListener('keydown'"):]
    assert keys.index("if (document.querySelector('.modal.show')) return;") < keys.index("if (event.key === 'Tab')")


def test_the_confirm_sheet_asks_one_question_at_a_time_and_hands_the_keyboard_back():
    js = read("ruqqus", "assets", "js", "confirm_sheet.js")
    assert "queue = queue.then(run, run);" in js and "function ask(el, options) {" in js
    assert "var opener = document.activeElement;" in js and "opener.focus()" in js                  # where the keyboard was, as the native pop-up did
    assert "el.querySelector('.btn-secondary')" in js and "cancel.focus()" in js                    # the least destructive button, so Enter never confirms by accident
    assert "form.removeAttribute('data-confirmed')" in js and "event.persisted" in js              # a back-forward restore must ask again
    assert "options.toast === false" in js                                                          # no Undo toast where the page reloads under it


def test_the_covers_keyboard_ring_is_as_specific_as_the_global_one_and_inside_the_cover():
    code = re.sub(r"//[^\n]*", "", GLASS)
    assert "body:not(.using-mouse) .sensitive-overlay:focus-visible {\n    outline: 2px solid #fff;\n    outline-offset: -4px;" in code
    # the global ring is `body:not(.using-mouse) :focus-visible:not(.form-control)` (0,3,1); the old (0,2,0) rule never applied, and the global
    # ring sits outside the cover and is clipped by the wrapper's overflow: hidden
    assert ".sensitive-overlay:focus-visible {" not in code.replace("body:not(.using-mouse) .sensitive-overlay:focus-visible {", "")


def test_a_dialogs_opaque_panels_do_not_cover_its_glass():
    css = compile_sheet("main_dark.scss")
    body = "\n".join(at_rule_blocks(css, SUPPORTS))
    assert ".modal-content .modal-body.bg-light" in body and ".modal-content .modal-footer.bg-white" in body
    assert ".modal-content .bg-white.rounded-lg" in body and "var(--surface-sunken)" in body


def test_the_destructive_button_is_aa_under_its_white_label():
    """Bootstrap's own red is 4.13:1 under white, and the sheet now asks before every Block, Purge and Delete: it is the most-seen button there is."""
    css = read("ruqqus", "assets", "style", "_components.scss")
    rule = css[css.index(".btn-danger {"):][:120]
    fill = re.search(r"background-color:\s*(#[0-9a-f]{6})", rule).group(1)
    assert contrast((255, 255, 255), rgb_of(fill)[0]) >= 4.5
    for state in (".btn-danger:hover {", ".btn-danger:not(:disabled):not(.disabled):active"):
        assert contrast((255, 255, 255), rgb_of(re.search(r"background-color:\s*(#[0-9a-f]{6})", css[css.index(state):][:260]).group(1))[0]) >= 4.5, state


def test_nothing_outside_the_gates_blurs_and_the_purge_handlers_keep_their_escaping():
    """The glass tests only looked inside @supports: an ungated backdrop-filter anywhere (on a card, say) was invisible to them."""
    css = compile_sheet("main.scss")
    stripped = css
    for prelude in (SUPPORTS, SOLID):
        for body in at_rule_blocks(stripped, prelude):
            stripped = stripped.replace(body, "")
    for m in re.finditer(r"(?<![\w(-])(?:-webkit-)?backdrop-filter:\s*([^;}{]+);", stripped):      # (a declaration; not the @supports condition)
        selector = stripped[:m.start()].rsplit("}", 1)[-1].split("{")[0].strip()
        if selector == ".toast" and m.group(1).strip() == "blur(10px)":
            continue                          # Bootstrap's own toast rule: _glass.scss resets it in the base layer (asserted below)
        assert m.group(1).strip() == "none", (selector, m.group(0))
    assert re.search(r"\.toast \{\s*-webkit-backdrop-filter: none;\s*backdrop-filter: none;", GLASS)
    # a translated string with quotes in it reaches an onclick attribute through these: the escaping is what keeps it a string
    for name in ("comments.html", "submission.html", "submission_deleted.html"):
        page = read("ruqqus", "templates", name)
        assert page.count("| tojson | forceescape") >= (2 if name == "comments.html" else 1), name
