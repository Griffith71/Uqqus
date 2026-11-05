"""Which Rep gates exist (the owner, 2026-10-30): 250 Rep (or Premium, or an admin) to create a guild; no Rep for a
profile picture or a banner, only the Tor rule. The suite never imports the model, so the properties' own return
expressions are read from the source and run against a stand-in account."""
from pathlib import Path
from types import SimpleNamespace as NS

from ruqqus.helpers import age_declaration

USER = (Path(__file__).resolve().parent.parent / "ruqqus" / "classes" / "user.py").read_text(encoding="utf-8")


def returned(name):
    body = USER.split(f"    def {name}(self):", 1)[1].split("\n    @property", 1)[0]
    return [line.strip()[len("return "):] for line in body.splitlines() if line.strip().startswith("return ")][0]


def guild_maker(score, premium=False, admin=0, can_join=True, locked=False):
    me = NS(true_score=score, has_premium=premium, admin_level=admin, can_join_gms=can_join, age_declared=2 if locked else 1, age_lock=1 if locked else None)
    return bool(eval(returned("can_make_guild"), {"self": me, "age_declaration": age_declaration}))


def picture_maker(country=None, activated=False):
    me = NS(is_activated=activated)
    request = NS(headers={"cf-ipcountry": country} if country else {})
    return bool(eval(returned("can_upload_avatar"), {"self": me, "request": request}))


def test_a_guild_needs_250_rep_unless_premium_or_an_admin():
    assert not guild_maker(0) and not guild_maker(249)
    assert guild_maker(250) and guild_maker(5000)
    assert guild_maker(0, premium=True) and guild_maker(0, admin=3)
    assert not guild_maker(0, admin=2)
    assert not guild_maker(5000, premium=True, locked=True), "a child-locked account starts no guild, whatever it has"


def test_the_guild_limit_still_stops_everyone_with_enough_rep():
    assert not guild_maker(250, can_join=False) and not guild_maker(0, premium=True, can_join=False)


def test_a_picture_needs_no_rep_premium_or_age():
    assert picture_maker() and picture_maker(country="GB")
    assert "true_score" not in returned("can_upload_avatar") and "has_premium" not in returned("can_upload_avatar")
    assert "created_utc" not in returned("can_upload_avatar")
    assert returned("can_upload_banner") == "self.can_upload_avatar"


def test_through_tor_a_picture_needs_a_verified_email():
    assert not picture_maker(country="T1")
    assert picture_maker(country="T1", activated=True)
