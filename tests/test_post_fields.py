"""Rules for the fields an author can change when editing a post
(ruqqus/helpers/post_fields.py)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "post_fields", ROOT / "ruqqus" / "helpers" / "post_fields.py")
post_fields = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post_fields)

clean_title = post_fields.clean_title
check_body = post_fields.check_body
normalize_url = post_fields.normalize_url
PostFieldError = post_fields.PostFieldError


def test_title_is_trimmed_and_flattened_to_one_line():
    assert clean_title("  hello\n\tworld\r  ") == "helloworld"


def test_title_markup_is_escaped_like_at_creation():
    assert clean_title("<script>x</script> ok") == "&lt;script&gt;x&lt;/script&gt; ok"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t", None])
def test_title_is_required(bad):
    with pytest.raises(PostFieldError, match="needs a title"):
        clean_title(bad)


def test_title_length_limit():
    assert clean_title("x" * 280) == "x" * 280
    with pytest.raises(PostFieldError, match="280 character limit"):
        clean_title("x" * 281)


def test_body_length_limit():
    assert check_body(None) == ""
    assert check_body("y" * 25000) == "y" * 25000
    with pytest.raises(PostFieldError, match="25000 character limit"):
        check_body("y" * 25001)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_url_means_no_link(raw):
    assert normalize_url(raw) == ""


def test_url_is_forced_to_https_and_keeps_its_parts():
    assert normalize_url("http://Example.com/a/b?x=1#frag") == "https://Example.com/a/b?x=1#frag"
    assert normalize_url("  https://odysee.com/@a:1/b:2 ") == "https://odysee.com/@a:1/b:2"


def test_url_without_scheme_gets_https():
    assert normalize_url("example.com/page") == "https://example.com/page"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://example.com/x", "https://", "notalink", "http://localhost"])
def test_invalid_urls_are_rejected(bad):
    with pytest.raises(PostFieldError, match="valid link"):
        normalize_url(bad)


def test_url_length_limit():
    with pytest.raises(PostFieldError, match="2048 character limit"):
        normalize_url("https://example.com/" + "a" * 2048)


# --- yes/no fields (content disclosure) ---------------------------------------

from werkzeug.datastructures import MultiDict  # noqa: E402  (flask is a dev dependency)

flag = post_fields.flag


def test_a_flag_that_is_absent_keeps_its_default():
    assert flag(MultiDict(), "made_with_ai") is False
    assert flag(MultiDict(), "made_with_ai", default=True) is True
    assert flag(MultiDict({"other": "x"}), "made_with_ai", default=True) is True


@pytest.mark.parametrize("value", ["true", "1", "on", "yes", "True", " x "])
def test_a_flag_is_on_for_any_yes_value(value):
    assert flag(MultiDict({"made_with_ai": value}), "made_with_ai") is True


@pytest.mark.parametrize("value", ["", "0", "false", "False", "off", "no", "none", "  "])
def test_a_flag_is_off_for_empty_and_no_values(value):
    # "false" used to count as on (bool("false")); an API client sending it means off
    assert flag(MultiDict({"made_with_ai": value}), "made_with_ai", default=True) is False


def test_an_edit_form_sends_a_hidden_empty_value_next_to_the_checkbox():
    unticked = MultiDict([("made_with_ai", "")])
    ticked = MultiDict([("made_with_ai", ""), ("made_with_ai", "true")])
    assert flag(unticked, "made_with_ai", default=True) is False
    assert flag(ticked, "made_with_ai") is True
