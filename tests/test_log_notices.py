"""What a person is told when a decision is made about them (helpers/log_notices.py, the Log's slice D2).

The promises: a hide, a removal and an author's hide each have a notice with the rule, the reason, the Log's link and the Appeal link; the
reason is the one place a stranger's words reach a message that arrives as the site's, so it goes in a fenced block (which the site's
linking skips) and can't end it; every message is marked for translation and names exactly the values it is given."""
import re

from ruqqus.helpers import log_notices as notices
from ruqqus.helpers.notice_parts import needs, rendered

ALL = (notices.HID_POST, notices.HID_COMMENT, notices.REMOVED_POST, notices.REMOVED_COMMENT, notices.AUTHOR_HID_COMMENT, notices.DECISION,
       notices.UPHELD, notices.REVERSED, notices.DISMISSED)


def values_in(message):
    """What the message needs from the one who sends it: its placeholders, the derived ones (the fenced reason, the links: helpers/notice_parts.py) as the values they are built from."""
    return needs(message)


def test_the_messages_name_only_the_values_their_builders_give():
    decision = {"item", "guild", "why", "log", "appeal"}
    for message in (notices.HID_POST, notices.HID_COMMENT):
        assert values_in(message) == decision
    for message in (notices.REMOVED_POST, notices.REMOVED_COMMENT, notices.AUTHOR_HID_COMMENT):
        assert values_in(message) == decision - {"guild"}
    assert values_in(notices.DECISION) == {"log", "appeal"}
    for message in (notices.UPHELD, notices.REVERSED, notices.DISMISSED):
        assert values_in(message) == {"log", "note"}


def test_a_reason_sits_in_a_fenced_block_and_every_decision_notice_links_the_log_and_the_appeal():
    """The fence and the links are built by code from the stored values (helpers/notice_parts.py), so this checks what a notice DRAWS."""
    values = {"item": "/post/abc", "guild": "pets", "why": "No jokes", "log": "/log/5", "appeal": "/log/5/appeal", "note": "Thanks"}
    for message in ALL:
        text = rendered(message, values)
        if "%(why_block)s" in message or "%(note_block)s" in message:
            assert "```\nNo jokes\n```" in text or "```\nThanks\n```" in text, message
            assert ">" not in text.replace("](", "")                                   # not a quote: a quote's text is linked on its way
    for message in (notices.HID_POST, notices.HID_COMMENT, notices.REMOVED_POST, notices.REMOVED_COMMENT, notices.AUTHOR_HID_COMMENT, notices.DECISION):
        text = rendered(message, values)
        assert "[See it in the Log](/log/5)" in text and "[Appeal this decision](/log/5/appeal)" in text
        assert "%(log_link)s" in message and "%(appeal_link)s" in message and "](" not in message and "```" not in message, "the translated message holds words and placeholders only"


def test_words_cant_end_the_block_or_run_on():
    assert notices.plain("a ``` b ` c") == "a ' b ' c"
    assert notices.plain("line one\n\n  line two") == "line one line two"
    assert len(notices.plain("x" * 1000)) == notices.WHY
    assert notices.plain(None) == ""
    # the rest of a message is code-built links; none of a person's words can add one outside the block
    built = notices.decision("hide_post_from_guild", entry_id=5, item="/+pets/post/abc", guild="pets", reason="```\n[x](http://evil.example)")
    text = rendered(built[0], built[1])
    assert text.count("```") == 2 and "evil.example" in text.split("```")[1]
    assert text.split("```")[2].count("http") == 0 and "http://evil" not in text.split("```")[0]


def test_a_reason_is_the_rule_and_the_words_or_whichever_there_is():
    assert notices.why_of("illegal", "Selling something") == "Criminal activity and prohibited conduct: Selling something"
    assert notices.why_of("illegal", "") == "Criminal activity and prohibited conduct"
    assert notices.why_of("", "Spam links everywhere") == "Spam links everywhere"
    assert notices.why_of("", "") == "No reason was given."
    assert notices.why_of("not_a_rule", "x") == "x"


