"""Plausible, if the owner ever switches it on, hears a page's kind and never its address (helpers/analytics.py, the owner
2026-11-19): on the real app, with the manual script configured."""
import secrets

import pytest

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"


def get(client, path):
    return client.get(path, base_url=BASE, headers={"User-Agent": AGENT},
                      environ_base={"REMOTE_ADDR": f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"})


@pytest.fixture
def plausible_on(real_app, monkeypatch):
    app, _ = real_app
    monkeypatch.setitem(app.config, "ANALYTICS_SRC", "https://stats.example.org/js/script.manual.js")
    monkeypatch.setitem(app.config, "ANALYTICS_DOMAIN", "laba.example")
    monkeypatch.setitem(app.config, "ANALYTICS_HOST", "stats.example.org")
    monkeypatch.setattr(app.jinja_env, "cache", {})      # `| app_config` is folded into the compiled template
    yield app
    app.jinja_env.cache = {}


def test_a_page_reports_its_kind_and_never_its_address(plausible_on):
    page = get(plausible_on.test_client(), "/all").get_data(as_text=True)
    assert 'window.plausible("pageview", { u: location.origin + "/all" })' in page
    assert 'src="https://stats.example.org/js/script.manual.js"' in page
    help_page = get(plausible_on.test_client(), "/help/privacy").get_data(as_text=True)
    assert 'location.origin + "/help"' in help_page and "/help/privacy\" })" not in help_page
    assert "the kind of page (a post, a profile), never its address" in help_page


def test_a_frame_and_a_page_with_no_kind_report_nothing(plausible_on):
    assert "plausible" not in get(plausible_on.test_client(), "/all?embed=1").get_data(as_text=True)
    assert "plausible" not in get(plausible_on.test_client(), "/login/qr/approve").get_data(as_text=True)


def test_nothing_while_it_is_off(real_app):
    app, _ = real_app
    assert "plausible" not in get(app.test_client(), "/all").get_data(as_text=True)
