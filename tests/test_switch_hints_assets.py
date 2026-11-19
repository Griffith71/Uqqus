"""What the switches in a comment box and in Create post's More options mean, as text anyone can reach (the Log's second review, C72).

The disclosure switches (Commercial promotion, Made with AI), the anonymous switch and "comment as @me" explained themselves only in a hover
tooltip or an info icon, which a phone and a keyboard never meet, and their checkbox was `hidden` (display: none), so a keyboard could not even
reach them. Now a switch is a real checkbox kept out of sight with `.sr-only` (focusable, named, described), the words behind it are a line of
plain text (`disclosure_hints`, shown while the switch is on; the locked anonymous one on your own anonymous post from the start, outside the
tools row, which a phone does not draw), and the explanations that were an info icon in the post options are visible text. What the switches do
is unchanged.

Three kinds of test: the macros rendered by Jinja with a stand-in translator (so it is the real markup), the templates' wiring read from the
source, and the script's behaviour run under node against a stand-in DOM."""
import json
import re
import subprocess
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest
from babel.messages.pofile import read_po
from jinja2 import Environment, FileSystemLoader

from chat_node_harness import node

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"


def read(*parts):
    return APP.joinpath(*parts).read_text(encoding="utf-8")


OPTIONS = read("templates", "partials", "post_options.html")
JS = read("assets", "js", "all_js.js")
CSS = read("assets", "style", "_comments.scss")


def macro_source(name):
    return OPTIONS.split("{% macro " + name + "(", 1)[1].split("{% endmacro %}", 1)[0]


ENV = Environment(loader=FileSystemLoader(str(APP / "templates")), autoescape=True)
ENV.globals["_"] = lambda text, **values: text % values if values else text
ENV.globals["pgettext"] = lambda context, text, **values: text
MODULE = ENV.get_template("partials/post_options.html").module

LOG_WARNING = "the Log may name the account"


