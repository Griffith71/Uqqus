"""helpers/forward_view.py (audit P-06): who forwarded each post of a page, read once, into the caches the bylines,
menus and flair rule use."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import forward_view


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    session = Session(engine)
    session.execute(text("CREATE TABLE forwardrels (forward_submission_id INTEGER, forwarded_by_id INTEGER)"))
    session.execute(text("CREATE TABLE comment_forwardrels (promoted_submission_id INTEGER, promoted_by_id INTEGER)"))
    session.execute(text("INSERT INTO forwardrels VALUES (2, 9), (3, 7)"))               # 3 the author forwarded
    session.execute(text("INSERT INTO comment_forwardrels VALUES (4, 9)"))
    monkeypatch.setattr(forward_view, "_users", lambda _db, ids: {i: SimpleNamespace(id=i, username="fwd") for i in ids})
    return session


def test_attach_fills_the_caches(db):
    posts = [SimpleNamespace(id=i, author_id=7) for i in (1, 2, 3, 4)]
    forward_view.attach(db, posts)
    one, two, three, four = (p.__dict__ for p in posts)
    assert one["_lazy"]["is_forward_copy"] is False and one["_forwarder_id"] is None
    assert two["_lazy"]["is_forward_copy"] is True and two["_lazy"]["forwarded_by"].username == "fwd"
    assert two["_forwarder_id"] == 9
    assert three["_lazy"]["forwarded_by"] is None and three["_forwarder_id"] == 7     # the author's own forward
    assert four["_lazy"]["is_comment_forward"] is True and four["_lazy"]["comment_forwarded_by"].id == 9


def test_attach_twice_is_harmless(db):
    posts = [SimpleNamespace(id=2, author_id=7)]
    forward_view.attach(db, posts)
    forward_view.attach(db, posts)
    assert posts[0].__dict__["_forwarder_id"] == 9
