"""The wiring of the Log's thread of a deleted post: a placeholder for a comment by an account the reader muted or blocked, Flag on a live comment and the
Log's own Report under a comment its author deleted (C31 and C75 of the second review of the Log). The behaviour is run on the real app in
tests/pg/test_log_thread_controls.py and the block query on SQLite in tests/test_log_thread.py; this reads the source the way the project's other asset
tests do, and holds the promises that a render could not tell (no query for each comment, no new message to translate)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


LOG_PAGES = read("ruqqus", "routes", "log_pages.py")
THREAD_ROUTE = LOG_PAGES.split("def log_thread(")[1].split("\n@app.route")[0]
THREAD_PAGE = read("ruqqus", "templates", "log_thread.html")
ROWS = read("ruqqus", "templates", "partials", "log_rows.html")
FORM = read("ruqqus", "templates", "partials", "log_report_form.html")
COMMENTS = read("ruqqus", "templates", "comments.html")
FLAGGING = read("ruqqus", "routes", "flagging.py")
REPORT_PAGES = read("ruqqus", "routes", "log_report_pages.py")
HELPER = read("ruqqus", "helpers", "log_thread.py")


def route_head(source, name):
    """The decorators of a route function, from the route line to its `def`."""
    return source.split(f"def {name}(")[0].rsplit("@app.route", 1)[1]


# --- the placeholders ---------------------------------------------------------------------------------------------------------------

def test_a_block_is_asked_once_for_the_page_not_for_each_comment():
    assert "any_block_exists" not in THREAD_ROUTE, "one userblocks query for each live comment, on a page of hundreds"
    assert "log_thread_rules.blocked_among(" in THREAD_ROUTE and "import log_thread as log_thread_rules" in LOG_PAGES
    assert "c.author_id in blocked_ids" in THREAD_ROUTE
    asked = THREAD_ROUTE.split("log_thread_rules.blocked_among(")[1].split("\n")[0]
    assert "not c.is_anonymous" in asked, "an anonymous comment's author is never asked about"
    assert "f\"" not in HELPER and "f'" not in HELPER and ".format(" not in HELPER, "no SQL built from text"
    assert "bindparam(\"ids\", expanding=True)" in HELPER


def test_a_muted_comment_is_a_placeholder_by_the_one_rule_that_never_hides_an_anonymous_one():
    assert "muting.is_muted(c, v)" in THREAD_ROUTE
    assert "def is_muted" in read("ruqqus", "helpers", "muting.py")
    assert "getattr(item, \"is_anonymous\", False)" in read("ruqqus", "helpers", "muting.py").split("def is_muted(")[1].split("def forget")[0]
    assert 'out["kind"], out["name"] = "muted"' in THREAD_ROUTE


def test_the_order_is_removed_hidden_deleted_filtered_blocked_muted_and_then_the_comment():
    """A deleted comment is drawn as Anonymous before a block or a mute is looked at, so neither can single one out."""
    order = ["c.is_banned or", "c.hidden_by_guild or c.author_hidden", "(c.deleted_utc or 0) > 0:\n            entry", "comment_hidden(c, v)",
             "c.author_id in blocked_ids", "muting.is_muted(c, v)", "out[\"flag\"]"]
    places = [THREAD_ROUTE.index(part) for part in order]
    assert places == sorted(places), places
    blocked = THREAD_ROUTE.split("c.author_id in blocked_ids")[0].rsplit("\n", 1)[1]
    assert "v is not None and not c.is_anonymous" in blocked


def test_the_pinned_gates_of_the_thread_are_still_in_front():
    thread = THREAD_ROUTE.split("def draw(")[0]
    assert thread.index("post.in_private_guild_for(v)") < thread.index("return redirect(post.permalink)")
    assert "(c.purged_utc and not (c.deleted_utc or 0) > 0)" in THREAD_ROUTE and 'scrubbed_copy.get("scrubbed")' in THREAD_ROUTE


# --- Flag and Report ----------------------------------------------------------------------------------------------------------------

def test_flag_is_the_site_s_own_report_of_a_comment_and_only_for_a_signed_in_reader_who_is_not_its_author():
    assert 'out["flag"] = v is not None and not anonymity.is_author(c, v)' in THREAD_ROUTE
    assert "{% if c.flag %}" in THREAD_PAGE and 'data-target="#reportCommentModal"' in THREAD_PAGE
    assert "report_commentModal('{{ c.id }}', {{ c.name | tojson | forceescape }})" in THREAD_PAGE
    assert '{% include "flag_comment_modal.html" %}' in read("ruqqus", "templates", "default.html"), "the modal every signed-in page carries"
    # the name in the modal's title is plain text, and an anonymous comment's is never its author's
    name = THREAD_ROUTE.split('out["name"] = (')[1].split("\n        return out")[0]
    assert "str(author) if hidden_author" in name and 'tr("[deleted account]")' in name and 'tr("A member")' in name


def test_the_thread_has_no_vote_comment_tip_share_or_ad_control_and_draws_nothing_from_deleted_words_as_a_link():
    for forbidden in ("data-id-up", "data-id-down", "comment-form", "tipModal", "sharePost", "ad_slot", "adsbygoogle", "upvote-button", "| safe",
                      "{{ c.words.url", "href=\"{{ c.words", "href=\"{{ c.line"):
        assert forbidden not in THREAD_PAGE, forbidden
    assert "{{ c.words.body }}" in THREAD_PAGE, "the sanitised words, with every link taken out (log_view.without_links), are drawn as they are"
    assert "_deleted_words(copy" in THREAD_ROUTE


def test_the_logs_own_report_is_drawn_under_a_deleted_comment_only_where_its_words_are_shown():
    assert 'out["entry_id"] = entry if shown else None' in THREAD_ROUTE
    assert "{% if c.entry_id and v %}{{ report_form(c.entry_id, v) }}{% endif %}" in THREAD_PAGE
    assert 'action="/log/{{ entry_id }}/report"' in FORM and 'name="formkey" value="{{ v.formkey }}"' in FORM
    # one copy of the form for the whole Log: the rows partial uses the macro too
    assert 'import report_form' in ROWS and "{% if e.can_report and v %}{{ report_form(e.id, v) }}{% endif %}" in ROWS
    assert "/report" not in ROWS and "Send report" not in ROWS
    assert 'import report_form' in THREAD_PAGE


def test_reporting_stays_open_to_a_child_account_and_is_held_for_a_sign_up_being_held():
    flag = route_head(FLAGGING, "api_flag_comment")
    assert '@not_quarantined("report")' in flag and "@validate_formkey" in flag and "@is_not_banned" in flag
    assert "not_child_locked" not in flag, "a child account only reads, and may still report"
    report = route_head(REPORT_PAGES, "log_report")
    assert '@not_quarantined("report")' in report and "@validate_formkey" in report and "@auth_required" in report
    assert "not_child_locked" not in report
    assert "ON CONFLICT (entry_id, reporter_id) DO NOTHING" in read("ruqqus", "helpers", "log_reports.py"), "once per person per entry"


# --- nothing new to translate -------------------------------------------------------------------------------------------------------

def test_every_message_the_thread_reuses_is_already_marked_elsewhere():
    """A new msgid lowers every language's share (`READY_SHARE`): the thread says only what comments.html and the Log's rows already say."""
    elsewhere = COMMENTS + read("ruqqus", "helpers", "log_view.py") + ROWS + FORM
    for message in ('_("You muted @%(name)s"', '_("Unmute")', 'pgettext("verb", "Flag")', "_('Report this')", "_('Send report')",
                    "_('Why should it come down?')", "_('[deleted account]')", 'tr("A member")'):
        assert message in elsewhere, message
    for message in ('_("You muted @%(name)s"', '_("Unmute")', 'pgettext("verb", "Flag")'):
        assert message in THREAD_PAGE, message
    assert "_('Report this')" in FORM and "_('Send report')" in FORM
