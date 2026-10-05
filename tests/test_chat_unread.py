"""Navbar chat badge: ChatUnread.total_for_user (real SQL on in-memory SQLite)
and GET /api/chat/unread_count (through the legacy harness)."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from legacy_harness import FakeUser

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------- the real model, real SQL
@pytest.fixture
def chat_models(monkeypatch):
    from sqlalchemy import Column, Integer
    from sqlalchemy.orm import declarative_base

    base = declarative_base()

    class User(base):  # chat models point at users.id / "User" by name
        __tablename__ = "users"
        id = Column(Integer, primary_key=True)

    base.user_model = User  # the class registry is weak: keep User alive

    classes_pkg = types.ModuleType("ruqqus.classes")
    classes_pkg.__path__ = [str(ROOT / "ruqqus" / "classes")]
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes_pkg)
    main = types.ModuleType("ruqqus.__main__")
    main.Base, main.cache, main.app = base, None, None  # mix_ins imports cache/app
    monkeypatch.setitem(sys.modules, "ruqqus.__main__", main)

    spec = importlib.util.spec_from_file_location(
        "ruqqus.classes.chat", ROOT / "ruqqus" / "classes" / "chat.py")
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ruqqus.classes.chat", mod)
    spec.loader.exec_module(mod)
    mod.Base = base
    return mod


@pytest.fixture
def session(chat_models):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    chat_models.Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _unread(models, session, cid, uid, n):
    session.add(models.ChatUnread(conversation_id=cid, user_id=uid, unread_count=n))


def test_total_sums_messages_across_conversations(chat_models, session):
    _unread(chat_models, session, 1, 1, 3)
    _unread(chat_models, session, 2, 1, 4)
    _unread(chat_models, session, 3, 1, 0)   # fully read: contributes nothing
    _unread(chat_models, session, 1, 2, 9)   # another user's row
    session.commit()
    total = chat_models.ChatUnread.total_for_user
    assert total(session, 1) == 7
    assert total(session, 2) == 9


def test_total_is_zero_without_rows(chat_models, session):
    assert chat_models.ChatUnread.total_for_user(session, 42) == 0


# ------------------------------------------------------------ the endpoint
@pytest.fixture
def chat_env(legacy, monkeypatch):
    """Load routes/chat.py onto the harness app with its heavy imports stubbed."""
    for name, attrs in {
        "ruqqus.helpers.chat_permissions": dict(
            can_message_directly=lambda *a: True, is_blocked=lambda *a: False),
        "ruqqus.helpers.secret_box": dict(
            encrypt_secret=lambda s: s, decrypt_secret=lambda s: s),
        "ruqqus.helpers.matrix_client": {},
    }.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location(
        "chat_under_test", ROOT / "ruqqus" / "routes" / "chat.py")
    chat = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "chat_under_test", chat)
    spec.loader.exec_module(chat)
    return legacy


def test_unread_count_returns_total_for_logged_in_user(chat_env):
    user = chat_env.db.insert(FakeUser(id=1, chat_unread_messages=7))
    chat_env.login(user)
    r = chat_env.client.get("/api/chat/unread_count")
    assert r.status_code == 200
    assert r.get_json() == {"unread": 7}
    assert "private" in r.headers.get("Cache-Control", "")


def test_unread_count_is_read_only(chat_env):
    user = chat_env.db.insert(FakeUser(id=1, chat_unread_messages=2))
    chat_env.login(user)
    chat_env.client.get("/api/chat/unread_count")
    assert chat_env.db.commits == 0 and chat_env.db.added == []


def test_unread_count_requires_login(chat_env):
    r = chat_env.client.get("/api/chat/unread_count")
    assert r.status_code != 200
    assert b"unread" not in r.data
