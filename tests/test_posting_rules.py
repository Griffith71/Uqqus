"""The rules shown beside "Create a post" / "Create a guild" live in ONE partial
and must stay in step with the content policy page (help/rules.html)."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "ruqqus" / "templates"
PARTIAL = TEMPLATES / "partials" / "posting_rules.html"
POLICY = TEMPLATES / "help" / "rules.html"

# The owner's rules, as phrases that must appear in both places.
CORE = (
    "bad-faith",
    "undisclosed, harmful motives",
    "gambling schemes",
    "misinformation",
    "unsafe or unregulated products or services",
    "purely erotic",
    "purely violent",
    "purely grotesque",
    "duplicate posting",
)


def _text(path):
    # drop tags and collapse whitespace so line breaks / markup do not matter
    html = path.read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


def test_core_rules_are_in_the_card_and_the_policy():
    card, policy = _text(PARTIAL), _text(POLICY)
    for phrase in CORE:
        assert phrase in card, f"rules card is missing: {phrase}"
        assert phrase in policy, f"content policy is missing: {phrase}"


def test_card_has_a_numbered_rule_for_each_heading():
    html = PARTIAL.read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"\{% call rule\(prefix, (\d+),", html)]
    assert numbers == list(range(1, len(numbers) + 1)), "rule numbers must run 1..n without gaps (they build the collapse ids)"
    assert len(numbers) >= 4


def test_pages_use_the_shared_partial_not_their_own_copy():
    for name in ("submit.html", "make_board.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert 'import "partials/posting_rules.html" as rules' in html, name
        assert "rules.rules_card(" in html and "rules.rules_panel(" in html, name
        assert "sidebar-rules" not in html, f"{name} has its own copy of the rules markup"


def test_rules_are_reachable_on_small_screens():
    # the desktop card sits in a d-none d-lg-block sidebar; small screens get the panel
    assert "d-lg-none" in PARTIAL.read_text(encoding="utf-8")
