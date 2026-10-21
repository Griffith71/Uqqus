"""The rules shown beside "Create a post" / "Create a guild" live in ONE partial
and must stay in step with the content policy page (help/rules.html)."""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "ruqqus" / "templates"
PARTIAL = TEMPLATES / "partials" / "posting_rules.html"
POLICY = TEMPLATES / "help" / "rules.html"

# The owner's rules (2026-10-20), as phrases that must appear in both places.
CORE = (
    "good-faith",
    "bad-faith",
    "documented evidence",
    "gambling schemes",
    "intellectual property",
    "deceptive impersonation",
    "erotic",
    "violent",
    "grotesque",
    "a broader work",
    "pirated copies",
    "consisting entirely of any one of",
    "personal information",
    "impersonation",
    "paid partnerships",
    "unsolicited commercial promotion",
    "commercial promotion",
    "created using ai",
    "identify themselves as bots",
    "not from",                  # anonymity hides the author from users, not from the site
    "ban",
    "hacking",
    "votes",
)
# rules the owner's text removed: they must not come back through one copy that was forgotten
REMOVED = ("duplicate posting", "misinformation", "anti-degen", "revenge pornography", "shortener", "unregulated",
           "sex scene", "complicit", "facilitate illegal", "entertainment", "purely to display")
BOTS = TEMPLATES / "help" / "bots.html"


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


def test_the_policy_and_the_card_both_have_six_sections():
    policy = POLICY.read_text(encoding="utf-8")
    assert re.findall(r"^## (\d+)\. ", policy, re.M) == ["1", "2", "3", "4", "5"]
    assert '<h2 id="bots">6. Bots</h2>' in policy
    card = PARTIAL.read_text(encoding="utf-8")
    assert len(re.findall(r"\{% call rule\(prefix, \d+,", card)) == 5   # the bot rules are on the full page only, not beside Create a post


def test_removed_rules_are_gone_from_both():
    card, policy = _text(PARTIAL), _text(POLICY)
    for phrase in REMOVED:
        assert phrase not in card and phrase not in policy, phrase


def test_the_site_is_named_from_settings_never_written_out():
    for path in (PARTIAL, POLICY):
        html = path.read_text(encoding="utf-8")
        assert "ruqqus.com" not in html.lower() and "laba" not in html.lower().replace("{{ 'site_name' | app_config }}", ""), path.name


def test_the_bot_pages_and_the_rules_point_at_each_other():
    assert "/help/rules#bots" in BOTS.read_text(encoding="utf-8")
    assert "/help/bots" in POLICY.read_text(encoding="utf-8")
    assert 'rule(prefix, 7, "Bots")' not in PARTIAL.read_text(encoding="utf-8")      # not on the post/guild card
    assert "x-user-type" not in _text(PARTIAL)
    assert "x-user-type" in _text(POLICY)
    assert "Settings > Bots" in POLICY.read_text(encoding="utf-8")


def test_the_pages_that_name_the_policy_call_it_the_rules_and_guidelines():
    for name in ("help.html", "docs_default.html"):
        assert "[Rules and Guidelines](/help/rules)" in (TEMPLATES / name).read_text(encoding="utf-8"), name
        assert "[Content Policy]" not in (TEMPLATES / name).read_text(encoding="utf-8"), name
    # the Terms still define "Content Policy": it is the same page
    assert 'Rules and Guidelines [here](/help/rules) (the "Content Policy")' in (TEMPLATES / "help" / "terms.html").read_text(encoding="utf-8")
