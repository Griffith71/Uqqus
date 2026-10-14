"""Which embedded players are trusted (ruqqus/helpers/embed_trust.py): a named platform's own page pointing at
its own player is, anything else scraped from og:video keeps the strict sandbox."""
import re
from pathlib import Path

import pytest

from ruqqus.helpers.embed_trust import same_provider_player

ROOT = Path(__file__).resolve().parent.parent
DOMAINS = ("youtube.com", "odysee.com", "rumble.com")
ODYSEE = "https://odysee.com/@whataboutit:1/spacex-confirms-how-starship-just:1"
ODYSEE_PLAYER = "https://odysee.com/%24/embed/%40whataboutit%3A1%2Fspacex?autoplay=1"


@pytest.mark.parametrize("page, src", [
    (ODYSEE, ODYSEE_PLAYER),
    ("http://odysee.com/@x:1/y:2", ODYSEE_PLAYER),
    ("https://www.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/embed/abcdefghijk"),
    ("https://m.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/embed/abcdefghijk"),
])
def test_a_platforms_own_player_is_trusted(page, src):
    assert same_provider_player(page, src, DOMAINS)


@pytest.mark.parametrize("page, src", [
    ("https://evil.example/video", ODYSEE_PLAYER),               # someone else's page naming a platform's player
    (ODYSEE, "https://evil.example/player"),                     # a platform page naming someone else's player
    (ODYSEE, "https://www.youtube.com/embed/abcdefghijk"),       # two platforms, not the same one
    (ODYSEE, "http://odysee.com/%24/embed/x"),                   # the player must be https
    ("https://notodysee.com/x", "https://notodysee.com/%24/embed/x"),
    ("https://odysee.com.evil.example/x", "https://odysee.com.evil.example/embed"),
    ("javascript:alert(1)//odysee.com", ODYSEE_PLAYER),
    ("", ODYSEE_PLAYER),
    (ODYSEE, ""),
    (ODYSEE, None),
])
def test_anything_else_keeps_the_sandbox(page, src):
    assert not same_provider_player(page, src, DOMAINS)


def test_the_og_video_fallback_asks_it_with_the_iframe_platforms():
    src = (ROOT / "ruqqus" / "helpers" / "embed.py").read_text()
    assert 'IFRAME_PROVIDER_DOMAINS = tuple(d for d, (kind, _) in KNOWN_PROVIDER_HANDLERS.items() if kind == "iframe")' in src
    fallback = src[src.index("# Layer 3: og:video fallback"):]
    assert fallback.index("same_provider_player(url, og_video, IFRAME_PROVIDER_DOMAINS)") < fallback.index('return "iframe_untrusted"')


def test_the_repair_migration_names_every_iframe_platform():
    src = (ROOT / "ruqqus" / "helpers" / "embed.py").read_text()
    table = src[src.index("KNOWN_PROVIDER_HANDLERS = {"):src.index("}", src.index("KNOWN_PROVIDER_HANDLERS = {"))]
    iframe = set(re.findall(r'"([a-z.]+)": \("iframe"', table))
    sql = (ROOT / "scripts" / "migrations" / "2026-10-10_trusted_provider_embeds.sql").read_text()
    named = set(re.findall(r"\('([a-z.]+)'\)", sql))
    # clips.twitch.tv is covered by twitch.tv
    assert iframe - {"clips.twitch.tv"} == named
    assert "WHERE a.embed_type = 'iframe_untrusted'" in sql


def test_between_equally_visible_videos_the_higher_one_plays():
    js = (ROOT / "ruqqus" / "assets" / "js" / "feed_video_autoplay.js").read_text()
    assert "(best ? ratio > bestRatio : ratio >= bestRatio)" in js