def test_each_decision_has_its_own_notice_and_an_action_with_none_has_no_notice():
    cases = {"hide_post_from_guild": notices.HID_POST, "hide_comment_from_guild": notices.HID_COMMENT, "ban_post": notices.REMOVED_POST,
             "ban_comment": notices.REMOVED_COMMENT, "author_hide_comment": notices.AUTHOR_HID_COMMENT}
    for action, message in cases.items():
        built = notices.decision(action, entry_id=12, item="/post/abc", guild="pets", rule_key="guild_rule", reason="No jokes")
        assert built[0] == message
        assert built[1] == {"item": "/post/abc", "guild": "pets", "why": "A rule of the guild: No jokes", "log": "/log/12",
                            "appeal": "/log/12/appeal"}
        assert rendered(message, built[1])                                               # every value it names is given
    assert notices.decision("exile_user", entry_id=1, item="x") is None


def test_an_appeals_outcome_says_where_to_read_the_decision_and_carries_the_note():
    for outcome, message in notices.OUTCOME_NOTICES.items():
        built = notices.outcome(outcome, entry_id=9, note="  Thanks `for` asking\n")
        assert built == (message, {"log": "/log/9", "note": "Thanks 'for' asking"})
    assert notices.outcome("upheld", entry_id=9, note="")[1]["note"] == "-"
    short = notices.way_to_appeal(77)
    assert short == (notices.DECISION, {"log": "/log/77", "appeal": "/log/77/appeal"})


def test_every_message_is_marked_for_translation():
    """`N_` returns the string itself, so this is what the extractor reads: the constants are assigned from N_(...) calls."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parent.parent / "ruqqus" / "helpers" / "log_notices.py").read_text(encoding="utf-8")
    assert source.count("= N_(") >= len(ALL)


def test_the_quarantine_notice_carries_the_rule_the_generic_reason_and_the_way_to_a_review_and_never_the_log():
    from ruqqus.helpers import auto_quarantine
    assert values_in(notices.QUARANTINED) == {"site", "why", "appeal"} and "%(why_block)s" in notices.QUARANTINED
    drawn = rendered(notices.QUARANTINED, {"site": "Laba", "why": "Automatic check", "appeal": notices.QUARANTINE_APPEAL})
    assert "```\nAutomatic check\n```" in drawn and "[Ask for a review](/appeal/quarantine)" in drawn and notices.QUARANTINE_APPEAL == "/appeal/quarantine"
    assert "%(log)s" not in notices.QUARANTINED                    # the staff's entry about it is not the person's to open: the review page finds it for them
    assert values_in(notices.QUARANTINE_LIFTED) == set()
    # every trigger's wording the person reads is a generic one: never what was matched
    for spec in auto_quarantine.TRIGGERS.values():
        why = notices.why_of(spec.rule if spec.say_rule else "", spec.reason)
        assert why.endswith(spec.reason) and "Automatic" in why
    assert notices.why_of("", "Automatic check") == "Automatic check"                              # ban-evasion detection names no rule
    assert notices.QUARANTINED in notices.HELD_FROM_LIMITED and notices.QUARANTINE_LIFTED in notices.HELD_FROM_LIMITED


def test_the_quarantine_notices_are_sent_the_way_the_others_are():
    source = open(__import__("pathlib").Path(__file__).resolve().parent.parent / "ruqqus" / "helpers" / "log_notices.py", encoding="utf-8").read()
    told = source.split("def tell_quarantined(", 1)[1].split("def tell_heads(", 1)[0]
    assert "send(user, (QUARANTINED, " in told and "mark_sent(db, entry_id)" in told and "db.rollback()" in told      # best effort, the entry says it was told
    assert "tell_lifted(db, user_id, QUARANTINE_LIFTED)" in told
