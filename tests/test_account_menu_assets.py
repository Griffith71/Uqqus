"""The account menu (templates/partials/account_menu.html): one menu shared by both page shells, its items in groups told
apart by a line, the Dark mode row in the shape all_js.js looks for, a different icon for every item, and "Send feedback"
reachable from every page that draws the menu."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"
NAMES = set((ROOT / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split())


def read(*parts):
    return TEMPLATES.joinpath(*parts).read_text(encoding="utf-8")


PARTIAL = read("partials", "account_menu.html")
SHELLS = ("default.html", "header.html")
STANDALONE = ("settings.html", "submit.html", "guild/guild_settings.html")
DIVIDER = '<div class="dropdown-divider" role="separator"></div>'
BODY = re.sub(r"\{#.*?#\}", "", PARTIAL, flags=re.S)
ITEM = re.compile(r'<a class="dropdown-item[^"]*"[^>]*>.*?</a>', re.S)

GROUPS = [["My profile", "Create post", "Creator Studio", "Circles"],
          ["Inbox", "Chat", "Spaces"],
          ["History", "Settings", "Dark mode"],
          ["Send feedback", "Log out"]]


def label(item):
    return re.search(r'\{\{ _\("([^"]+)"\) \}\}', item).group(1)


def groups():
    """The labels of the menu's items, a list per group, in the order the file writes them (the Inbox and Chat items are
    written twice, with and without a badge: one each)."""
    out = []
    for part in BODY.split(DIVIDER):
        seen = []
        for item in ITEM.findall(part):
            if label(item) not in seen:
                seen.append(label(item))
        out.append(seen)
    return out


def icons():
    """{label: icon name} for the items that have one (the profile row has an avatar)."""
    found = {}
    for item in ITEM.findall(BODY):
        icon = re.search(r'<i class="fas (fa-[\w-]+) fa-fw text-left mr-3"', item)
        if icon:
            found.setdefault(label(item), set()).add(icon.group(1))
    return found


def test_the_items_are_in_four_groups_with_the_profile_row_first():
    assert groups() == GROUPS
    assert BODY.count(DIVIDER) == 3                                     # a line between groups, none after the last
    assert 'account-menu-profile' in ITEM.findall(BODY)[0]              # the profile row opens the menu


def test_every_item_has_its_own_free_icon_and_it_comes_first():
    found = icons()
    assert set(found) == {name for group in GROUPS for name in group} - {"My profile"}
    for name, names in found.items():
        assert len(names) <= 2, name                                    # only Dark mode changes its icon (on and off)
        assert all(n.removeprefix("fa-") in NAMES for n in names), name          # a name only the Pro set has draws as nothing
    flat = [next(iter(sorted(names))) for names in found.values()]
    assert len(flat) == len(set(flat)), "two items share an icon"
    for item in ITEM.findall(BODY)[1:]:
        assert re.match(r'<a class="dropdown-item"[^>]*><i class="fas fa-', item), item[:80]


def test_send_feedback_is_not_a_speech_bubble_because_chat_already_is():
    found = icons()
    assert found["Send feedback"] == {"fa-bullhorn"} and found["Chat"] == {"fa-comment-dots"}
    assert PARTIAL.count("fa-comment-dots") == 2                        # Chat's, with and without a badge, and nothing else
    assert "fa-comment-dots" not in read("partials", "site_feedback_modal.html")
    assert 'href="/help/contact" data-feedback-open' in BODY            # without scripts it is the old contact page


def test_the_dark_mode_row_keeps_the_shape_all_js_looks_for():
    js = (ROOT / "ruqqus" / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
    assert "[id=\"dark-switch\"], #dark-switch-mobile" in js and "[onclick*=\"switch_css\"]" in js
    rows = [item for item in ITEM.findall(BODY) if label(item) == "Dark mode"]
    assert len(rows) == 2                                               # on and off
    for row in rows:
        assert 'onclick="switch_css()"' in row and 'id="dark-switch"' in row
        assert re.match(r'<a class="dropdown-item" href="javascript:void\(0\)" onclick="switch_css\(\)"><i class="fas fa-toggle-(on|off) ', row)
    assert "{% if v.can_use_darkmode and theme_choice() == \"dark\" %}" in BODY and "{% elif v.can_use_darkmode %}" in BODY


def test_log_out_still_forgets_the_chat_key_and_posts_the_logout():
    (row,) = [item for item in ITEM.findall(BODY) if label(item) == "Log out"]
    assert "data-logout" in row and "localStorage.removeItem('uqqus_chat_prekey')" in row and "post('/logout'" in row


def test_the_badges_still_show_what_is_unread():
    assert "{{ v.notifications_count }}" in BODY and "{{ chat_badge }}" in BODY
    assert 'href="/notifications?all=true"' in BODY and 'href="/notifications"' in BODY


def test_both_shells_draw_the_one_menu_and_keep_no_copy_of_it():
    for shell in SHELLS:
        page = read(shell)
        assert page.count('{% include "partials/account_menu.html" %}') == 1, shell
        wrapper = re.search(r'class="(dropdown-menu [^"]*\baccount-menu\b[^"]*)"\s+(id="accountDropdownMenu"\s+)?aria-labelledby=', page)
        assert wrapper, shell
        # right-aligned at every width: "-lg-left" put the menu's left edge at the toggle, and the toggle is at the window's edge
        assert "dropdown-menu-right" in wrapper.group(1) and "dropdown-menu-lg-left" not in wrapper.group(1), shell
        assert "text-left mr-3" not in page, shell                      # the dropdown's item markup is the partial's now
        assert 'id="dark-switch"' not in page, shell
    assert "data-feedback-open" not in read("default.html")             # its phone menu is for visitors only
    # header.html shows members its phone menu (the avatar has no dropdown on a phone there): its own entry, above Log out
    header = read("header.html")
    assert header.count("data-feedback-open") == 1
    assert header.index("data-feedback-open") < header.rindex("data-logout") and "fa-bullhorn" in header
    assert 'id="accountDropdownMenu"' in read("default.html")           # the phone's full-screen rules in main*.scss key on it


def test_send_feedback_works_on_every_page_that_draws_the_menu():
    sheet = '{% include "partials/site_feedback_modal.html" %}'
    script = "{{ asset_url('js/site_feedback.js') }}"
    for shell in ("default.html",) + STANDALONE:
        page = read(*shell.split("/"))
        assert sheet in page and script in page, shell
        assert page.index(sheet) < page.index(script), shell
        # the sheet confirms or refuses a send with the post toasts
        assert 'id="toast-post-success-text"' in page and 'id="toast-post-error-text"' in page, shell
        assert "{% if v" in page.split(sheet)[0].rsplit("\n", 1)[-1], shell      # members only
    for shell in ("settings.html", "submit.html", "guild/guild_settings.html", "default.html"):
        assert '{% from "partials/i18n_boot.html" import i18n_boot %}' in read(*shell.split("/")), shell     # tr() for the script


def test_the_menus_look_is_tokens_only_and_reaches_both_menus():
    sheet = (ROOT / "ruqqus" / "assets" / "style" / "_components.scss").read_text(encoding="utf-8")
    block = sheet.split("// --- the account menu ", 1)[1]
    assert ".account-menu {" in block and ".account-menu .dropdown-divider {" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", block) and "var(--" in block        # no raw colours
    assert "#accountDropdownMenu" not in block.split("\n\n", 2)[-1]                    # keyed on the class both menus carry
