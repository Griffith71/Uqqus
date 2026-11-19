"""The history page of a deleted item says what is true (the second review of the Log, C78). `templates/content_history.html` is rendered for
real, without importing the app, for every state its notes depend on:

- the "not shown" note no longer blames the site's administrators for text the site cleared by itself 90 days after its author deleted it
  (`scripts/recomputes.py` sets `purged_utc` on every deleted post and comment), and is said once, on the first version it is about, not on
  whatever row happens to come first;
- a deleted item is never "nothing recorded" (a forwarded copy a delete reached has no row of its own: it gets a "Deleted" card), and it has no
  "(view current)", since its address leads to the Log's thread, which has nothing for an item that was erased;
- who may open the page stays the author's and the admins' (`visibility.history_open`), and the reader's own rules for the words stay as built.
The same promises on the real app are in tests/pg/test_log_history_note.py."""
import html
import re
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from ruqqus.helpers.i18n_jinja import environment

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
TEMPLATES = ROOT / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HISTORY = read("templates", "content_history.html")
VISIBILITY = read("helpers", "visibility.py")

CLEARED = "The earlier versions aren't shown: this was deleted and its text has been cleared."
ADMINS = "The text isn't shown: this was removed by the site's administrators."
HIDDEN = "The earlier versions aren't shown while this is hidden."
VERSION_HIDDEN = "The text of this version was hidden by a site administrator."
NOTHING = "No edits or removals recorded for this"


def env():
    # the child's title block holds a `%(site)s` that only the app's own `_` fills in: the stub parent has no such block, so it is never drawn
    loader = ChoiceLoader([DictLoader({"home.html": "{% block content %}{% endblock %}"}), FileSystemLoader(str(TEMPLATES))])
    e = environment(loader=loader, autoescape=True)
    e.install_null_translations(newstyle=True)
    e.globals["date_text"] = lambda timestamp, fmt=None, *rest: "01 January 2026"
    e.globals["words_shown"] = lambda severity, v: True
    e.globals["anon_hidden"] = lambda item, v: False
    e.globals["author_of"] = lambda item, v: NS(shown_name="Anonymous")
    return e


def target(kind="post", **over):
    row = dict(permalink=f"/@author/{kind}/abc", base36id="abc", deleted_utc=0, purged_utc=0, is_banned=False, hidden_by_guild=False,
               author_hidden=False, author_id=7, post=None)
    row.update(over)
    return NS(**row)


def entry(action="edit", **over):
    row = dict(id=1, action=action, created_utc=1, reason="", summary="", text_severity=0, hidden_utc=0, previous_title="OLD-TITLE",
               previous_url="", previous_body_html="<p>OLD-WORDS</p>", actor=NS(permalink="/@author", username="author"), actor_id=7,
               board=NS(permalink="/+guild", name="guild"))
    row.update(over)
    return NS(**row)


def page(item, entries=(), show_text=True, kind="post", v=NS(admin_level=0, formkey="k"), obliteration=None, copy_url=None):
    text = env().get_template("content_history.html").render(
        v=v, target=item, target_type=kind, entries=list(entries), obliteration=obliteration, copy_url=copy_url, show_text=show_text)
    return html.unescape(text)


# --- the "not shown" note ---------------------------------------------------------------------------------------------------------------

def test_an_item_its_author_deleted_and_the_site_cleared_is_not_said_to_be_removed_by_the_administrators():
    for kind in ("post", "comment"):
        text = page(target(kind, deleted_utc=100, purged_utc=200), [entry(), entry(id=2, action="delete")], show_text=False, kind=kind)
        assert text.count(CLEARED) == 1, kind
        assert "administrators" not in text, kind
        assert "OLD-WORDS" not in text and "OLD-TITLE" not in text, kind


def test_the_administrators_are_blamed_only_when_an_administrator_removed_it():
    # a removed item stays removed whether or not its author then deleted it, and a live item an admin purged is theirs
    for item in (target(deleted_utc=100, purged_utc=200, is_banned=True), target(deleted_utc=100, is_banned=True),
                 target(purged_utc=200), target(is_banned=True), target(purged_utc=200, hidden_by_guild=True)):
        text = page(item, [entry()], show_text=False)
        assert text.count(ADMINS) == 1 and CLEARED not in text and HIDDEN not in text, item


def test_a_guilds_or_an_authors_hide_has_its_own_sentence_until_the_text_is_cleared():
    for field in ("hidden_by_guild", "author_hidden"):
        text = page(target(**{field: True}), [entry()], show_text=False)
        assert text.count(HIDDEN) == 1 and ADMINS not in text and CLEARED not in text, field
    # hidden and then deleted and cleared: the clearing is what explains the missing text
    text = page(target(hidden_by_guild=True, deleted_utc=100, purged_utc=200), [entry()], show_text=False)
    assert text.count(CLEARED) == 1 and HIDDEN not in text and ADMINS not in text


