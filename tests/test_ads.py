"""Ads (ruqqus/helpers/ads.py): where an ad may show, and that nothing shows until the site is set up for it."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import ads

ENV = {"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "1111111111",
       "ADSENSE_SLOT_POST": "2222222222", "ADSENSE_SLOT_SIDEBAR": "3333333333", "ADSENSE_FEED_LAYOUT_KEY": "-fb+5w+4e-db+86"}
CFG = ads.config(ENV)


def board(**over):
    return NS(**{"is_banned": False, "is_private": False, "is_circle": False, "is_sensitive": False, "is_nsfl": False,
                 "name_severity": 0, **over})


def post(**over):
    return NS(**{"is_banned": False, "deleted_utc": 0, "post_public": True, "is_sensitive": False, "word_severity": 0,
                 "board": board(), **over})


def member(**over):
    return NS(**{"id": 5, "has_premium_no_renew": False, "filter_level": 1, **over})


def test_nothing_without_a_client_id():
    assert ads.config({}) is None
    assert ads.config({"ADSENSE_CLIENT": "pub-123"}) is None
    assert ads.config({"ADSENSE_CLIENT": 'ca-pub-123"><script>'}) is None
    assert not ads.allows("feed", None, cfg=None)


def test_values_written_into_the_page_are_checked():
    cfg = ads.config({"ADSENSE_CLIENT": "ca-pub-1234567890123456", "ADSENSE_SLOT_FEED": "12345\"x",
                      "ADSENSE_SLOT_POST": "2222222222", "ADSENSE_FEED_LAYOUT_KEY": "<b>"})
    assert cfg["slots"] == {"post": "2222222222"} and cfg["feed_layout_key"] == ""
    assert cfg["publisher"] == "pub-1234567890123456"
    assert not ads.allows("feed", None, cfg=cfg) and ads.allows("post", None, post=post(), cfg=cfg)


def test_ads_txt_names_the_publisher():
    assert ads.ADS_TXT.format(publisher=CFG["publisher"]) == "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n"


def test_visitors_and_members_see_ads():
    for page in ads.PAGES:
        assert ads.allows(page, None, cfg=CFG) and ads.allows(page, member(), cfg=CFG)


def test_premium_members_child_filter_and_side_panels_see_none():
    assert not ads.allows("feed", member(has_premium_no_renew=True), cfg=CFG)
    assert not ads.allows("feed", member(filter_level=2), cfg=CFG)
    assert ads.allows("feed", member(filter_level=0), cfg=CFG)
    assert not ads.allows("feed", None, embedded=True, cfg=CFG)


def test_premium_is_read_without_renewing():
    class Member:
        id, filter_level, has_premium_no_renew = 5, 1, False

        @property
        def has_premium(self):
            raise AssertionError("has_premium spends a coin to renew")
    assert ads.allows("feed", Member(), cfg=CFG)


@pytest.mark.parametrize("over", [{"post_public": False}, {"is_banned": True}, {"deleted_utc": 5},
                                  {"is_sensitive": True}, {"word_severity": 1}, {"word_severity": 2},
                                  {"board": board(is_private=True)}, {"board": board(is_circle=True)},
                                  {"board": board(is_sensitive=True)}, {"board": board(is_nsfl=True)},
                                  {"board": board(is_banned=True)}, {"board": board(name_severity=1)}])
def test_no_ad_beside_a_post_that_is_not_for_everyone(over):
    assert ads.allows("post", None, post=post(), cfg=CFG)
    assert not ads.allows("post", None, post=post(**over), cfg=CFG)
    assert not ads.allows("sidebar", None, post=post(**over), cfg=CFG)


@pytest.mark.parametrize("over", [{"is_private": True}, {"is_circle": True}, {"is_sensitive": True}, {"is_nsfl": True},
                                  {"is_banned": True}, {"name_severity": 2}])
def test_no_ad_in_a_guild_that_is_not_for_everyone(over):
    assert ads.allows("feed", None, board=board(), cfg=CFG)
    assert not ads.allows("feed", None, board=board(**over), cfg=CFG)