class Page(HTMLParser):
    """Every element of a rendered macro: tag, attributes (a bare one is ""), own text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements, self.open = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": {k: ("" if v is None else v) for k, v in attrs}, "bare": [k for k, v in attrs if v is None], "text": ""}
        self.elements.append(element)
        if tag not in ("input", "br", "hr", "img", "meta", "link"):
            self.open.append(element)

    def handle_endtag(self, tag):
        if self.open and self.open[-1]["tag"] == tag:
            self.open.pop()

    def handle_data(self, data):
        if self.open:
            self.open[-1]["text"] += data

    def by_id(self, id):
        return next(e for e in self.elements if e["attrs"].get("id") == id)

    def all(self, tag):
        return [e for e in self.elements if e["tag"] == tag]


def toggles(*args, **kw):
    return Page(str(MODULE.disclosure_toggles(*args, **kw)))


def hints(*args, **kw):
    return Page(str(MODULE.disclosure_hints(*args, **kw)))


# --------------------------------------------------------------------------------- the switches

def test_every_switch_is_a_real_checkbox_a_keyboard_reaches_with_a_name_and_a_description():
    page = toggles("c-1", False, False, True, False, True)
    inputs = page.all("input")
    assert [i["attrs"]["id"] for i in inputs] == ["c-1-paid", "c-1-ai", "c-1-anon", "c-1-me"]
    for box in inputs:
        assert box["attrs"]["type"] == "checkbox"
        assert "hidden" not in box["bare"] and "hidden" not in box["attrs"], "a hidden checkbox can't be focused, named or toggled by a keyboard"
        assert box["attrs"]["class"] == "sr-only", box["attrs"]["id"]
        assert box["attrs"]["data-hint"] == box["attrs"]["id"] + "-hint"
    # a name for each (the label holds only an icon), a description for the three that have a hint of their own
    assert [i["attrs"].get("aria-label") for i in inputs[:3]] == ["Commercial promotion", "Made with AI", "Anonymous"]
    for box in inputs[:3]:
        assert box["attrs"]["aria-describedby"] == box["attrs"]["data-hint"]
    # "comment as @me" is named by its own sentence, never described by it as well (a screen reader would say it twice)
    me = inputs[3]["attrs"]
    assert me["aria-labelledby"] == "c-1-me-hint" and "aria-describedby" not in me and "aria-label" not in me
    assert me["onchange"] == "commentAsMe(this, 'c-1')"
    # the three switches that need nothing more than a state use the one function
    for box in inputs[:3]:
        assert box["attrs"]["onchange"] == "disclosureSwitch(this)"


def test_every_hint_a_switch_points_at_is_drawn_and_the_ids_the_scripts_read_are_unchanged():
    args = ("c-1", False, False, True, False, True)
    drawn = {e["attrs"]["id"] for e in hints(*args).all("p")}
    for box in toggles(*args).all("input"):
        assert box["attrs"]["data-hint"] in drawn, box["attrs"]["id"]
        assert box["attrs"]["id"] in ("c-1-paid", "c-1-ai", "c-1-anon", "c-1-me")      # post_comment and comment_edit read these ids
    assert "'-paid'" in JS and "'-ai'" in JS and "'-anon'" in JS and "'-me'" in JS


def test_the_tools_keep_their_shape_and_their_tooltips_are_only_a_shortcut_for_a_mouse():
    body = macro_source("disclosure_toggles")
    assert body.count('class="format{%') == 3 and body.count('class="format"') == 1
    assert 'data-toggle="tooltip"' in body and body.count('data-placement="top"') == 4 and 'data-placement="bottom"' not in body
    assert "btn-secondary" not in body
    # no more inline script that only toggled a colour: the one function does it and shows the line
    assert "classList.toggle('text-info'" not in body


def test_a_phone_still_draws_no_tools_row_so_the_locked_line_is_outside_the_row():
    # the phone shows only the Comment button (the owner, 2026-10-30), so the hint must not be inside .comment-format
    for template, toggles_call in (("partials/comment_form.html", 'disclosure_toggles("comment-disc-" ~ p.fullname'),
                                   ("comments.html", 'disclosure_toggles("comment-disc-" ~ c.fullname')):
        page = read("templates", template)
        row = page.split('<div class="comment-format"', 1)[1]
        assert toggles_call in row
        between = row.split(toggles_call, 1)[1].split("post_options.disclosure_hints(", 1)[0]
        assert "</div>" in between, template
    assert "@media (max-width: 767.98px)" in CSS
    phone = CSS.rsplit("@media (max-width: 767.98px)", 1)[1].split("\n}", 1)[0]
    assert ".comment-format-hint" not in phone


# --------------------------------------------------------------------------------- the lines

def test_a_plain_comment_box_draws_every_line_hidden_so_its_block_has_no_height():
    page = hints("c-1", False, False, True, False, False)
    assert [e["attrs"]["id"] for e in page.all("p")] == ["c-1-paid-hint", "c-1-ai-hint", "c-1-anon-hint"]
    for line in page.all("p"):
        assert "hidden" in line["bare"], line["attrs"]["id"]
        assert line["attrs"]["class"] == "comment-format-hint"


def test_an_edit_form_shows_the_line_of_a_flag_the_comment_already_has():
    page = hints("e-1", True, False)
    assert [(e["attrs"]["id"], "hidden" in e["bare"]) for e in page.all("p")] == [("e-1-paid-hint", False), ("e-1-ai-hint", True)]
    both = hints("e-1", True, True)
    assert not [e for e in both.all("p") if "hidden" in e["bare"]]
    # the edit form has no anonymous or "as me" switch, so no such line
    assert "anon" not in str(MODULE.disclosure_hints("e-1", True, True)) and "-me-" not in str(MODULE.disclosure_hints("e-1", True, True))


def test_on_your_own_anonymous_post_the_locked_line_is_there_from_the_start_and_says_what_a_ban_can_do():
    page = hints("c-1", False, False, True, True, True)
    anon, me = page.by_id("c-1-anon-hint"), page.by_id("c-1-me-hint")
    assert "hidden" not in anon["bare"], "comments on your own anonymous post are anonymous: say so without a hover"
    assert anon["text"].startswith("Comments on your anonymous post are anonymous.")
    assert LOG_WARNING in anon["text"], "the locked state had left out the warning the unlocked switch carries"
    assert "hidden" in me["bare"] and me["text"] == "Comment as yourself: your name shows on this comment"
    # and the switch itself is locked on, still named and described
    switch = toggles("c-1", False, False, True, True, True).by_id("c-1-anon")
    assert "checked" in switch["bare"] and "disabled" in switch["bare"]


def test_on_someone_elses_post_the_anonymous_line_waits_for_the_switch_and_carries_the_same_warning():
    page = hints("c-1", False, False, True, False, False)
    anon = page.by_id("c-1-anon-hint")
    assert "hidden" in anon["bare"] and LOG_WARNING in anon["text"] and anon["text"].startswith("Comment anonymously:")
    assert not [e for e in page.all("p") if e["attrs"]["id"].endswith("-me-hint")]


def test_no_tooltip_sentence_is_left_that_a_line_does_not_say():
    """The tooltips (a mouse's shortcut) and the lines (everyone's) are one text: nothing is said only on hover. A tooltip that is only the
    switch's own name ("Made with AI") is what its accessible name says, so it is covered by that."""
    for args in [("c-1", True, True, True, False, True), ("c-1", False, False, True, True, True), ("e-1", True, False)]:
        said = [unescape(e["text"]) for e in hints(*args).all("p")]
        page = Page(str(MODULE.disclosure_toggles(*args)))
        for label, box in zip(page.all("label"), page.all("input")):
            title = label["attrs"]["title"]
            named = box["attrs"].get("aria-label") == title
            assert named or any(title in line for line in said), title


