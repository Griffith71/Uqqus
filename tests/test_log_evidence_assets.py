"""The ban reveal's wiring (the Log's slice D3d; the rules are tests/test_log_evidence.py, the real app tests/pg/test_log_d3d.py): where the evidence is checked and
proposed, who may decide it, what withdraws it, and which code may touch the table at all."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUQQUS = ROOT / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_ban_checks_the_list_before_it_changes_anything_and_proposes_it_after():
    route = read("ruqqus", "routes", "admin_api.py").split("def ban_user(user_id, v):")[1].split("\n@app.route")[0]
    checked = route.index("log_evidence.refusals(g.db, user.id, cited)")
    first_ban = route.index("user.ban(")
    proposed = route.index("log_evidence.propose(g.db, entry, user.id, v.id, cited")
    assert route.index("log_evidence.parse_items(") < checked < first_ban < proposed, "parse, refuse, ban, then propose"
    assert 'record_staff(g.db, "admin_evidence", v,' in route and "target_user_id=user.id" in route
    assert route.index("for x in user.alts:") > proposed, "an alt's ban cites nothing: its entry is staff-only"
    assert "evidence" not in route.split("for x in user.alts:")[1].split("notify(user, notice[0]")[0]


def test_only_a_different_admin_decides_and_every_decision_is_a_staff_entry():
    pages = read("ruqqus", "routes", "log_evidence_pages.py")
    decide = pages.split("def admin_log_evidence_decide(entry_id, v):")[1]
    assert "@admin_level_required(3)\n@validate_formkey\ndef admin_log_evidence_decide" in pages
    assert "log_evidence.decide(g.db, entry_id, v.id, v.admin_level, approve, now)" in decide
    assert 'record_staff(g.db, "admin_evidence", v,' in decide and "sole-admin approval" in decide
    assert "@admin_level_required(3)\ndef admin_evidence_items" in pages and "v.admin_level <= user.admin_level" in pages, "only below the admin's own level"
    rules = read("ruqqus", "helpers", "log_evidence.py")
    approval = rules.split("def approval(")[1].split("\ndef ")[0]
    assert "int(admin_id) != int(proposer_id)" in approval and "(admin_level or 0) >= 4 and not another_admin_exists" in approval
    assert "WHERE entry_id = :e AND status = 'pending'" in rules, "one conditional update: two admins deciding at once decide once"


def test_unbanning_drops_only_an_unapproved_proposal_in_the_one_place_every_unban_goes_through():
    unban = read("ruqqus", "classes", "user.py").split("    def unban(self")[1].split("    @property\n    def is_suspended")[0]
    assert "log_evidence.withdraw_pending(g.db, self.id, int(time.time()))" in unban and "begin_nested()" in unban
    assert unban.index("record_unban(") < unban.index("log_evidence.withdraw_pending(")
    assert "log_evidence.withdraw(" not in unban
    rules = read("ruqqus", "helpers", "log_evidence.py")
    withdraw = rules.split("def withdraw_pending(")[1].split("\n\n\n")[0]
    assert "status = 'pending'" in withdraw and "'approved'" not in withdraw, "an approved reveal is never taken back by a reversal"
    assert "def withdraw(" not in rules


def test_only_the_reveal_s_own_code_reads_or_writes_the_evidence():
    users = set()
    for path in list((RUQQUS / "routes").glob("*.py")) + list((RUQQUS / "helpers").glob("*.py")) + list((RUQQUS / "classes").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "log_evidence" in source:
            users.add(path.relative_to(RUQQUS).as_posix())
    assert users == {"helpers/log_evidence.py", "routes/log_evidence_pages.py", "routes/__init__.py", "routes/admin_api.py", "routes/log_pages.py", "routes/log_appeal_pages.py", "classes/user.py"}, users
    for anonymiser in ("helpers/anonymise.py", "helpers/account_anonymise.py", "helpers/account_cleanup.py", "helpers/deletion.py"):
        assert "log_evidence" not in read("ruqqus", *anonymiser.split("/")), anonymiser + " must never touch the evidence: citing is not undone by deleting or unlinking"


def test_the_log_draws_the_name_only_through_the_reveal_and_the_page_has_its_wording():
    rows = read("ruqqus", "routes", "log_pages.py").split("def _rows(entries, v):")[1].split("\ndef _deleted_words")[0]
    assert "named, cited, accounts = _reveals(entries)" in rows
    assert "item in named" in rows and "if e[\"id\"] in accounts:" in rows and 'rows[-1]["evidence"]' in rows
    template = read("ruqqus", "templates", "partials", "log_rows.html")
    assert "{% if e.evidence %}" in template and "/log\">" in template
    for page in ("userpage.html", "userpage_deleted.html", "userpage_blocked.html", "userpage_blocking.html", "userpage_banned.html"):
        text_ = read("ruqqus", "templates", page)
        assert '{% include "admin/ban_form.html" %}' in text_ and "/api/ban_user/" not in text_, page
    form = read("ruqqus", "templates", "admin", "ban_form.html")
    assert 'id="ban-evidence"' in form and 'data-url="/admin/evidence_items/{{ u.id }}"' in form and "a different admin approves" in form
    script = read("ruqqus", "assets", "js", "ban_evidence.js")
    assert "innerHTML" not in script, "drawn with textContent only"
    assert "the Log may name the account" in read("ruqqus", "templates", "partials", "post_options.html")
    for page in ("delete_post_modal.html", "delete_comment_modal.html"):
        assert "the Log may name the account for it" in read("ruqqus", "templates", page), page
    for page in ("privacy.html", "terms.html", "moderation.html"):
        assert "second administrator agrees" in read("ruqqus", "templates", "help", page) or "second admin agrees" in read("ruqqus", "templates", "help", page), page
