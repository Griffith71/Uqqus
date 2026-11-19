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


def test_a_banned_picture_holds_the_uploader_through_the_one_routine_with_the_match_as_staff_detail(monkeypatch):
    """The hold itself is `auto_quarantine.hold` (helpers/scan_bans.py, tested in tests/test_scan_bans.py and on the real app in
    tests/pg/test_scan_bans.py); this checks what the check hands it: that a site-list match is a staff detail and a child-abuse match a level-4 one."""
    from ruqqus.helpers import scan_bans

    calls = []
    monkeypatch.setattr(scan_bans, "quarantine_uploader", lambda db, user, **kw: calls.append((db, user, kw)) or 77)
    uploader, db = user(1), FakeDB()
    assert safety.quarantine_uploader(db, uploader, "Spam picture") == 77
    assert safety.quarantine_uploader(db, uploader, "the CDN answered 451 for the picture", legal=True) == 77
    assert calls == [
        (db, uploader, {"legal": False, "detail": "matched a picture on the site's banned list: Spam picture"}),
        (db, uploader, {"legal": True, "detail": "the CDN answered 451 for the picture"}),
    ]


def test_no_check_bans_or_suspends_so_there_are_no_days_to_hand_over():
    """The owner, 2026-11-17: the site's own checks only quarantine. The old `ban_uploader(..., days=)` is gone, and nothing is left that could be given one."""
    import inspect

    assert not hasattr(safety, "ban_uploader")
    assert list(inspect.signature(safety.quarantine_uploader).parameters) == ["db", "user", "reason", "legal"]


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
