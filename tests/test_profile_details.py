"""Job, location and website on a profile (ruqqus/helpers/profile_details.py): one line of plain text each for job and
location; the website is an http(s) address with a real host and nothing hidden in it."""
import pytest

from ruqqus.helpers.profile_details import Refused, clean_text, clean_website, rated_text, website_label


def test_text_is_one_trimmed_line_with_no_markup():
    assert clean_text("  Rocket   engineer\n at  SpaceX ", "Job") == "Rocket engineer at SpaceX"
    assert clean_text("", "Job") == "" and clean_text(None, "Job") == ""
    with pytest.raises(Refused):
        clean_text("x" * 61, "Job")
    with pytest.raises(Refused):
        clean_text("<b>boss</b>", "Job")


@pytest.mark.parametrize("raw, stored", [
    ("example.com", "https://example.com"),
    ("https://Example.COM/about?x=1#top", "https://example.com/about?x=1"),
    ("http://blog.example.co.uk/", "http://blog.example.co.uk/"),
    ("https://example.com:8443/a", "https://example.com:8443/a"),
    ("", ""),
])
def test_websites_that_are_kept(raw, stored):
    assert clean_website(raw) == stored


@pytest.mark.parametrize("raw", [
    "javascript:alert(1)", "data:text/html,hi", "mailto:a@example.com", "ftp://example.com",
    "https://user:pass@example.com", "https://localhost", "https://192.168.0.1", "https://exa mple.com",
    "https://" + "a" * 200 + ".com", "https://-bad.com", "//example.com:99999",
])
def test_websites_that_are_refused(raw):
    with pytest.raises(Refused):
        clean_website(raw)


def test_how_a_website_reads_on_the_profile():
    assert website_label("https://www.example.com/") == "example.com"
    assert website_label("https://example.com/blog/") == "example.com/blog"
    assert website_label("https://example.com/" + "a" * 60).endswith("…")
    assert website_label("") == ""


def test_the_word_filter_reads_job_and_location():
    assert rated_text("Pilot", "Mars") == "Pilot Mars" and rated_text("", "") == ""