def test_no_line_is_drawn_for_a_switch_the_form_does_not_have():
    assert [e["attrs"]["id"] for e in hints("e-1").all("p")] == ["e-1-paid-hint", "e-1-ai-hint"]
    assert [e["attrs"]["id"] for e in toggles("e-1").all("input")] == ["e-1-paid", "e-1-ai"]


def test_the_only_new_words_are_the_locked_anonymous_sentence():
    """Every other text reuses a sentence the site already has in its catalogues (a new msgid lowers every language's share)."""
    catalogue = read_po(open(APP / "translations" / "fr" / "LC_MESSAGES" / "messages.po", "rb"))
    known = {m.id for m in catalogue if m.id}
    macros = macro_source("disclosure_hints") + macro_source("disclosure_toggles") + macro_source("disclosure_field") + macro_source("anonymous_field")
    used = {unescape(m.group(2)) for m in re.finditer(r"""_\((['"])(.*?)\1\)""", macros)}
    used |= {unescape(m.group(2)) for m in re.finditer(r"""pgettext\(['"][a-z]+['"], (['"])(.*?)\1\)""", macros)}
    new = sorted(u for u in used if u not in known)
    # the one sentence may be missing from the catalogue now and present after the translation run, so this holds before and after it:
    # nothing but that sentence is ever new (an empty list is fine once the catalogues have it)
    allowed = {"Comments on your anonymous post are anonymous. If an admin bans or suspends the account for one, the Log may name the account for it."}
    assert set(new) <= allowed, new
    assert allowed & used, "the locked anonymous sentence is no longer in the macros: update this test"


# --------------------------------------------------------------------------------- the post options

def test_the_post_options_explain_themselves_in_visible_text_and_not_in_an_info_icon():
    for name in ("disclosure_field", "anonymous_field"):
        assert "fa-info-circle" not in macro_source(name), name
        assert 'data-toggle="tooltip"' not in macro_source(name), name
    field = Page(str(MODULE.disclosure_field(True, False, "post-disc")))
    group = field.by_id("post-disc-label")
    assert group["text"] == "Content disclosure"
    assert field.by_id("post-disc-hint")["text"] == "Be upfront with readers. The labels show next to the title."
    paid_hint = field.by_id("post-disc-paid-hint")
    assert paid_hint["tag"] == "span" and "audience-hint" in paid_hint["attrs"]["class"] and "services" in paid_hint["text"]
    assert field.by_id("post-disc-paid")["attrs"]["aria-describedby"] == "post-disc-paid-hint"
    assert [e for e in field.all("div") if e["attrs"].get("role") == "group"][0]["attrs"]["aria-labelledby"] == "post-disc-label"
    # the same fields, ids and values the scripts and the server read
    names = [(e["attrs"].get("name"), e["attrs"].get("id")) for e in field.all("input") if e["attrs"]["type"] == "checkbox"]
    assert names == [("paid_partnership", "post-disc-paid"), ("made_with_ai", "post-disc-ai")]
    assert len([e for e in field.all("input") if e["attrs"]["type"] == "hidden"]) == 2


def test_the_long_anonymity_sentence_is_visible_and_said_by_the_checkbox():
    page = Page(str(MODULE.anonymous_field(False, "post-anon")))
    box = page.by_id("post-anon")
    assert box["attrs"]["aria-describedby"] == "post-anon-hint" and box["attrs"]["name"] == "anonymous"
    hint = page.by_id("post-anon-hint")
    assert "A region filter still shows which region it comes from." in hint["text"] and LOG_WARNING in hint["text"]
    compact = Page(str(MODULE.anonymous_field(False, "post-anon", compact=True)))
    assert compact.by_id("post-anon")["attrs"]["aria-describedby"] == "post-anon-hint"
    assert OPTIONS.count("A region filter still shows which region it comes from.") == 2


