"""Rules for drafts and scheduled posts (ruqqus/helpers/post_drafts.py)."""
import pytest
from werkzeug.datastructures import MultiDict

from ruqqus.helpers import post_drafts as pd
from ruqqus.helpers.post_fields import BODY_MAX, PostFieldError

NOW = 1_800_000_000


def form(*pairs):
    return MultiDict(list(pairs))


# --- what is saved -------------------------------------------------------------

def test_a_draft_saves_the_composers_fields():
    f = pd.clean_fields(form(
        ("title", "  A title\n\twith breaks  "), ("url", " https://example.com/x "), ("body", "some **text**"),
        ("forward_guilds", "+general"), ("forward_guilds", "Test"), ("forward_guilds", "general"),
        ("comment_permission", "2"), ("paid_partnership", "true"), ("sensitive", "on")))
    assert f["title"] == "A titlewith breaks"          # one line, like the real title
    assert f["url"] == "https://example.com/x" and f["body"] == "some **text**"
    assert f["forward_guilds"] == ["general", "Test"]  # leading + dropped, case-insensitive duplicates dropped
    assert f["options"] == {"comment_permission": 2, "paid_partnership": True, "made_with_ai": False, "anonymous": False, "sensitive": True}


def test_an_empty_draft_is_valid_but_flagged_empty():
    f = pd.clean_fields(form())
    assert pd.is_empty(f)
    assert f["options"]["comment_permission"] == 0 and f["forward_guilds"] == []
    assert not pd.is_empty(pd.clean_fields(form(("body", "x"))))


@pytest.mark.parametrize("pairs, message", [
    ([("title", "x" * 281)], "280 character limit"),
    ([("url", "https://e.com/" + "x" * 2048)], "2048 character limit"),
    ([("body", "x" * (BODY_MAX + 1))], "25000 character limit"),
    ([("comment_permission", "9")], "who can comment"),
    ([("forward_guilds", "g" * 51)], "too long"),
    ([("forward_guilds", f"g{i}") for i in range(pd.FORWARD_GUILDS_MAX + 1)], "at most"),
])
def test_bad_fields_are_refused_with_a_message(pairs, message):
    with pytest.raises(PostFieldError) as err:
        pd.clean_fields(form(*pairs))
    assert message.lower() in str(err.value).lower()


def test_a_false_tick_box_value_means_off():
    f = pd.clean_fields(form(("made_with_ai", "false"), ("sensitive", "")))
    assert f["options"]["made_with_ai"] is False and f["options"]["sensitive"] is False


# --- the scheduling window -----------------------------------------------------

def test_a_time_inside_the_window_is_accepted():
    assert pd.schedule_time(str(NOW + pd.MIN_LEAD_SECONDS), NOW) == NOW + pd.MIN_LEAD_SECONDS
    assert pd.schedule_time(NOW + pd.MAX_LEAD_SECONDS, NOW) == NOW + pd.MAX_LEAD_SECONDS


@pytest.mark.parametrize("when, message", [
    (NOW - 1, "5 minutes"), (NOW, "5 minutes"), (NOW + pd.MIN_LEAD_SECONDS - 1, "5 minutes"),
    (NOW + pd.MAX_LEAD_SECONDS + 1, "a year"),
    ("", "date and time"), ("tomorrow", "date and time"), (None, "date and time"),
])
def test_a_time_outside_the_window_is_refused(when, message):
    with pytest.raises(PostFieldError) as err:
        pd.schedule_time(when, NOW)
    assert message in str(err.value)


def test_scheduling_needs_a_title():
    with pytest.raises(PostFieldError):
        pd.require_title(pd.clean_fields(form(("body", "text only"))))
    pd.require_title(pd.clean_fields(form(("title", "ok"))))


# --- what the publisher submits ------------------------------------------------

def test_the_publisher_submits_the_same_form_the_composer_would():
    fields = pd.clean_fields(form(("title", "T"), ("forward_guilds", "general"), ("forward_guilds", "test"),
                                  ("comment_permission", "1"), ("made_with_ai", "true"), ("anonymous", "true")))
    data = pd.publish_form(fields, "KEY")
    assert data == {"title": "T", "url": "", "body": "", "forward_guilds": ["general", "test"],
                    "comment_permission": "1", "formkey": "KEY", "made_with_ai": "true", "anonymous": "true"}
    assert "sensitive" not in data and "paid_partnership" not in data   # unticked boxes are not sent


# --- waiting out a posting cooldown --------------------------------------------

def test_the_cooldown_message_is_read():
    assert pd.cooldown_seconds("Slow down - cooldown active for another 4:07.") == 247
    assert pd.cooldown_seconds("Too much spam!") is None
    assert pd.cooldown_seconds(None) is None


def test_a_cooldown_is_waited_out_but_only_so_many_times():
    msg = "Slow down - cooldown active for another 2:00."
    assert pd.retry_at(NOW, msg, attempts=1) == NOW + 125
    assert pd.retry_at(NOW, "Slow down - cooldown active for another 0:03.", attempts=1) == NOW + 60   # never sooner than a minute
    assert pd.retry_at(NOW, msg, attempts=pd.MAX_ATTEMPTS) is None
    assert pd.retry_at(NOW, "403 Forbidden", attempts=1) is None


def test_the_failure_notice_names_the_post_and_the_reason():
    text = pd.failure_notice("My post", "+general has been banned.")
    assert '"My post"' in text and "+general has been banned." in text and "drafts" in text
    assert '"Untitled"' in pd.failure_notice("", "x")
