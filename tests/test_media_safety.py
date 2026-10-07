"""Checks run on an upload in a member's linked storage (ruqqus/helpers/media/safety.py)."""
import os
import time
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers.media import rules, safety
from ruqqus.helpers.media.base import Stream


class FakeProvider:
    def __init__(self, blocks):
        self.blocks = blocks

    def open(self, account, asset, byte_range=None):
        return Stream(iter(self.blocks), size=sum(len(b) for b in self.blocks))


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def user(id, alts=()):
    u = NS(id=id, is_banned=0, ban_reason=None, unban_utc=0)
    u.alts_threaded = lambda db: list(alts)
    return u


def test_the_file_is_fetched_to_a_temp_file_that_can_be_discarded():
    path = safety.fetch_to_temp(FakeProvider([b"abc", b"def"]), None, None, limit=100)
    try:
        assert open(path, "rb").read() == b"abcdef"
    finally:
        safety.discard(path)
    assert not os.path.exists(path)
    safety.discard(path)        # twice is fine


def test_a_file_bigger_than_it_claimed_is_refused_and_nothing_is_left_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(safety.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(rules.MediaError) as err:
        safety.fetch_to_temp(FakeProvider([b"x" * 60, b"x" * 60]), None, None, limit=100)
    assert err.value.code == 413
    assert list(tmp_path.iterdir()) == []


def test_a_provider_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(safety.tempfile, "tempdir", str(tmp_path))

    def broken():
        yield b"some"
        raise ConnectionError("provider went away")

    with pytest.raises(ConnectionError):
        safety.fetch_to_temp(FakeProvider(broken()), None, None, limit=100)
    assert list(tmp_path.iterdir()) == []


def test_a_banned_picture_suspends_the_uploader_and_their_other_accounts():
    alt = user(2)
    uploader = user(1, alts=[alt])
    db = FakeDB()
    before = int(time.time())
    safety.ban_uploader(db, uploader, "Spam picture", days=3)
    for account in (uploader, alt):
        assert account.is_banned == 1 and account.ban_reason == "Spam picture"
        assert before + 3 * 86400 <= account.unban_utc <= before + 3 * 86400 + 5
    assert db.added == [uploader, alt]


def test_no_days_means_a_permanent_ban():
    uploader = user(1)
    safety.ban_uploader(FakeDB(), uploader, "Sexualizing Minors")
    assert uploader.is_banned == 1 and uploader.unban_utc == 0


def test_only_served_pictures_are_scanned_at_their_public_address(monkeypatch):
    import sys
    import types

    spawned = []
    monkeypatch.setitem(sys.modules, "gevent", types.SimpleNamespace(spawn=lambda fn, *a: spawned.append(a)))
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.media.cdn", types.SimpleNamespace(absolute=lambda p: "https://site.test" + p))
    monkeypatch.setattr("ruqqus.helpers.media.cdn", sys.modules["ruqqus.helpers.media.cdn"], raising=False)

    picture = NS(id=5, kind="image", provider="gdrive", ext="png", path="/media/5/t.png")
    sound = NS(id=6, kind="audio", provider="gdrive", ext="mp3", path="/media/6/t.mp3")
    video = NS(id=7, kind="video", provider="youtube", ext="", path="")
    safety.scan_later([picture, sound, video])
    assert spawned == [("https://site.test/media/5/t.png", 5)]
    safety.scan_later(None)
    assert len(spawned) == 1