# --------------------------------------------------------------------------------- the three comment forms

@pytest.mark.parametrize("template", ["partials/comment_form.html", "comments.html"])
def test_each_comment_form_draws_the_lines_with_the_arguments_of_its_switches_and_inside_the_form(template):
    page = read("templates", template)
    calls = re.findall(r"post_options\.disclosure_(toggles|hints)\((.*)\) \}\}", page)
    assert calls and len(calls) % 2 == 0
    # every toggles call is followed by a hints call with the same arguments
    pairs = [(calls[i], calls[i + 1]) for i in range(0, len(calls), 2)]
    for first, second in pairs:
        assert (first[0], second[0]) == ("toggles", "hints"), template
        assert first[1] == second[1], "the lines must start in the same state as their switches"
    # the hints come after the tools row closes and before the form does
    for match in re.finditer(r"post_options\.disclosure_toggles\(", page):
        after = page[match.end():]
        hint_at = after.index("post_options.disclosure_hints(")
        assert "</div>" in after[:hint_at] and "</form>" not in after[:hint_at] and "<form" not in after[:hint_at]
        assert after[hint_at:].split("</form>", 1)[0].count("post_options.disclosure_toggles(") == 0


def test_comments_html_has_the_edit_form_and_the_comment_on_a_comment_form():
    page = read("templates", "comments.html")
    assert 'disclosure_hints("comment-edit-disc-" ~ c.base36id, c.paid_partnership, c.made_with_ai)' in page
    assert page.count("post_options.disclosure_hints(") == 2
    assert read("templates", "partials", "comment_form.html").count("post_options.disclosure_hints(") == 1


# --------------------------------------------------------------------------------- the script and the style

def test_the_script_has_one_function_for_a_switch_and_comment_as_me_uses_it_for_both_boxes():
    assert "disclosureSwitch=function(box){" in JS
    body = JS.split("disclosureSwitch=function(box){", 1)[1].split("};", 1)[0]
    assert "classList.toggle('text-info', box.checked)" in body and "getAttribute('data-hint')" in body and "hint.hidden=!box.checked" in body
    settle = JS.split("commentAsMe=function(box, prefix){", 1)[1].split("if (!box.checked)", 1)[0]
    assert "disclosureSwitch(box)" in settle and "disclosureSwitch(anon)" in settle
    assert "label.classList" not in settle and "querySelectorAll" not in settle
    # no new words in the script: the sheet's are the ones the site already had
    assert "tr('Comment as yourself?')" in JS and JS.count("disclosureSwitch=function") == 1


