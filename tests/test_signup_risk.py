"""The signup guard's rules (ruqqus/helpers/signup_risk.py): what refuses a sign-up, what adds to its score, when the
score or a surge quarantines it, and the name and address helpers."""
import ast
from pathlib import Path

import pytest

from ruqqus.helpers import signup_risk as sr
from ruqqus.helpers.signup_risk import Facts

PERSON = dict(captcha="ok", form_age=45, user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0",
              accept_language="en-GB,en;q=0.9", username="sarah_reads")


def facts(**changes):
    return Facts(**{**PERSON, **changes})


def test_an_ordinary_sign_up_is_allowed_with_nothing_against_it():
    verdict = sr.assess(facts())
    assert verdict == sr.Verdict(sr.ALLOW, 0, ())


def test_no_captcha_provider_is_not_held_against_anyone():
    assert sr.assess(facts(captcha="off")).action == sr.ALLOW


@pytest.mark.parametrize("changes, reason", [
    ({"captcha": "missing"}, "captcha_missing"),
    ({"captcha": "failed"}, "captcha_failed"),
    ({"honeypot": "https://spam.example"}, "honeypot"),
    ({"form_age": sr.FORM_MAX_AGE + 1}, "stale_form"),
    ({"disposable_email": True}, "disposable_email"),
    ({"user_agent": "python-requests/2.32.3"}, "automation_agent"),
    ({"user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HeadlessChrome/130.0 Safari/537.36"},
     "automation_agent"),
    ({"user_agent": "curl/8.7.1"}, "automation_agent"),
])
def test_a_hard_sign_refuses_whatever_else_is_true(changes, reason):
    verdict = sr.assess(facts(**changes))
    assert verdict.action == sr.REFUSE and verdict.reasons == (reason,)


def test_a_hidden_field_holding_only_spaces_is_empty():
    assert sr.assess(facts(honeypot="   ")).action == sr.ALLOW


def test_a_form_two_hours_old_is_still_fine():
    assert sr.assess(facts(form_age=sr.FORM_MAX_AGE)).action == sr.ALLOW


@pytest.mark.parametrize("changes, reason", [
    ({"form_age": 7}, "fast_form"),
    ({"accept_language": ""}, "no_language"),
    ({"tor": True}, "tor"),
    ({"prefix_recent": sr.PREFIX_BURST}, "prefix_burst"),
    ({"username": "xkcdqwrtz"}, "machine_name"),
    ({"username": "anna12345", "shape_recent": sr.SHAPE_BURST}, "shape_burst"),
    ({"ban_evasion": True}, "ban_evasion"),
    ({"captcha": "unavailable"}, "captcha_unavailable"),
])
def test_each_soft_sign_is_named_and_weighed(changes, reason):
    verdict = sr.assess(facts(**changes))
    assert reason in verdict.reasons
    assert verdict.score == sum(sr.WEIGHTS[r] for r in verdict.reasons)


def test_one_weak_sign_alone_does_not_quarantine():
    for changes in ({"form_age": 7}, {"accept_language": ""}, {"tor": True}, {"username": "xkcdqwrtz"}):
        assert sr.assess(facts(**changes)).action == sr.ALLOW, changes


def test_signs_that_add_up_to_the_threshold_quarantine():
    verdict = sr.assess(facts(form_age=7, accept_language="", tor=True))          # 20 + 15 + 25
    assert verdict.action == sr.QUARANTINE and verdict.score == 60


def test_a_burst_from_one_network_quarantines_by_itself():
    assert sr.assess(facts(prefix_recent=sr.PREFIX_BURST - 1)).action == sr.ALLOW
    assert sr.assess(facts(prefix_recent=sr.PREFIX_BURST)).action == sr.QUARANTINE


def test_the_captcha_service_being_down_quarantines_rather_than_refuses():
    assert sr.assess(facts(captcha="unavailable")).action == sr.QUARANTINE


def test_the_admins_threshold_is_used():
    assert sr.assess(facts(tor=True, quarantine_at=25)).action == sr.QUARANTINE
    assert sr.assess(facts(prefix_recent=9, quarantine_at=200)).action == sr.ALLOW
    assert sr.assess(facts(tor=True, quarantine_at=0)).action == sr.QUARANTINE, "never below 1"


def test_a_surge_quarantines_every_sign_up():
    verdict = sr.assess(facts(surge=True))
    assert verdict.action == sr.QUARANTINE and verdict.reasons == ("surge",)


def test_surge_is_the_switch_or_the_count_over_the_ceiling():
    assert sr.in_surge(0, True)
    assert not sr.in_surge(sr.SURGE_CEILING - 1, False)
    assert sr.in_surge(sr.SURGE_CEILING, False)
    assert sr.in_surge(5, False, ceiling=5)


@pytest.mark.parametrize("name", ["xkcdqwrtz", "user482913", "bcdfghjk", "a1b2c3d4e5f6a7", "mrthmpsn"])
def test_names_a_generator_makes(name):
    assert sr.machine_made(name)


@pytest.mark.parametrize("name", ["sarah_reads", "john1990", "Schwartz", "rhythm", "the_strength", "k8", "deadbeef", "",
                                  "Ashcroft42", "lindstrom"])
def test_names_people_pick(name):
    assert not sr.machine_made(name)


def test_name_shapes():
    assert sr.name_shape("john48291") == sr.name_shape("Mary12345") == "a9{5}"
    assert sr.name_shape("big_cat_007") == "a_a_9{3}"
    assert sr.name_shape("") == ""


def test_only_shapes_with_a_long_number_count():
    assert sr.shape_counts("a9{5}") and sr.shape_counts("a_9{3}") and sr.shape_counts("a9{12}")
    assert not sr.shape_counts("a") and not sr.shape_counts("a9{2}") and not sr.shape_counts("a_a")
    verdict = sr.assess(facts(username="sarah", shape_recent=99))
    assert "shape_burst" not in verdict.reasons


def test_prefixes():
    assert sr.ip_prefix("203.0.113.77") == "203.0.113.0/24"
    assert sr.ip_prefix("2001:db8:1:2:3:4:5:6") == "2001:db8:1:2::/64"
    assert sr.ip_prefix("::ffff:198.51.100.9") == "198.51.100.0/24"
    assert sr.ip_prefix("not an address") == "not an address"
    assert sr.ip_prefix(None) == ""


def test_disposable_domains_match_their_subdomains():
    domains = {"mailinator.com", "10minutemail.net"}
    assert sr.is_disposable("mailinator.com", domains)
    assert sr.is_disposable("eu.mailinator.com", domains)
    assert not sr.is_disposable("gmail.com", domains)
    assert not sr.is_disposable("com", domains)
    assert not sr.is_disposable("", domains)
    assert sr.email_domain("A.Person@Mailinator.COM.") == "mailinator.com"
    assert sr.email_domain("nobody") == ""


def test_stored_reasons_are_read_back_as_known_keys_only():
    assert sr.parse_reasons("tor,fast_form,<script>,") == ("tor", "fast_form")
    assert sr.parse_reasons(None) == ()


def test_every_reason_has_a_label():
    assert set(sr.WEIGHTS) | set(sr.REFUSALS) == set(sr.LABELS)


def test_the_rules_import_nothing_but_the_standard_library():
    tree = ast.parse(Path(sr.__file__).read_text())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"ipaddress", "re", "dataclasses"}
