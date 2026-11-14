"""Every place that lifts a limitation tells the person (the Log, 2026-11-14; the owner: "if the limitation is removed/lifted the user should be informed").

A tripwire on the source: a function that writes an un-action (an unexile, an unmute, an unhide, a restore, a lifted ban) must call one of `log_notices`'
`tell_*` functions, or be named here with the reason it doesn't. A new way to lift something that says nothing fails this."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIFTS = {"unexile_user", "unexile_alias", "unchatban_user", "unchatban_alias", "unhide_post_from_guild", "unhide_comment_from_guild", "unban_post",
         "unban_comment", "author_unhide_comment", "unban_guild", "unban_user"}
# function -> why it tells nobody here
OTHERWISE_TOLD = {
    "_undo": "an appeal that reverses a decision sends its own outcome notice, and not a second one",
    "unban_user": "its own 'your account has been reinstated' notice",
    "close_own_account": "the account is being deleted",
    "complete_deletion": "the account is being deleted",
    "_lift_after_close": "the account is being deleted",
}


def functions_with(kinds):
    """{(file, function): the lifts it writes} over the routes and helpers that write them."""
    found = {}
    for path in sorted((ROOT / "ruqqus" / "routes").glob("*.py")) + [ROOT / "ruqqus" / "classes" / "user.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            used = {k for k in kinds if re.search(rf"""["']{k}["']""", body) and re.search(rf"""(kind\s*=|record\w*\(\s*g\.db,|ModAction\()""", body)}
            if used:
                found[(path.name, node.name)] = used
    return found


def test_every_lifting_route_tells_the_person_or_says_why_not():
    silent = []
    for (file, name), kinds in functions_with(LIFTS).items():
        if name in OTHERWISE_TOLD:
            continue
        body = ""
        for path in (ROOT / "ruqqus" / "routes" / file, ROOT / "ruqqus" / "classes" / file):
            if path.exists():
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and node.name == name:
                        body = ast.get_source_segment(path.read_text(encoding="utf-8"), node)
        if "log_notices.tell_" not in body and "notify(" not in body:
            silent.append((file, name, sorted(kinds)))
    assert silent == [], f"these lift something and tell nobody: {silent}"


def test_the_exceptions_are_real_functions():
    for name in OTHERWISE_TOLD:
        hits = [p for p in (ROOT / "ruqqus").rglob("*.py") if re.search(rf"def {name}\(", p.read_text(encoding="utf-8"))]
        assert hits or name in ("complete_deletion", "_lift_after_close"), f"{name} is listed as an exception but doesn't exist"


def test_the_lapse_step_tells_the_accounts_whose_suspension_ended():
    source = (ROOT / "scripts" / "recomputes.py").read_text(encoding="utf-8")
    assert "lapse_due(db, int(time.time()), told=ended)" in source and "tell_lapsed(db_session, ended)" in source


def test_a_lift_notice_has_no_link_and_the_imposed_ones_carry_the_log_and_the_appeal():
    from ruqqus.helpers import log_notices as n

    for name in ("EXILE_LIFTED", "MUTE_LIFTED", "POST_SHOWN", "COMMENT_SHOWN", "POST_RESTORED", "COMMENT_RESTORED", "COMMENT_SHOWN_AGAIN",
                 "SUSPENSION_ENDED", "GUILD_REINSTATED"):
        assert "%(log)s" not in getattr(n, name) and "%(appeal)s" not in getattr(n, name), name
    for name in ("MUTED", "GUILD_BANNED"):
        text = getattr(n, name)
        assert "%(log)s" in text and "%(appeal)s" in text and "%(why)s" in text, name
    assert "%(log)s" in n.BAN_CITES and "%(items)s" in n.BAN_CITES


def test_no_lift_notice_can_name_an_alias():
    from ruqqus.helpers import log_notices as n

    for name in ("EXILE_LIFTED", "MUTE_LIFTED"):
        assert "Anonymous" not in getattr(n, name) and "alias" not in getattr(n, name).lower()