def test_the_note_is_said_once_on_the_first_version_it_is_about_not_on_the_first_row():
    guild_hide = entry(id=1, action="guild_hide", previous_body_html="")
    first, second = entry(id=2, created_utc=2), entry(id=3, created_utc=3)
    text = page(target(deleted_utc=100, purged_utc=200), [guild_hide, first, second], show_text=False)
    assert text.count(CLEARED) == 1                      # the old `loop.first` drew nothing here: the first row was the guild's hide
    assert "Removed by guildmasters of" in text
    two = page(target(purged_utc=200), [first, second], show_text=False)
    assert two.count(ADMINS) == 1


def test_a_version_an_admin_hid_does_not_use_up_the_note():
    hidden = entry(id=1, hidden_utc=5, previous_body_html="")
    text = page(target(deleted_utc=100, purged_utc=200), [hidden, entry(id=2)], show_text=False)
    assert text.count(VERSION_HIDDEN) == 1 and text.count(CLEARED) == 1


def test_no_note_and_the_versions_are_drawn_when_the_reader_may_read_them():
    text = page(target(deleted_utc=100), [entry(), entry(id=2, action="delete")], show_text=True)
    assert "OLD-WORDS" in text and "OLD-TITLE" in text
    for sentence in (CLEARED, ADMINS, HIDDEN):
        assert sentence not in text


# --- a deleted item ----------------------------------------------------------------------------------------------------------------------

def test_a_deleted_item_with_no_row_of_its_own_is_shown_as_deleted_and_never_as_nothing_recorded():
    # a forwarded copy a delete reached, or one a post made for circles withdrew: `deleted_utc` and no history row
    for kind in ("post", "comment"):
        text = page(target(kind, deleted_utc=100), [], kind=kind)
        assert "<strong>Deleted</strong>" in text and "01 January 2026" in text, kind
        assert NOTHING not in text and "Deleted by" not in text, kind


def test_a_deleted_item_with_its_delete_row_has_no_second_deleted_card():
    text = page(target(deleted_utc=100), [entry(action="delete")])
    assert "Deleted by" in text and "<strong>Deleted</strong>" not in text


def test_a_live_item_keeps_the_empty_line_and_a_removal_does_not_count_as_a_delete():
    for kind in ("post", "comment"):
        text = page(target(kind), [], kind=kind)
        assert f"{NOTHING} {kind} yet." in text, kind
        assert "<strong>Deleted</strong>" not in text, kind
    # an obliterated item has its own card, and no empty line
    text = page(target(), [], obliteration=NS(created_utc=1, reason_category="legal_takedown", reason="r"))
    assert "Permanently removed by site administrators" in text and NOTHING not in text


def test_a_deleted_item_has_no_view_current_link_but_still_has_the_log():
    gone = page(target(deleted_utc=100), [entry(action="delete")])
    assert "(view current)" not in gone and "/post/abc/log" in gone and "/@author/post/abc" not in gone
    live = page(target(), [entry()])
    assert "(view current)" in live and "/@author/post/abc" in live and "/post/abc/log" in live
    comment = page(target("comment", deleted_utc=100), [entry(action="delete")], kind="comment")
    assert "(view current)" not in comment and "/comment/abc/log" in comment


def test_the_preserved_copy_link_is_drawn_only_when_the_route_gives_one():
    assert 'href="/post/abc/copy"' in page(target(deleted_utc=100), [entry(action="delete")], copy_url="/post/abc/copy")
    assert "/copy" not in page(target(deleted_utc=100), [entry(action="delete")])


# --- the source ---------------------------------------------------------------------------------------------------------------------------

def test_the_note_is_marked_once_and_does_not_read_the_first_row_any_more():
    assert HISTORY.count(CLEARED) == 1 and HISTORY.count(ADMINS) == 1 and HISTORY.count(HIDDEN) == 1
    assert "loop.first" not in HISTORY
    assert "{% elif target.deleted_utc and target.purged_utc and not target.is_banned %}" in HISTORY
    # the guild's sentence keeps the condition tests/test_log_public_assets.py pins, and "Deleted" is a msgid the site already has
    assert "(target.hidden_by_guild or target.author_hidden) and not target.is_banned and not target.purged_utc" in HISTORY
    assert "{{ _('Deleted') }}" in read("templates", "history.html")


def test_the_order_the_page_draws_versions_in_is_kept():
    edit_part = '{% if entry.action == "edit" %}\n    {% if entry.summary and words_shown(entry.text_severity, v) %}'
    before, after = HISTORY.split(edit_part)
    assert "previous_body_html" in after and "previous_body_html" not in before
    assert "{% elif not show_text %}" in HISTORY and "words_shown(entry.text_severity, v)" in HISTORY


def test_the_visibility_rules_say_what_is_built_and_no_longer_promise_a_change():
    assert "Slice D3a replaces this" not in VISIBILITY
    assert "def history_open(item, v):" in VISIBILITY and "v.admin_level >= 3 or anonymity.is_author(item, v)" in VISIBILITY
    docstring = VISIBILITY.split("def history_text_shown(item, v):")[1].split('"""')[1]
    assert "automatic clearing" in docstring and "recomputes.py" in docstring
    assert "hidden_by_guild" in docstring or "hidden_by_guild" in VISIBILITY.split("def history_text_shown(item, v):")[1]
    assert re.search(r"purged_utc or 0\) > 0:\s+return False", VISIBILITY.split("def history_text_shown(item, v):")[1])
