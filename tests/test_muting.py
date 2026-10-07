"""Muting (ruqqus/helpers/muting.py): what the filter keeps and drops, and that an
anonymous post or comment is never hidden by a mute."""
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, Table, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base

from ruqqus.helpers import muting

Base = declarative_base()


class Row(Base):
    __tablename__ = "rows"
    id = Column(Integer, primary_key=True)
    author_id = Column(Integer)
    is_anonymous = Column(Boolean)


mutes = Table("usermutes", MetaData(), Column("user_id", Integer), Column("target_id", Integer))


@pytest.fixture(autouse=True)
def fake_mutes(monkeypatch):
    monkeypatch.setattr(muting, "muted_ids", lambda v: select(mutes.c.target_id).where(mutes.c.user_id == v.id))


def sql(query):
    return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def listing(viewer):
    return sql(muting.hide_muted(select(Row.id), viewer, Row))


def test_a_visitor_has_muted_nobody_and_the_query_is_untouched():
    q = select(Row.id)
    assert muting.hide_muted(q, None, Row) is q


def test_rows_by_a_muted_account_are_dropped_for_the_muter_only():
    text = listing(NS(id=7))
    assert "rows.author_id NOT IN (SELECT usermutes.target_id" in text
    assert "usermutes.user_id = 7" in text


def test_an_anonymous_row_stays_whoever_wrote_it():
    # hiding only the anonymous ones would tell the muter which they wrote
    assert "OR rows.is_anonymous = true" in listing(NS(id=7))


def test_one_muting_does_not_leak_into_another_viewers_query():
    assert "usermutes.user_id = 7" in listing(NS(id=7)) and "usermutes.user_id = 8" in listing(NS(id=8))
    assert "usermutes.user_id = 7" not in listing(NS(id=8))


def test_is_muted_follows_the_set_and_never_for_anonymous_items(monkeypatch):
    monkeypatch.setattr(muting, "muted_set", lambda v: frozenset({5}))
    viewer = NS(id=1)
    assert muting.is_muted(NS(author_id=5, is_anonymous=False), viewer)
    assert not muting.is_muted(NS(author_id=6, is_anonymous=False), viewer)
    assert not muting.is_muted(NS(author_id=5, is_anonymous=True), viewer)      # anonymous: never
    assert not muting.is_muted(NS(author_id=5, is_anonymous=False), None)       # a visitor mutes nobody
    assert muting.is_muted(NS(author_id=5), viewer)                             # no flag at all reads as not anonymous


def test_a_visitor_has_an_empty_set():
    assert muting.muted_set(None) == frozenset()
