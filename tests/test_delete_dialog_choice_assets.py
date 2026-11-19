"""The delete dialogs' Delete / Remove my name choice (the Log's second review, C70): the dialog's title, its icon and its confirm button
say and look like what is chosen. Red is for a real delete only, the dialog has an accessible name, and a refused request puts the
buttons back so the member can try again or switch. The templates carry both versions of each text and class pair; the script
(`deleteDialogChoice` in assets/js/all_js.js) only swaps them, so no string lives in the script and every reader sees their own language.

Three kinds of test: the markup (read from the two templates), the script's wiring (read from all_js.js), and the behaviour, which runs
the real script under node against a stand-in DOM built from the real templates' tags, so the two stay in step."""
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from chat_node_harness import node

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"
JS = (ROOT / "ruqqus" / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")

POST = "delete_post_modal.html"
COMMENT = "delete_comment_modal.html"


def source(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def rendered(name):
    """The template as the English reader gets it: Jinja comments dropped, every `_("...")` written out. These two templates use nothing else."""
    text = re.sub(r"\{#.*?#\}", "", source(name), flags=re.S)
    text = re.sub(r"\{\{\s*_\((['\"])(.*?)\1\)\s*\}\}", lambda m: m.group(2), text)
    assert "{{" not in text and "{%" not in text, name
    return text


class Tags(HTMLParser):
    """Every element of a template: its tag, attributes, bare (boolean) attributes, own text and parent."""
    VOID = {"input", "br", "img", "hr", "meta", "link"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": {k: ("" if v is None else v) for k, v in attrs}, "bools": [k for k, v in attrs if v is None],
                   "text": "", "parent": self.stack[-1] if self.stack else None}
        self.elements.append(element)
        if tag not in self.VOID:
            self.stack.append(len(self.elements) - 1)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack:
            self.elements[self.stack[-1]]["text"] += data


def tags(name):
    parser = Tags()
    parser.feed(rendered(name))
    for element in parser.elements:
        element["text"] = element["text"].strip()
    return parser.elements


def with_attr(name, attribute):
    return [e for e in tags(name) if attribute in e["attrs"]]


def classes(element):
    return element["attrs"].get("class", "").split()


# --- the markup -------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, title, button, buttons", [(POST, "Delete post?", "Delete post", 2), (COMMENT, "Delete comment?", "Delete comment", 1)])
def test_the_title_and_the_confirm_buttons_carry_both_versions_and_open_on_delete(name, title, button, buttons):
    titled = [e for e in with_attr(name, "data-text-delete") if e["tag"] in ("h5", "div")]
    assert titled and all(e["attrs"]["data-text-delete"] == title and e["attrs"]["data-text-unlink"] == "Remove my name" for e in titled), name
    confirm = [e for e in with_attr(name, "data-text-delete") if e["tag"] == "button"]
    assert len(confirm) == buttons, name
    for e in confirm:
        assert e["attrs"]["data-text-delete"] == button and e["attrs"]["data-text-unlink"] == "Remove my name", e["attrs"]["id"]
        assert e["attrs"]["data-class-delete"] == "btn-danger" and e["attrs"]["data-class-unlink"] == "btn-primary", e["attrs"]["id"]
        assert e["attrs"]["id"].startswith(("deletePostButton", "deleteCommentButton"))
        assert e["attrs"]["type"] == "button", "the dialog's buttons never submit a form"
    for e in with_attr(name, "data-text-delete"):
        # what is written in the tag is the "Delete it" version, so a page where the script didn't run says what it always said
        assert e["text"] == e["attrs"]["data-text-delete"], e
    for e in with_attr(name, "data-class-delete"):
        delete, unlink = e["attrs"]["data-class-delete"].split(), e["attrs"]["data-class-unlink"].split()
        assert set(delete) <= set(classes(e)), e
        assert not (set(unlink) - set(delete)) & set(classes(e)), e


@pytest.mark.parametrize("name, radio_label", [(POST, "delete-post-how-unlink"), (COMMENT, "delete-comment-how-unlink")])
def test_the_unlink_text_is_the_radios_own_label(name, radio_label):
    label = next(e for e in tags(name) if e["tag"] == "label" and e["attrs"].get("for") == radio_label)
    unlink_texts = {e["attrs"]["data-text-unlink"] for e in with_attr(name, "data-text-unlink")}
    assert unlink_texts == {label["text"]} == {"Remove my name"}


@pytest.mark.parametrize("name", [POST, COMMENT])
def test_red_marks_a_real_delete_only(name):
    # everything that is red or a trash can by default has an unlink version that is neither
    for e in tags(name):
        if "btn-danger" in classes(e) and e["tag"] == "button":
            assert e["attrs"]["data-class-unlink"] == "btn-primary", e["attrs"].get("id")
        if "text-danger" in classes(e):
            assert "text-danger" not in e["attrs"].get("data-class-unlink", "").split(), e
        if "fa-trash-alt" in classes(e):
            assert "fa-trash-alt" not in e["attrs"].get("data-class-unlink", "").split(), e
            assert "fa-user-secret" in e["attrs"]["data-class-unlink"].split(), e


def test_no_msgid_was_invented():
    # every text the dialogs can say is one the catalogues already have (a new msgid lowers every language's share: READY_SHARE)
    catalogue = next((ROOT / "ruqqus" / "translations").glob("*/LC_MESSAGES/messages.po")).read_text(encoding="utf-8")
    for name in (POST, COMMENT):
        for e in with_attr(name, "data-text-delete"):
            for attribute in ("data-text-delete", "data-text-unlink"):
                assert f'\nmsgid "{e["attrs"][attribute]}"\n' in catalogue, (name, e["attrs"][attribute])


@pytest.mark.parametrize("name, label_id", [(POST, "deletePostTitle"), (COMMENT, "deleteCommentTitle")])
def test_each_dialog_has_an_accessible_name_that_exists_once(name, label_id):
    root = next(e for e in tags(name) if e["attrs"].get("role") == "dialog")
    assert root["attrs"]["aria-labelledby"] == label_id
    named = [e for e in tags(name) if e["attrs"].get("id") == label_id]
    assert len(named) == 1 and named[0]["text"], "the name is the title, which is never empty"
    assert "data-text-delete" in named[0]["attrs"], "and it follows the choice"


def test_the_old_title_ids_are_never_given_to_anything():
    # tip_modal.html still names `deletePostModalTitle` in its own aria-labelledby: giving the delete dialog's heading that id would have the
    # tip dialog announced as "Delete post?"
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for old in ("deletePostModalTitle", "deleteCommentModalTitle"):
            assert f'id="{old}"' not in text and f"id='{old}'" not in text, (path.name, old)


# --- the script's wiring -----------------------------------------------------------------------------------------------------

def helper_source():
    return "function deleteDialogChoice(" + JS.split("function deleteDialogChoice(")[1].split("\n}\n")[0] + "\n}\n"


def modal_source(fn):
    return JS.split(f"function {fn}(id, canUnlink)")[1].split("\n};")[0]


def test_the_helper_swaps_attributes_and_holds_no_string_of_its_own():
    assert JS.index("function deleteDialogChoice(") < JS.index("// Delete Post") < JS.index("function delete_postModal(")
    body = helper_source()
    assert "innerHTML" not in body, "the texts are put in as text"
    strings = set(re.findall(r'"([^"]*)"', body))
    assert strings == {"[data-text-delete]", "[data-class-delete]", "data-text-unlink", "data-text-delete", "data-class-delete",
                       "data-class-unlink", " "}, strings
    assert "tr(" not in body and "trn(" not in body


@pytest.mark.parametrize("fn, radios, dialog", [("delete_postModal", "delete-post-how", "deletePostModal"), ("delete_commentModal", "delete-comment-how", "deleteCommentModal")])
def test_each_dialog_follows_its_radios_and_comes_back_after_a_refusal(fn, radios, dialog):
    body = modal_source(fn)
    assert f'getElementById("{dialog}")' in body
    assert f"input[name=\"{radios}\"]').forEach(function(radio) {{ radio.onchange = show; }});" in body, "onchange, not addEventListener: one handler per radio however often it opens"
    assert "deleteDialogChoice(dialog, canUnlink && unlinkRadio.checked)" in body, "never the unlink wording where the server won't unlink"
    assert body.count("\n  ready();\n") == 1, "it opens ready: enabled, with the Delete wording"
    assert re.search(r"\{how: how[^\n]*\},\n\s+refused\n", body), "the refusal puts the buttons back"
    assert re.search(r"var refused = function\(\) \{\n\s+ready\(\);\n\s+(if \(pressed\) pressed|button)\.focus\(\);", body), "and the keyboard's focus"
    assert "choice.disabled = true;" in body and "choice.disabled = false;" in body
    assert ".addEventListener(" not in body


def test_the_post_dialog_holds_both_buttons_while_the_request_is_out():
    body = modal_source("delete_postModal")
    assert "buttons.forEach(function(button) { button.disabled = true; });" in body
    assert "buttons.forEach(function(button) { button.disabled = false; });" in body


# --- the behaviour: the real script, a stand-in DOM built from the real templates -------------------------------------------

HARNESS = r"""
const vm = require("vm");
const ELEMENTS = __ELEMENTS__;
const SOURCE = __SOURCE__;
const all = [];
const byId = {};
class El {
  constructor(spec, index) {
    this.index = index; this.tag = spec.tag; this.attrs = spec.attrs; this.parentIndex = spec.parent;
    this.classes = new Set((spec.attrs["class"] || "").split(/\s+/).filter(Boolean));
    this.classList = { add: (c) => this.classes.add(c), remove: (c) => this.classes.delete(c), contains: (c) => this.classes.has(c) };
    this._text = spec.text; this._html = null;
    this.hidden = spec.bools.includes("hidden"); this._checked = spec.bools.includes("checked");
    this.disabled = false; this.onclick = null; this.onchange = null;
  }
  focus() { focused = this.attrs.id || null; }
  get checked() { return this._checked; }
  set checked(value) {
    this._checked = !!value;
    if (value && this.attrs.type === "radio") for (const other of all) if (other !== this && other.attrs.type === "radio" && other.attrs.name === this.attrs.name) other._checked = false;
  }
  get textContent() { return this._text; }
  set textContent(value) { this._text = String(value); this._html = null; }
  get innerHTML() { return this._html === null ? this._text : this._html; }
  set innerHTML(value) { this._html = String(value); this._text = this._html.replace(/<[^>]*>/g, ""); }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  inside(root) { for (let i = this.parentIndex; i !== null && i !== undefined; i = all[i].parentIndex) if (all[i] === root) return true; return false; }
  querySelectorAll(selector) { return all.filter((el) => el.inside(this) && matches(el, selector)); }
}
function matches(el, selector) {
  let m;
  if ((m = /^\[([\w-]+)\]$/.exec(selector))) return m[1] in el.attrs;
  if ((m = /^input\[name="([\w-]+)"\]$/.exec(selector))) return el.tag === "input" && el.attrs.name === m[1];
  throw new Error("selector not faked: " + selector);
}
ELEMENTS.forEach((spec, i) => { const el = new El(spec, i); all.push(el); if (spec.attrs.id) byId[spec.attrs.id] = el; });
const document = {
  getElementById: (id) => byId[id] || null,
  querySelectorAll: (selector) => all.filter((el) => matches(el, selector)),
};
let focused = null;
const posts = [];
const post = (url, callback, errortext, extra, failed) => { posts.push({ url, extra, failed, callback }); };
const requests = [];
let counts = {};
const fetch = (url) => { requests.push(url); const body = counts[url]; return Promise.resolve({ ok: body !== undefined, json: () => Promise.resolve(body) }); };
const tr = (text) => text;
const trn = (one, many, n, values) => many.replace("%(num)s", values.num);
const escapeHtmlText = (text) => text;
const location = { reload() {} };
const api = new Function("document", "post", "fetch", "tr", "trn", "escapeHtmlText", "location",
  SOURCE + "\nreturn { delete_postModal, delete_commentModal };")(document, post, fetch, tr, trn, escapeHtmlText, location);
const settle = () => new Promise((resolve) => setImmediate(resolve));
function pick(id) { const radio = byId[id]; radio.checked = true; if (radio.onchange) radio.onchange(); }
function look(el) { return { text: el.textContent, classes: [...el.classes].sort(), disabled: el.disabled }; }
function press(id) { const button = byId[id]; button.onclick.call(button); return look(button); }
const find = (test) => all.find(test);
const report = (value) => console.log(JSON.stringify(value));
"""


def run_node(parts, scenario):
    elements = []
    for name in parts:
        base = len(elements)
        for e in tags(name):
            e = dict(e, parent=None if e["parent"] is None else e["parent"] + base)
            elements.append(e)
    start = JS.index("function deleteDialogChoice(")
    script = HARNESS.replace("__ELEMENTS__", json.dumps(elements)).replace("__SOURCE__", json.dumps(JS[start:JS.index("//Email verification text")]))
    code = "(async () => {\n" + script + "\n" + scenario + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


POST_VIEW = r"""
function view() {
  const phoneTitle = find((e) => e.classes.has("h4"));
  return {
    title: look(byId.deletePostTitle), phoneTitle: look(phoneTitle),
    button: look(byId.deletePostButton), phoneButton: look(byId["deletePostButton-mobile"]),
    icon: look(find((e) => e.classes.has("d-md-block"))), disc: look(find((e) => e.classes.has("fa-stack-2x"))), glyph: look(find((e) => e.classes.has("fa-stack-1x"))),
    choice: byId.deletePostChoice.disabled, ticks: byId.deletePostCommentsOption.hidden, unlinkOption: byId.deletePostUnlinkOption.hidden,
  };
}
"""

COMMENT_VIEW = r"""
function view() {
  return {
    title: look(byId.deleteCommentTitle), button: look(byId.deleteCommentButton), icon: look(find((e) => e.classes.has("fa-trash-alt") || e.classes.has("fa-user-secret"))),
    choice: byId.deleteCommentChoice.disabled, unlinkOption: byId.deleteCommentUnlinkOption.hidden,
  };
}
"""


def has(view, *names):
    return set(names) <= set(view["classes"])


def lacks(view, *names):
    return not set(names) & set(view["classes"])


@node
def test_the_post_dialog_says_delete_in_red_and_remove_my_name_in_blue_and_back():
    s = run_node([POST], POST_VIEW + """
      api.delete_postModal("abc12", true);
      const opened = view();
      pick("delete-post-how-unlink");
      const unlink = view();
      pick("delete-post-how-delete");
      const back = view();
      report({ opened, unlink, back });
    """)
    opened, unlink, back = s["opened"], s["unlink"], s["back"]
    assert opened["title"]["text"] == opened["phoneTitle"]["text"] == "Delete post?"
    for key in ("button", "phoneButton"):
        assert opened[key]["text"] == "Delete post" and has(opened[key], "btn", "btn-danger") and lacks(opened[key], "btn-primary"), key
    assert has(opened["icon"], "fas", "fa-trash-alt", "text-muted") and lacks(opened["icon"], "fa-user-secret")
    assert has(opened["disc"], "text-danger", "opacity-25") and has(opened["glyph"], "text-danger", "fa-trash-alt")
    # "Remove my name" is chosen: the title, the icon and the button say so, and nothing is red
    assert unlink["title"]["text"] == unlink["phoneTitle"]["text"] == "Remove my name"
    for key in ("button", "phoneButton"):
        assert unlink[key]["text"] == "Remove my name" and has(unlink[key], "btn", "btn-primary") and lacks(unlink[key], "btn-danger"), key
    assert has(unlink["phoneButton"], "btn-block", "btn-lg"), "the rest of the button's classes stay"
    assert has(unlink["icon"], "fas", "fa-user-secret", "text-muted") and lacks(unlink["icon"], "fa-trash-alt")
    assert has(unlink["disc"], "text-primary", "opacity-25") and lacks(unlink["disc"], "text-danger")
    assert has(unlink["glyph"], "text-primary", "fa-user-secret", "fa-stack-1x") and lacks(unlink["glyph"], "text-danger", "fa-trash-alt")
    assert back == opened, "choosing Delete again puts everything back"


@node
def test_where_the_server_will_not_unlink_the_dialog_stays_a_delete_dialog():
    s = run_node([POST], POST_VIEW + """
      api.delete_postModal("abc12", false);
      const opened = view();
      byId["delete-post-how-unlink"].checked = true;        // not offered, but should it ever be checked
      byId["delete-post-how-unlink"].onchange();
      report({ opened, forced: view() });
    """)
    assert s["opened"]["unlinkOption"] is True
    assert s["forced"] == s["opened"]
    assert s["forced"]["button"]["text"] == "Delete post" and "btn-danger" in s["forced"]["button"]["classes"]


@node
def test_a_refusal_puts_the_buttons_back_and_the_choice_stays_changeable():
    s = run_node([POST], POST_VIEW + """
      counts["/api/post/abc12/own_comment_count"] = { count: 2 };
      api.delete_postModal("abc12", true);
      await settle();
      pick("delete-post-how-unlink");
      const chosen = view();
      const pressed = press("deletePostButton-mobile");
      const during = view();
      posts[0].failed();                                     // the route said no: a 409, a 429 or need_password
      const after = view();
      const focusBack = focused;
      pick("delete-post-how-delete");
      report({ chosen, pressed, during, after, focusBack, deleted: view(), post: { url: posts[0].url, extra: posts[0].extra }, asked: posts.length });
    """)
    assert s["asked"] == 1 and s["post"]["url"] == "/delete_post/abc12" and s["post"]["extra"] == {"how": "unlink", "with_comments": "1"}
    assert s["pressed"]["text"] == "Removing your name" and s["pressed"]["disabled"] is True
    assert s["during"]["button"]["disabled"] is True and s["during"]["phoneButton"]["disabled"] is True and s["during"]["choice"] is True
    # the refusal: enabled, no spinner, and still the wording and colour of what is chosen
    assert s["after"] == s["chosen"], "back to what it was before the press"
    assert s["focusBack"] == "deletePostButton-mobile", "the keyboard's focus went with the disabled button: it comes back to the one pressed"
    assert s["after"]["button"]["disabled"] is False and s["after"]["choice"] is False and s["after"]["phoneButton"]["text"] == "Remove my name"
    assert s["deleted"]["button"]["text"] == "Delete post" and "btn-danger" in s["deleted"]["button"]["classes"]


@node
def test_a_dialog_opened_after_an_unfinished_try_opens_clean():
    # the buttons are shared by every post on the page: a try that never came back must not leave the next post's dialog disabled and spinning
    s = run_node([POST], POST_VIEW + """
      api.delete_postModal("first", true);
      pick("delete-post-how-unlink");
      press("deletePostButton");
      const stuck = view();
      api.delete_postModal("second", true);
      report({ stuck, reopened: view(), radioDelete: byId["delete-post-how-delete"].checked, radioUnlink: byId["delete-post-how-unlink"].checked });
    """)
    assert s["stuck"]["button"]["disabled"] is True and s["stuck"]["button"]["text"] == "Removing your name"
    reopened = s["reopened"]
    assert reopened["button"]["disabled"] is False and reopened["phoneButton"]["disabled"] is False and reopened["choice"] is False
    assert reopened["button"]["text"] == "Delete post" and "btn-danger" in reopened["button"]["classes"]
    assert s["radioDelete"] is True and s["radioUnlink"] is False


@node
def test_the_tick_box_and_what_it_sends_are_unchanged():
    s = run_node([POST], POST_VIEW + """
      counts["/api/post/abc12/own_comment_count"] = { count: 3 };
      api.delete_postModal("abc12", true);
      const early = view().ticks;                            // hidden until the count is known
      await settle();
      const underDelete = view().ticks;                      // and under Delete
      const label = byId["delete-post-with-comments-label"].textContent;
      press("deletePostButton"); const sentDelete = posts.at(-1).extra; posts.at(-1).failed();
      pick("delete-post-how-unlink");
      const underUnlink = view().ticks;
      press("deletePostButton"); const sentTicked = posts.at(-1).extra; posts.at(-1).failed();
      byId["delete-post-with-comments"].checked = false;
      press("deletePostButton"); const sentUnticked = posts.at(-1).extra; posts.at(-1).failed();
      pick("delete-post-how-delete");
      report({ early, underDelete, label, underUnlink, backUnderDelete: view().ticks, sentDelete, sentTicked, sentUnticked });
    """)
    assert s["early"] is True and s["underDelete"] is True and s["underUnlink"] is False and s["backUnderDelete"] is True
    assert s["label"] == "Also remove my name from my 3 comments here"
    assert s["sentDelete"] == {"how": "delete", "with_comments": ""}
    assert s["sentTicked"] == {"how": "unlink", "with_comments": "1"}
    assert s["sentUnticked"] == {"how": "unlink", "with_comments": ""}


@node
def test_a_post_with_no_comments_of_its_own_never_shows_the_tick_box():
    s = run_node([POST], POST_VIEW + """
      counts["/api/post/abc12/own_comment_count"] = { count: 0 };
      api.delete_postModal("abc12", true);
      await settle();
      pick("delete-post-how-unlink");
      report({ ticks: view().ticks, fetched: requests });
    """)
    assert s["ticks"] is True and s["fetched"] == ["/api/post/abc12/own_comment_count"]


@node
def test_the_comment_dialog_follows_its_radios_the_same_way():
    s = run_node([COMMENT], COMMENT_VIEW + """
      api.delete_commentModal("xyz", true);
      const opened = view();
      pick("delete-comment-how-unlink");
      const unlink = view();
      const pressed = press("deleteCommentButton");
      const during = view();
      posts[0].failed();
      const after = view();
      const focusBack = focused;
      pick("delete-comment-how-delete");
      const back = view();
      press("deleteCommentButton");
      report({ opened, unlink, pressed, during, after, focusBack, back, sent: posts.map((p) => [p.url, p.extra]) });
    """)
    opened, unlink = s["opened"], s["unlink"]
    assert opened["title"]["text"] == "Delete comment?" and opened["button"]["text"] == "Delete comment"
    assert has(opened["button"], "btn", "btn-danger") and lacks(opened["button"], "btn-primary") and has(opened["icon"], "fa-trash-alt")
    assert unlink["title"]["text"] == "Remove my name" and unlink["button"]["text"] == "Remove my name"
    assert has(unlink["button"], "btn", "btn-primary") and lacks(unlink["button"], "btn-danger")
    assert has(unlink["icon"], "fa-user-secret") and lacks(unlink["icon"], "fa-trash-alt")
    assert s["pressed"]["text"] == "Removing your name" and s["during"]["button"]["disabled"] is True and s["during"]["choice"] is True
    assert s["after"] == unlink, "a refusal puts the dialog back as it was, the choice still changeable"
    assert s["focusBack"] == "deleteCommentButton"
    assert s["back"] == opened
    assert s["sent"] == [["/delete/comment/xyz", {"how": "unlink"}], ["/delete/comment/xyz", {"how": "delete"}]]


@node
def test_the_comment_dialog_without_unlinking_stays_a_delete_dialog():
    s = run_node([COMMENT], COMMENT_VIEW + """
      api.delete_commentModal("xyz", false);
      const opened = view();
      byId["delete-comment-how-unlink"].checked = true;
      byId["delete-comment-how-unlink"].onchange();
      report({ opened, forced: view() });
    """)
    assert s["opened"]["unlinkOption"] is True and s["forced"] == s["opened"]
    assert s["forced"]["button"]["text"] == "Delete comment" and "btn-danger" in s["forced"]["button"]["classes"]
