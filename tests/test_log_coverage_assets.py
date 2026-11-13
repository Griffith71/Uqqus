"""The paper trail has no gaps the audit found (the Log, slice D2): tripwires on the source, so a new decision can't be made without the Log
hearing of it.

* **No account is banned except through `User.ban`** (S1): the image checks used to set `is_banned` on an uploader and their alts by hand, so
  the Log never heard of it and none of a permanent ban's other duties ran. A new place that bans an account calls `User.ban`.
* **Every admin route that changes something writes an entry or says here why not** (H5): the sweep of 2026-11-05 found 53 admin routes that
  wrote nothing; each now writes a public or a staff-only entry, and the few that are only triage are named below with their reason.
* **Every automatic removal is in the Log**, by generic words that name no category (the owner's decision 1)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"

# an assignment to `.is_banned` on something that is not an account: a post, a comment, a guild, an API app, the SQL of a listing
NOT_AN_ACCOUNT = {"post", "comment", "banned", "new_post", "submission", "item", "c", "p", "board", "app", "s"}
# the one place an account is banned by hand, on purpose: the CSAM route of the level-4 admins (a law-enforcement flow that must not cascade
# into deleting a parent's children, CLAUDE.md "Parent and child accounts"). It writes its own entries (`record_ban(..., floor=LEGAL)`).
HAND_BANS = {("ruqqus/routes/admin_api.py", "admin_csam_nuke"): {"user", "alt"}}


def read(path):
    return Path(path).read_text(encoding="utf-8")


def _function_at(path, number):
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.FunctionDef) and node.lineno <= number <= node.end_lineno:
            inner = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) and n.lineno <= number <= n.end_lineno]
            if node is min(inner, key=lambda n: n.end_lineno - n.lineno):
                return node.name


def test_no_code_bans_an_account_except_user_ban():
    found = []
    for path in sorted(RUQQUS.rglob("*.py")):
        source = read(path)
        for number, line in enumerate(source.splitlines(), 1):
            match = re.match(r"\s*(\w+)\.is_banned\s*=\s*(?!=)", line)
            if match and match.group(1) not in NOT_AN_ACCOUNT:
                found.append((str(path.relative_to(ROOT)), number, match.group(1)))
    # the writes inside User.ban and User.unban, and the CSAM route's, are the only ones
    user_py = "ruqqus/classes/user.py"
    allowed_by_hand = {(f, who) for (f, _fn), whos in HAND_BANS.items() for who in whos}
    assert {(f, who) for f, _, who in found} == {(user_py, "self")} | allowed_by_hand, found
    for filename, number, who in found:
        if filename == user_py:
            tree = ast.parse(read(ROOT / user_py))
            methods = {n.name: (n.lineno, n.end_lineno) for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "User"
                       for n in cls.body if isinstance(n, ast.FunctionDef)}
            assert any(lo <= number <= hi for name, (lo, hi) in methods.items() if name in ("ban", "unban")), number
        else:
            assert (filename, _function_at(ROOT / filename, number)) in HAND_BANS, (filename, number)


def test_the_image_checks_ban_through_the_one_routine():
    for name in ("helpers/aws.py", "helpers/media/safety.py"):
        source = read(RUQQUS / name)
        assert "is_banned" not in re.sub(r"(post|comment|banned)\.is_banned", "", source), name
    assert "scan_bans.ban_uploader" in read(RUQQUS / "helpers" / "aws.py")
    scans = read(RUQQUS / "helpers" / "scan_bans.py")
    assert "user.ban(reason=action_log.AUTOMATIC_IMAGE_REASON" in scans and "floor=action_log.STAFF" in scans     # alts: staff-only entries
    assert "record_detail" in scans and scans.count("cascade=False)") == 2                  # no sub-account is closed by a scan: law enforcement


# admin routes that change nothing a person could be wronged by, or that keep a record of their own
NO_ENTRY = {
    "admin_user_reports_dismiss": "closing a report is triage: it decides nothing about anyone",
    "admin_note_dismiss": "closing note requests is triage; a written note is logged",
    "admin_space_report_dismiss": "closing a report is triage",
    "admin_space_message_dismiss": "closing a report is triage",
    "admin_story_dismiss": "closing a report is triage",
    "admin_feedback_status": "feedback has its own record (site_feedback)",
    "admin_creator_fund_pay": "the creator fund keeps its own ledger of what was paid and why (creator_fund_payouts)",
    "admin_creator_fund_release": "the creator fund keeps its own ledger",
    "admin_happening_create": "the What's happening box has its own change log (site_event_log)",
    "admin_happening_edit": "the What's happening box has its own change log (site_event_log)",
    "admin_happening_state": "the What's happening box has its own change log (site_event_log)",
    "admin_happening_hide": "the What's happening box has its own change log (site_event_log)",
    "admin_happening_unhide": "the What's happening box has its own change log (site_event_log)",
    "admin_happening_seed": "the What's happening box has its own change log (site_event_log)",
    "api_distinguish_post": "an admin's mark on a post is drawn on the post itself, and is not a decision about anyone",
    "api_sticky_post": "a site-wide pin is drawn on the post itself, and is not a decision about anyone",
    "admin_distinguish_comment": "an admin's mark on a comment is drawn on the comment itself",
    "admin_dump_cache": "empties a cache: changes nothing a member can see",
    "admin_sig_generate": "makes a signature for the admin's own use: changes nothing",
}
LOGGED = ("action_log.", "ModAction(", ".ban(", ".unban(", "_decided(", "_obliterated(", "_ban_addresses(", "_act(", "_settle(")


def _admin_routes():
    for path in sorted((RUQQUS / "routes").glob("*.py")):
        source = read(path)
        for node in ast.parse(source).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            decorators = [ast.get_source_segment(source, d) or "" for d in node.decorator_list]
            routes = [d for d in decorators if d.startswith("app.")]
            text = "\n".join(decorators)
            addresses = re.findall(r'"(/[^"]+)"', "\n".join(routes))
            writes = any(d.startswith(("app.post", "app.delete", "app.patch")) or re.search(r"methods=\[[^\]]*\"(POST|post)\"", d) for d in routes)
            if writes and addresses and ("admin_level_required" in text or any(a.startswith("/admin") for a in addresses)):
                yield path.name, node.name, addresses[0], ast.get_source_segment(source, node)


def test_every_admin_route_that_changes_something_writes_an_entry_or_says_here_why_not():
    unlogged = {}
    count = 0
    for module, name, address, body in _admin_routes():
        count += 1
        if not any(mark in body for mark in LOGGED):
            unlogged[name] = (module, address)
    assert count >= 70, count                                   # the scan itself found the routes
    assert set(unlogged) == set(NO_ENTRY), (set(unlogged) ^ set(NO_ENTRY), unlogged)


def test_every_staff_action_the_routes_write_is_one_the_log_knows():
    from ruqqus.helpers import action_log
    written = set()
    for path in (RUQQUS / "routes").glob("*.py"):
        written |= set(re.findall(r'record_staff\(\s*(?:g\.db|db),\s*"(\w+)"', read(path)))
    for path in (RUQQUS / "helpers").glob("*.py"):
        written |= set(re.findall(r'record_staff\(\s*(?:g\.db|db),\s*"(\w+)"', read(path)))
    assert written and written <= action_log.RUNNING_ACTIONS, written - action_log.RUNNING_ACTIONS


def test_the_automatic_removals_are_in_the_log_in_generic_words():
    posts = read(RUQQUS / "routes" / "posts.py")
    assert posts.count("record_image_check_removal(") == 3                         # edit, new post, picture in the text
    comments = read(RUQQUS / "routes" / "comments.py")
    assert comments.count("record_image_check_removal(") == 1
    assert 'kind="ban_comment",\n                board_id=comment.post.board_id,\n                note="spam"' in comments   # the comment-edit spam guard
    for source in (posts, comments):
        assert 'note="banned image"' not in source                                  # the raw wording is gone from every producer
    log = read(RUQQUS / "helpers" / "action_log.py")
    assert '"spam": ("evasion", "Automatic spam check")' in log
    assert 'AUTOMATIC_IMAGE_REASON = "Automatic image check"' in log


def test_account_deletion_writes_what_it_ends():
    settings = read(RUQQUS / "routes" / "settings.py")
    for action in ("remove_mod", "unexile_user", "uncontrib_user"):
        assert f'record_automatic(g.db, "{action}"' in settings, action


def test_the_background_steps_keep_the_paper_trail_whole():
    steps = read(ROOT / "scripts" / "recomputes.py")
    assert "action_log.lapse_due(" in steps and "action_log.reconcile(" in steps
    retention = read(RUQQUS / "helpers" / "retention.py")
    assert "DELETE FROM log_failures" in retention and "UPDATE log_appeals SET reason = ''" in retention
    assert "mirror_from_table" in read(RUQQUS / "helpers" / "action_log.py")
