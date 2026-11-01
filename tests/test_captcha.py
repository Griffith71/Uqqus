"""The sign-up captcha (ruqqus/helpers/captcha.py): which provider is chosen, what the page draws, and how an answer
is checked (the provider faked: nothing leaves the machine)."""
import pytest

from ruqqus.helpers import captcha

HCAPTCHA = {"CAPTCHA_PROVIDER": "hcaptcha", "HCAPTCHA_SITEKEY": "site-h", "HCAPTCHA_SECRET": "secret-h"}
TURNSTILE = {"CAPTCHA_PROVIDER": "turnstile", "TURNSTILE_SITEKEY": "site-t", "TURNSTILE_SECRET": "secret-t"}


class Answer:
    def __init__(self, status=200, body=None, error=None):
        self.status_code, self.body, self.error = status, body, error

    def json(self):
        if self.error:
            raise self.error
        return self.body


class Poster:
    def __init__(self, answer=None, raises=None):
        self.answer, self.raises, self.calls = answer, raises, []

    def __call__(self, url, data=None, timeout=None):
        self.calls.append((url, data, timeout))
        if self.raises:
            raise self.raises
        return self.answer


def test_no_configuration_is_no_captcha():
    assert captcha.chosen({}) is None and captcha.widget({}) is None
    assert captcha.verify({}, "anything", "1.2.3.4", post=Poster()) == "off"


def test_an_hcaptcha_site_key_alone_keeps_the_old_behaviour():
    assert captcha.chosen({"HCAPTCHA_SITEKEY": "s", "HCAPTCHA_SECRET": "x"}) == "hcaptcha"


@pytest.mark.parametrize("config", [
    {"CAPTCHA_PROVIDER": "none", "HCAPTCHA_SITEKEY": "s", "HCAPTCHA_SECRET": "x"},
    {"CAPTCHA_PROVIDER": "turnstile", "TURNSTILE_SITEKEY": "s"},
    {"CAPTCHA_PROVIDER": "recaptcha", "RECAPTCHA_SITEKEY": "s", "RECAPTCHA_SECRET": "x"},
])
def test_off_unknown_or_half_configured_is_no_captcha(config):
    assert captcha.chosen(config) is None


def test_the_widget_names_its_provider_script_and_key():
    assert captcha.widget(TURNSTILE) == {"provider": "turnstile", "sitekey": "site-t", "widget_class": "cf-turnstile",
                                         "script": "https://challenges.cloudflare.com/turnstile/v0/api.js"}
    assert captcha.widget(HCAPTCHA)["widget_class"] == "h-captcha"


def test_each_provider_reads_its_own_form_field():
    form = {"h-captcha-response": " h-token ", "cf-turnstile-response": "t-token"}
    assert captcha.answer_of(HCAPTCHA, form) == "h-token"
    assert captcha.answer_of(TURNSTILE, form) == "t-token"
    assert captcha.answer_of({}, form) == ""


def test_no_answer_is_missing_and_asks_nobody():
    poster = Poster()
    assert captcha.verify(TURNSTILE, "", "1.2.3.4", post=poster) == "missing"
    assert poster.calls == []


@pytest.mark.parametrize("config, url, sitekey", [
    (HCAPTCHA, "https://api.hcaptcha.com/siteverify", "site-h"),
    (TURNSTILE, "https://challenges.cloudflare.com/turnstile/v0/siteverify", None),
])
def test_a_good_answer_is_ok_and_the_address_is_sent(config, url, sitekey):
    poster = Poster(Answer(body={"success": True}))
    assert captcha.verify(config, "tok", "203.0.113.9", post=poster) == "ok"
    (called, data, timeout), = poster.calls
    assert called == url and timeout == captcha.TIMEOUT
    assert data["response"] == "tok" and data["remoteip"] == "203.0.113.9"
    assert data.get("sitekey") == sitekey


def test_the_provider_saying_no_is_failed():
    assert captcha.verify(TURNSTILE, "tok", "1.2.3.4", post=Poster(Answer(body={"success": False}))) == "failed"


@pytest.mark.parametrize("poster", [
    Poster(raises=TimeoutError("slow")),
    Poster(Answer(status=502, body={"success": True})),
    Poster(Answer(error=ValueError("not json"))),
    Poster(Answer(body=["success"])),
    Poster(Answer(body={"success": "true"})),
    Poster(Answer(body={})),
])
def test_a_provider_that_cannot_be_asked_or_answers_nonsense_is_unavailable(poster):
    assert captcha.verify(HCAPTCHA, "tok", "1.2.3.4", post=poster) == "unavailable"


def test_the_privacy_page_gets_the_name_of_the_service_in_use_and_none_without_one():
    assert captcha.label(HCAPTCHA) == "hCaptcha"
    assert captcha.label(TURNSTILE) == "Cloudflare Turnstile"
    assert captcha.label({"CAPTCHA_PROVIDER": "none", "HCAPTCHA_SITEKEY": "x", "HCAPTCHA_SECRET": "y"}) is None
    assert captcha.label({"CAPTCHA_PROVIDER": "hcaptcha", "HCAPTCHA_SITEKEY": "only half"}) is None
    assert captcha.label({}) is None
