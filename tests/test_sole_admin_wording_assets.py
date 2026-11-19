"""Every sentence that tells a person a SECOND administrator must approve naming their anonymous items in the Log also says what happens on a site with a single
administrator (the Log plan's decision 27, finding C40 of the second review): the level-4 owner may confirm their own proposal (`log_evidence.approval`, recorded
as a sole-admin approval). The Terms, the privacy page and `/help/moderation` said so already; the appeal page and the ban notice did not.

Source-reading tests: nothing here imports the app or a database."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"

SECOND = re.compile(r"second admin(?:istrator)?\b", re.IGNORECASE)
SOLE = re.compile(r"(?:single|only one) administrator", re.IGNORECASE)
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
REACH = 220  # the clause comes straight after "approves", inside the same sentence


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_every_member_facing_second_administrator_line_also_names_the_sole_one():
    found = []
    missing = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative.startswith("admin/"):
            continue  # the admin pages are English and for the staff: the next test holds the two that matter
        text = JINJA_COMMENT.sub("", path.read_text(encoding="utf-8"))
        for match in SECOND.finditer(text):
            found.append(relative)
            if not SOLE.search(text[match.end():match.end() + REACH]):
                missing.append(f"{relative}: ...{text[max(0, match.start() - 30):match.end() + 60]!r}")
    assert {"log_appeal.html", "help/terms.html", "help/privacy.html", "help/moderation.html"} <= set(found), "the scan found its four known pages, or it is blind"
    assert not missing, "these tell a person a second administrator approves, and leave out a site with a single administrator:\n" + "\n".join(missing)


def test_the_ban_notice_names_the_sole_administrator_and_keeps_its_facts():
    from ruqqus.helpers import log_notices

    message = str(log_notices.BAN_CITES)
    after = message.split("second administrator approves", 1)
    assert len(after) == 2, "the notice still says a second administrator approves"
    assert SOLE.search(after[1][:REACH]), "and what happens when there is only one"
    # the facts it keeps: a count and the appeal link; never an id, the entry or a link to an item (it is read by whoever is signed in, a relayed QR code included)
    assert "%(num)s" in message and "%(appeal)s" in message
    for forbidden in ("%(item)s", "%(items)s", "%(log)s", "%(link)s", "%(id)s"):
        assert forbidden not in message, forbidden
    assert message not in log_notices.HELD_FROM_LIMITED, "it links no item and no entry, so a limited sign-in may read it (and it must stay that way)"


def test_the_ban_notice_builder_says_it_in_its_docstring_too():
    builder = read("ruqqus", "helpers", "log_notices.py").split("def tell_ban_cites(")[1].split("def tell_item_lifted(")[0]
    assert "single administrator" in builder.split('"""')[1], "the docstring is where the next reader learns the rule"


def test_the_admin_ban_form_and_evidence_page_say_the_owner_may_approve_their_own_when_alone():
    form = read("ruqqus", "templates", "admin", "ban_form.html")
    assert "a different admin approves" in form, "the existing pin (tests/test_log_evidence_assets.py)"
    assert "only one administrator" in form.split("a different admin approves", 1)[1][:REACH]
    page = read("ruqqus", "templates", "admin", "log_evidence.html")
    assert "sole-admin approval" in page and "the owner (level 4) when no other admin exists" in page
