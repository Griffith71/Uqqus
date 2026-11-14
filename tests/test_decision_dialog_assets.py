"""The decision dialog and the takedown entry points (the owner, 2026-11-14: an exile, a chat mute and every admin takedown must say why): the wiring in the
templates and scripts, without a browser. The routes' own refusals are tested in tests/pg/test_log_evidence.py and tests/test_log_d4.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_dialog_is_in_the_page_shell_for_members_and_its_ids_are_the_scripts():
    shell = read("ruqqus", "templates", "default.html")
    assert 'partials/decision_dialog.html' in shell and "js/decision_dialog.js" in shell
    assert shell.count("decision_dialog") >= 2 and "request.args.get('embed')" in shell.split("decision_dialog.html")[0].splitlines()[-1], "never in a side panel's frame"
    partial, script = read("ruqqus", "templates", "partials", "decision_dialog.html"), read("ruqqus", "assets", "js", "decision_dialog.js")
    for ident in re.findall(r'\$\("(decision\w+)"\)', script) + ["decisionModal", "decisionForm"]:
        assert f'id="{ident}"' in partial, ident
    assert "required" in partial.split('id="decisionReason"')[1].split(">")[0], "the reason box is required"
    assert "LOG_RULES" in partial, "the rule menu is the Log's own"


def test_the_script_sends_the_reason_the_rule_and_each_cited_item_and_never_draws_html():
    script = read("ruqqus", "assets", "js", "decision_dialog.js")
    for needle in ('data.append("reason", reason)', 'data.append("rule"', 'data.append("evidence", id)', 'data.append("formkey", formkey())', "A reason is required."):
        assert needle in script, needle
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script, "an item's words are a stranger's: textContent only"
    assert "Choose at most five." in script and "MAX = 5" in script and "sanction_evidence" not in script
    assert "if (!ticked.length)" in script, "an exile by name must cite at least one item (the route refuses it too)"
    assert 'if (!(current.evidence && k === "thing"))' in script and 'ticked.indexOf(fields.thing) >= 0' in script, "an item the guildmaster unticked is not cited"


def test_every_exile_and_mute_menu_item_opens_the_dialog_through_the_macros():
    calls = read("ruqqus", "templates", "partials", "decision_calls.html")
    assert "/mod/exile/" in calls and "/mod/exile_alias/" in calls and "/mod/chatban_alias/" in calls and "/mod/exile_items/" in calls
    for name, count in (("comments.html", 2), ("submission.html", 2)):
        page = read("ruqqus", "templates", name)
        assert "decision_calls.html" in page
        for call in ("exile_call(", "alias_exile_call(", "alias_mute_call("):
            assert page.count("{{ " + call) == count, (name, call)
        assert "post_toast('/mod/exile" not in page and "post_toast('/mod/chatban" not in page
    script = read("ruqqus", "assets", "js", "all_js.js")
    body = script.split("function exile_from_guild(boardname) {")[1].split("\n}\n")[0]
    assert "decisionDialog(" in body and "ask_username: true" in body and "XMLHttpRequest" not in body, "the typed-name exile asks why too"
    assert "exile_from_guild(" in read("ruqqus", "templates", "guild", "bans.html") and "guildmasterExileModal" not in read("ruqqus", "templates", "guild", "bans.html").split("{% block exileModal %}")[0].split("data-target")[-1]


def test_the_admin_takedown_entry_points_ask_for_a_reason():
    script = read("ruqqus", "assets", "js", "all_js.js")
    remove = script.split("function removePost(post_id) {")[1].split("\n}\n")[0]
    assert "window.prompt(" in remove and "{reason: reason.trim()}" in remove, "the one-tap Remove sent no reason and was refused every time"
    purge = script.split("function purgeComment(cid) {")[1].split("\n}\n")[0]
    assert "window.prompt(" in purge and "/api/purge_comment/" in purge and "{reason: reason.trim()}" in purge
    comments = read("ruqqus", "templates", "comments.html")
    assert comments.count("purgeComment('{{ c.base36id }}')") == 2 and "post_toast('/api/purge_comment" not in comments
    pages = read("ruqqus", "assets", "js", "guild_pages.js")
    assert "data-ask-reason" in pages and "body.set('reason', reason.trim())" in pages
    assert "data-ask-reason=" in read("ruqqus", "templates", "guild", "wiki_history.html")
    for name, action in (("submission.html", "/api/purge_post/"), ("submission_deleted.html", "/api/purge_post/"),
                         ("userpage.html", "/admin/display_name/clear"), ("admin/broadcast_reports.html", "/remove"), ("admin/story_reports.html", "/remove"),
                         ("admin/chat_reports.html", "/remove"), ("admin/space_reports.html", "/end"), ("admin/space_reports.html", "/space_messages/{{ m.id }}/remove")):
        page = read("ruqqus", "templates", *name.split("/"))
        forms = [f for f in re.findall(r"<form[^>]*action=\"[^\"]*" + re.escape(action) + r"[^\"]*\".*?</form>", page, flags=re.S)]
        assert forms, (name, action)
        assert all('name="reason"' in f and "required" in f for f in forms), (name, action)


def test_the_picker_route_is_registered_limited_and_for_guildmasters_only():
    routes = read("ruqqus", "routes", "__init__.py")
    assert "from .sanction_items_pages import *" in routes
    source = read("ruqqus", "routes", "sanction_items_pages.py")
    assert '@limiter.limit("30/minute", key_func=member_limit_key)\n@auth_required\n@is_guildmaster("access")\ndef mod_exile_items(' in source
    assert "is_anonymous == False" in source and "deleted_utc == 0" in source and "visibility.post_hidden(" in source and "visibility.comment_hidden(" in source
    assert "private, no-store" in source


def test_the_dialog_has_its_style_in_the_shared_partial_with_tokens_only():
    scss = read("ruqqus", "assets", "style", "_components.scss")
    block = scss.split(".decision-items {")[1].split("}")[0]
    assert "var(--border)" in block and "var(--radius-sm)" in block and "var(--space-3)" in block and "#" not in block


def _source(name):
    """A template without its Jinja comments (which name the partial and the script in passing)."""
    return re.sub(r"{#.*?#}", "", read("ruqqus", "templates", name), flags=re.S)


def _extends(name):
    match = re.search(r'{%\s*extends\s+"([^"]+)"', _source(name))
    return match.group(1) if match else None


def _draws_the_dialog(name, seen=()):
    """Whether this template or a shell it extends draws the decision dialog and its script."""
    page = _source(name)
    if "partials/decision_dialog.html" in page and "js/decision_dialog.js" in page:
        return True
    parent = _extends(name)
    if parent and parent not in seen:
        # a child that adds only one half (the block that holds the dialog, or the scripts) still needs the other from its shell
        if "partials/decision_dialog.html" in page or "js/decision_dialog.js" in page:
            half = "partials/decision_dialog.html" if "partials/decision_dialog.html" in page else "js/decision_dialog.js"
            other = "js/decision_dialog.js" if half.startswith("partials") else "partials/decision_dialog.html"
            return _shell_has(parent, other, seen + (name,))
        return _draws_the_dialog(parent, seen + (name,))
    return False


def _shell_has(name, needle, seen=()):
    if needle in _source(name):
        return True
    parent = _extends(name)
    return bool(parent and parent not in seen and _shell_has(parent, needle, seen + (name,)))


def test_every_page_that_can_open_the_dialog_draws_it():
    """A page with an exile or mute control must reach the dialog and its script through its shell: the guild's Exiled page extends a standalone shell
    (guild_settings.html, not default.html), and its "Exile user" button threw a ReferenceError when only default.html drew the dialog."""
    openers = ("exile_from_guild(", "decisionDialog(", "exile_call(", "alias_exile_call(", "alias_mute_call(")
    # a fragment (no `extends`) is drawn inside a page that does, and its macros file and the dialog itself are not pages
    pages = [p for p in (ROOT / "ruqqus" / "templates").rglob("*.html")
             if any(o in p.read_text(encoding="utf-8") for o in openers) and _extends(p.relative_to(ROOT / "ruqqus" / "templates").as_posix())]
    assert pages, "no page opens the dialog?"
    undrawn = [p.relative_to(ROOT / "ruqqus" / "templates").as_posix() for p in pages
               if not _draws_the_dialog(p.relative_to(ROOT / "ruqqus" / "templates").as_posix())]
    assert undrawn == [], f"these open the dialog but their shell never draws it: {undrawn}"