def test_the_lines_are_muted_text_under_the_row_in_tokens_with_a_ring_on_the_tool_for_the_keyboard():
    rule = CSS.split(".comment-write .comment-format-hint {", 1)[1].split("}", 1)[0]
    for line in ("font-size: var(--text-sm);", "color: var(--text-muted);", "padding: 0 0 var(--space-2);", "margin: 0;"):
        assert line in rule, line
    assert "display" not in rule and "!important" not in rule and not re.search(r"#[0-9a-fA-F]{3,8}\b", rule), "display would beat [hidden]"
    block = CSS.split(".comment-write .comment-format-hints {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 100%;" in block and "width: 100%;" in block and "padding" not in block and "margin" not in block, "no height of its own"
    ring = CSS.split(".comment-write .comment-format .format:focus-within {", 1)[1].split("}", 1)[0]
    assert "outline: 2px solid var(--link);" in ring
    assert "body:not(.using-mouse) .comment-write .comment-format .format:focus-within {" in CSS
    # the checkbox is absolute inside the tool
    first = CSS.split(".comment-write .comment-format .format {", 1)[1].split("}", 1)[0]
    assert "position: relative;" in first
    # one rule with the tool's own selector outside the phone's, as the tools' own test splits on it
    assert CSS.count(".comment-write .comment-format .format {") == 2


# --------------------------------------------------------------------------------- the behaviour, under node

HARNESS = r"""
const SOURCE = __SOURCE__;
function Classes() { const set = new Set(); return { toggle(name, force) { const on = force === undefined ? !set.has(name) : !!force; on ? set.add(name) : set.delete(name); return on; }, contains: (n) => set.has(n), set }; }
const byId = {};
function make(id, extra) { const e = Object.assign({ id, hidden: false, checked: false, attrs: {}, getAttribute(name) { return this.attrs[name] === undefined ? null : this.attrs[name]; } }, extra || {}); byId[id] = e; return e; }
function switchOf(prefix, key, attrs) {
  const label = { classList: Classes() };
  const box = make(prefix + "-" + key, { parentNode: label, attrs: Object.assign({ "data-hint": prefix + "-" + key + "-hint" }, attrs || {}) });
  make(prefix + "-" + key + "-hint", { hidden: true });
  return { label, box, hint: byId[prefix + "-" + key + "-hint"] };
}
const document = { getElementById: (id) => byId[id] || null };
let answer = true;
const confirmSheet = () => Promise.resolve(answer);
const window = { confirmSheet };
const tr = (text) => text;
const api = new Function("document", "window", "tr", "confirm", "confirmSheet", SOURCE + "\nreturn { disclosureSwitch, commentAsMe };")(document, window, tr, () => true, confirmSheet);
const settle = () => new Promise((resolve) => setImmediate(resolve));
const out = (value) => console.log(JSON.stringify(value));
function state(s) { return { checked: s.box.checked, lit: s.label.classList.contains("text-info"), shown: !s.hint.hidden }; }
"""


def run_node(scenario):
    start = JS.index("disclosureSwitch=function(box){")
    source = JS[start:JS.index("post_comment=function(fullname){")]
    code = "(async () => {\n" + HARNESS.replace("__SOURCE__", json.dumps(source)) + "\n" + scenario + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    done = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@node
def test_a_switch_lights_its_label_and_shows_its_line_while_it_is_on_and_only_then():
    s = run_node("""
      const paid = switchOf("c", "paid");
      const first = state(paid);
      paid.box.checked = true; api.disclosureSwitch(paid.box);
      const on = state(paid);
      paid.box.checked = false; api.disclosureSwitch(paid.box);
      out({ first, on, off: state(paid) });
    """)
    assert s["first"] == {"checked": False, "lit": False, "shown": False}
    assert s["on"] == {"checked": True, "lit": True, "shown": True}
    assert s["off"] == s["first"]


@node
def test_a_switch_with_no_line_still_lights_its_label_and_breaks_nothing():
    s = run_node("""
      const label = { classList: Classes() };
      const lone = make("lone", { parentNode: label, checked: true });
      api.disclosureSwitch(lone);
      out({ lit: label.classList.contains("text-info") });
    """)
    assert s == {"lit": True}


@node
def test_comment_as_me_swaps_the_locked_anonymous_line_for_its_own_and_back():
    s = run_node("""
      const anon = switchOf("c", "anon"); anon.box.checked = true; anon.hint.hidden = false; anon.label.classList.toggle("text-info", true);
      const me = switchOf("c", "me");
      const start = { anon: state(anon), me: state(me) };
      me.box.checked = true;                                   // the member ticks it: the sheet is asked first
      const asked = api.commentAsMe(me.box, "c");
      const waiting = { anon: state(anon), me: state(me) };    // not yet: nothing changes until they say yes
      await asked; await settle();
      const yes = { anon: state(anon), me: state(me) };
      me.box.checked = false; api.commentAsMe(me.box, "c");    // ... and unticks it: back at once, nothing asked
      out({ start, waiting, yes, back: { anon: state(anon), me: state(me) } });
    """)
    assert s["start"] == {"anon": {"checked": True, "lit": True, "shown": True}, "me": {"checked": False, "lit": False, "shown": False}}
    assert s["waiting"]["me"]["checked"] is False and s["waiting"]["anon"]["shown"] is True
    assert s["yes"] == {"anon": {"checked": False, "lit": False, "shown": False}, "me": {"checked": True, "lit": True, "shown": True}}
    assert s["back"] == s["start"]


@node
def test_declining_the_sheet_changes_nothing_on_the_page():
    s = run_node("""
      const anon = switchOf("c", "anon"); anon.box.checked = true; anon.hint.hidden = false; anon.label.classList.toggle("text-info", true);
      const me = switchOf("c", "me");
      const start = { anon: state(anon), me: state(me) };
      answer = false;
      me.box.checked = true; api.commentAsMe(me.box, "c"); await settle();
      out({ start, after: { anon: state(anon), me: state(me) } });
    """)
    assert s["after"] == s["start"]


@node
def test_a_form_with_no_anonymous_switch_still_lets_comment_as_me_work_alone():
    s = run_node("""
      const me = switchOf("c", "me");
      me.box.checked = true; api.commentAsMe(me.box, "c"); await settle();
      out(state(me));
    """)
    assert s == {"checked": True, "lit": True, "shown": True}
