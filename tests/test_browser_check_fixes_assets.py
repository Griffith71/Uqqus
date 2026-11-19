"""What a real-browser look at the Log's leftover work found (2026-11-17), pinned so it stays fixed: the delete dialog's tick box is a checkbox and not a
round dot, the Log thread's Unmute link and the Log's Report button are readable, the Prev link of the Log's pages can be reached from the keyboard, and the
four comment-box switches' tooltips stay a mouse shortcut."""
from html.parser import HTMLParser
from pathlib import Path

RUQQUS = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


class Ancestry(HTMLParser):
    """The classes of every open element above the element with the wanted id."""

    VOID = {"input", "br", "img", "hr", "meta", "link"}

    def __init__(self, wanted):
        super().__init__()
        self.wanted, self.stack, self.found = wanted, [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == self.wanted:
            self.found = [classes for _, classes in self.stack]
        if tag not in self.VOID:
            self.stack.append((tag, attrs.get("class") or ""))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def test_the_delete_dialogs_tick_box_is_not_inside_a_radio_s_wrapper():
    """Inside `.custom-radio` the radio's own `::before` / `::after` styles reach the nested checkbox and drew it as a round dot."""
    parser = Ancestry("deletePostCommentsOption")
    parser.feed(read("templates", "delete_post_modal.html"))
    assert parser.found is not None, "the tick box is still in the dialog"
    assert not any("custom-radio" in classes for classes in parser.found), parser.found
    assert "custom-checkbox" in read("templates", "delete_post_modal.html").split('id="deletePostCommentsOption"')[0].rsplit("<div", 1)[1]


def test_the_log_thread_s_muted_placeholder_link_is_underlined_and_the_report_button_is_not_red_on_white():
    thread = read("templates", "log_thread.html")
    assert 'pl-3 log-thread-placeholder">' in thread.split('c.kind == "muted"')[1].split("{% else %}")[0]
    style = read("assets", "style", "_log_pages.scss")
    assert ".log-thread-placeholder a {\n    text-decoration: underline;" in style
    form = read("templates", "partials", "log_report_form.html")
    assert "btn-outline-secondary" in form and "btn-outline-danger" not in form, "red text on white is 4.1:1"


def test_the_prev_link_of_the_log_pages_is_reachable_by_the_keyboard():
    about = read("templates", "log_about.html")
    prev = about.split("{% if page > 1 %}")[1].split("{% else %}")[0]
    assert "tabindex" not in prev and 'href="?page={{ page - 1 }}"' in prev


def test_the_switch_tooltips_are_a_mouse_shortcut_only():
    options = read("templates", "partials", "post_options.html")
    for suffix in ("paid", "ai", "anon", "me"):
        tips = [part.split(">")[0] for part in options.split(f'for="{{{{ prefix }}}}-{suffix}"')[1:] if part.startswith(' data-toggle="tooltip"') or ' data-toggle="tooltip"' in part.split(">")[0]]
        assert tips, f"{suffix} has a tooltip label"
        assert all('data-trigger="hover"' in tip for tip in tips), suffix
