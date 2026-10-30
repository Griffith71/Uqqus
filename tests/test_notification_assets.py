"""The notifications page in the source: x.com's layout (tabs, bundled rows, a "..." menu), the data it is drawn from, the menu's
script against the routes the site has, and the stylesheet. The rules themselves are tests/test_notification_groups.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"
NAMES = set((ROOT / "tests" / "data" / "fa_free_6_4_0_names.txt").read_text(encoding="utf-8").split())


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


PAGE = read("templates", "notifications.html")
ROW = read("templates", "partials", "notification_row.html")
JS = read("assets", "js", "notification_actions.js")
STYLE = read("assets", "style", "_notifications.scss")
GROUPS = read("helpers", "notification_groups.py")
FEED = read("helpers", "notification_feed.py")
FRONT = read("routes", "front.py")
USER = read("classes", "user.py")


def route(name):
    body = FRONT.split(f"\ndef {name}(")[1]
    return re.split(r"\n(?=@|def |class )", body, maxsplit=1)[0]


def test_the_page_is_tabs_over_bundled_rows_and_keeps_what_the_side_panel_and_layout_need():
    assert '{% from "partials/notification_row.html" import row %}' in PAGE
    assert "{% for grp in groups %}{{ row(grp, v) }}{% endfor %}" in PAGE
    assert '<div class="row mx-0 border-bottom bg-white sticky' in PAGE                      # the tab row (phones don't scroll sideways)
    assert 'class="nav settings-nav' in PAGE                                                  # embedded.js keeps these links in the frame
    assert "{% block sidebar %}{% endblock %}" in PAGE and "{% block main_row_class %} wide-main is-focused{% endblock %}" in PAGE
    assert "asset_url('js/notification_actions.js')" in PAGE and 'src="/assets' not in PAGE
    for tab, href in (("all", "/notifications/all"), ("unread", "/notifications"), ("posts", "/notifications/posts"),
                      ("comments", "/notifications/replies"), ("mentions", "/notifications/mentions"), ("system", "/notifications/system")):
        assert f"tab == '{tab}'" in PAGE and f'href="{href}"' in PAGE, tab
    assert "aria-current=\"page\"" in PAGE
    assert "comments.html" not in PAGE                                                        # rows are no longer full comments


def test_the_route_draws_the_feed_and_the_api_keeps_its_old_list():
    body = route("notifications")
    assert "notifications_page(v, notification_feed.tab_of(request.path, request.args.get(\"all\")), page)" in body
    assert "v.notification_commentlisting(" in body and '"data": [x.json for x in api_listing()]' in body
    assert "c._is_blocked = False" not in body
    posts = route("notifications_posts")
    assert 'notifications_page(v, "posts", page)' in posts and "x.json for x in posts" in posts and "for x in listing" not in posts
    page = route("notifications_page")
    assert "notification_feed.page(g.db, v, tab, number)" in page and 'render_template("notifications.html"' in page


def test_the_lists_and_the_page_share_one_set_of_filters():
    assert "def _notification_comment_query(self, tab):" in USER and "def _notification_post_query(self, all_=True):" in USER
    assert "self._notification_comment_query(" in FEED.replace("v._notification_comment_query(", "self._notification_comment_query(")
    assert "v._notification_post_query(" in FEED
    for builder in ("_notification_comment_query", "_notification_post_query"):
        body = USER.split(f"def {builder}(")[1].split("\n    def ")[0]
        assert "hide_blocked(hide_muted(" in body and ("filter_comments(" in body or "filter_posts(" in body), builder


def test_a_top_level_mention_is_not_lost_to_a_null_in_not_in():
    body = USER.split("def _notification_comment_query(")[1].split("\n    def ")[0]
    mentions = body[body.index('elif tab == "mentions":'):body.index('elif tab == "system":')]
    assert "Comment.parent_comment_id.is_(None)" in mentions and "Comment.author_id != 1" in mentions
    count = USER.split("def mentions_count(")[1].split("\n    @property")[0]
    assert "Comment.parent_comment_id.is_(None)" in count and "Comment.author_id != 1" in count


def test_the_feed_pages_by_rows_of_bundles_and_marks_only_what_it_shows_after_noting_what_was_new():
    page = FEED[FEED.index("def page("):]
    assert "PER_PAGE * number" in page and "shown = everything[need - PER_PAGE:need]" in page and "next_exists=len(everything) > need" in page
    assert page.index("grp.menu = ng.controls(grp, state)") < page.index(".update({\"read\": True}")      # the tint is decided first
    assert "if not i.read" in page and "get_comments(fresh_c, v=v, load_parent=True)" in page and "post_follows.attach(" in page
    assert "while rows_read < CAP:" in page


ACTIONS = sorted(set(re.findall(r'Control\("([a-z-]+)"', GROUPS)))


def test_every_action_the_rules_can_offer_has_a_label_an_icon_and_a_handler():
    assert ACTIONS == ["guild-bell", "guild-member", "guild-mute", "post-follow", "user-bell", "user-follow", "user-mute"]
    icons = dict(re.findall(r'"([a-z-]+)": "(fa-[a-z-]+)"', ROW[ROW.index("ACTION_ICONS"):ROW.index("KIND_ICONS")]))
    handlers = set(re.findall(r"'([a-z]+-[a-z]+)': [A-Z]+", JS[JS.index("var KINDS"):JS.index("function toast")]))
    for action in ACTIONS:
        assert action in icons and action in handlers, action
        assert f'c.action == "{action}"' in ROW, action
        assert all(icon.removeprefix("fa-") in NAMES for icon in icons.values())


def test_the_menu_script_only_sends_to_this_site_and_only_names_that_look_like_names():
    assert "var NAME = /^[A-Za-z0-9_]{1,25}$/;" in JS and "var POST = /^[A-Za-z0-9]{1,16}$/;" in JS
    assert "url.charAt(0) !== '/' || url.charAt(1) === '/'" in JS
    assert "body.append('formkey', formkey());" in JS and "innerHTML" not in JS
    assert "post_toast(" not in JS                                                              # it sends the field the mute routes read
    # the race-safe toast: no dispose while another is on its way out (Bootstrap 4.3 throws)
    assert ".toast('dispose')" not in JS
    for needed in ("data-url-on", "data-url-off", "data-field", "data-label-on", "data-label-off", "data-on"):
        assert needed in ROW and needed in JS, needed


def test_the_routes_the_controls_call_exist_with_the_parameters_the_script_sends():
    users, boards = read("routes", "users.py"), read("routes", "boards.py")
    follows, settings, muting = read("routes", "post_follows.py"), read("routes", "settings.py"), read("routes", "muting.py")
    assert '"/api/post/<pid>/follow"' in follows and '"/api/post/<pid>/unfollow"' in follows
    assert '"/api/follow/<username>"' in users and '"/api/unfollow/<username>"' in users and '@app.post("/@<username>/toggle_bell")' in users
    assert '"/api/subscribe/<guildname>"' in boards and '"/api/unsubscribe/<guildname>"' in boards and '@app.post("/+<guildname>/toggle_bell")' in boards
    assert '"/settings/mute"' in muting and '"/settings/unmute"' in muting
    assert '"/settings/mute_guild"' in settings and '"/settings/unmute_guild"' in settings
    assert 'request.values.get("board")' in settings                                            # the guild mutes read `board`
    assert 'field="username"' in GROUPS and 'field="board"' in GROUPS


def test_the_row_marks_everything_for_translation_and_names_anonymous_authors_through_the_rules():
    assert ROW.count("ngettext(") >= 6 and "{{ _(" in ROW                                  # plurals and sentences are marked, in every shape
    assert 'who(a)' in ROW and '{{ _("Anonymous") }}' in ROW
    assert "a.anonymous" in ROW and "notice_html(first.ref) | safe" in ROW
    assert not re.search(r"<p\b", ROW)                       # a notice brings its own <p>; the legacy `p a` rule underlines every link in one
    # (the site's word is "comment", never "reply": tests/test_terminology.py scans the templates, scripts and sources)


def test_the_stylesheet_is_one_partial_both_themes_import_and_uses_only_tokens():
    for sheet in ("main.scss", "main_dark.scss"):
        text = read("assets", "style", sheet)
        assert text.count('@import "{ruqquspath}/assets/style/_notifications";') == 1, sheet
        assert text.index("_screens") < text.index("_notifications") < text.index("_motion"), sheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", STYLE) and not re.search(r"\$primary|\$purple|\$info", STYLE)
    for token in ("--brand-soft", "--brand-fill", "--text-muted", "--border", "--hover", "--focus-ring"):
        assert token in STYLE, token
    assert "@media (max-width: 575.98px)" in STYLE and ".notif-open" in STYLE
    assert "animation" not in STYLE                          # nothing moves on load; the only motion is a hover colour on the tokens' durations
