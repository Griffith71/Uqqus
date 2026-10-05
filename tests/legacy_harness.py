"""Shared harness for characterization tests of the legacy Flask code.

Nothing here touches Postgres, Redis, the network or the real ORM models.
`build_legacy()` installs stub modules (ruqqus.__main__, ruqqus.classes,
ruqqus.helpers.get/alerts/sanitize, gevent) via monkeypatch, then imports the
REAL ruqqus.helpers.wrappers / security / base36 and loads the REAL
ruqqus/routes/votes.py against a minimal Flask app.

The fakes mimic only what the code under test uses:
* FakeDB.query(cls).filter(...)/filter_by(...)/first()/all()/count() over
  in-memory lists, with Col predicates (==, >, in_) standing in for SQLAlchemy
  column expressions;
* FakeUser replicates User.formkey / validate_formkey / is_suspended from
  ruqqus/classes/user.py (a test asserts the source still matches);
* FakeVote.change_to replicates Vote.change_to from ruqqus/classes/votes.py;
* ups/downs/score/rank_* on the fake post/comment are simplified stand-ins -
  their real SQL cannot be characterized without a database.
"""
import importlib
import importlib.util
import sys
import time
import types
from pathlib import Path

import flask
from flask import Flask, abort, g

ROOT = Path(__file__).resolve().parent.parent


class Col:
    """Stand-in for an SQLAlchemy column: comparisons build row predicates."""

    def __init__(self, name):
        self.name = name

    __hash__ = object.__hash__

    def __eq__(self, other):
        return lambda row: getattr(row, self.name) == other

    def __gt__(self, other):
        return lambda row: getattr(row, self.name) > other

    def in_(self, items):
        items = tuple(items)
        return lambda row: getattr(row, self.name) in items


class Row:
    defaults = {}

    def __init__(self, **kw):
        for k, v in self.defaults.items():
            setattr(self, k, v() if callable(v) else v)
        for k, v in kw.items():
            setattr(self, k, v)


class FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *preds):
        return FakeQuery(r for r in self.rows if all(p(r) for p in preds))

    def filter_by(self, **kw):
        return FakeQuery(
            r for r in self.rows
            if all(getattr(r, k) == v for k, v in kw.items()))

    def options(self, *a):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)

    def count(self):
        return len(self.rows)


class FakeDB:
    def __init__(self):
        self.tables = {}
        self.added = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.fail_flush = False

    def insert(self, obj):
        rows = self.tables.setdefault(type(obj), [])
        if obj not in rows:
            if getattr(obj, "id", None) is None:
                obj.id = len(rows) + 1
            rows.append(obj)
        return obj

    def query(self, cls):
        return FakeQuery(self.tables.get(cls, []))

    def add(self, obj):
        self.added.append(obj)
        self.insert(obj)

    def flush(self):
        self.flushes += 1
        if self.fail_flush:
            raise RuntimeError("simulated IntegrityError")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def rows(self, cls):
        return list(self.tables.get(cls, []))


# ---------------------------------------------------------------- fake models
class FakeClientAuth(Row):
    access_token = Col("access_token")
    access_token_expire_utc = Col("access_token_expire_utc")
    defaults = {"scope_vote": True}


class FakeApplication(Row):
    defaults = {"id": 77, "is_banned": False, "app_name": "TestApp"}


class FakeUser(Row):
    id = Col("id")
    is_deleted = Col("is_deleted")
    moderates = None
    subscriptions = None
    defaults = {
        "id": 1, "username": "alice", "is_banned": 0, "unban_utc": 0,
        "is_deleted": False, "login_nonce": 0, "admin_level": 0,
        "negative_balance_cents": 0, "ban_evade": 0, "alts": list,
        "moderates": list, "client": None, "ban_calls": list,
    }

    @property
    def is_suspended(self):  # ruqqus/classes/user.py
        return (self.is_banned and (self.unban_utc == 0
                                    or self.unban_utc > time.time()))

    @property
    def formkey(self):  # ruqqus/classes/user.py
        from ruqqus.helpers.security import generate_hash
        s = flask.session
        if "session_id" not in s:
            s["session_id"] = "generated-session-id"
        return generate_hash(f"{s['session_id']}+{self.id}+{self.login_nonce}")

    def validate_formkey(self, formkey):  # ruqqus/classes/user.py
        from ruqqus.helpers.security import validate_hash
        s = flask.session
        return validate_hash(
            f"{s['session_id']}+{self.id}+{self.login_nonce}", formkey)

    def ban(self, reason=None):
        self.ban_calls.append(reason)


class FakeVote(Row):
    user_id = Col("user_id")
    created_utc = Col("created_utc")
    vote_type = Col("vote_type")
    submission_id = Col("submission_id")
    defaults = {"created_utc": lambda: int(time.time()), "app_id": None,
                "creation_ip": None}

    def change_to(self, x):  # ruqqus/classes/votes.py
        if x in ["-1", "0", "1"]:
            x = int(x)
        elif x not in [-1, 0, 1]:
            abort(400)
        self.vote_type = x
        self.created_utc = int(time.time())


class FakeCommentVote(FakeVote):
    comment_id = Col("comment_id")


class _Voteable(Row):
    vote_cls = None
    fk = None
    defaults = {"id": 1, "is_blocking": False, "is_blocked": False,
                "is_banned": False, "deleted_utc": 0, "upvotes": 0,
                "downvotes": 0, "score_top": None, "board_id": 1,
                "author_id": 99}

    def _count(self, t):
        return sum(1 for r in g.db.rows(self.vote_cls)
                   if getattr(r, self.fk) == self.id and r.vote_type == t)

    @property
    def ups(self):
        return self._count(1)

    @property
    def downs(self):
        return self._count(-1)

    @property
    def score(self):
        return self.ups - self.downs


class FakeSubmission(_Voteable):
    vote_cls = FakeVote
    fk = "submission_id"
    author_id = Col("author_id")
    is_archived = False
    score_disputed = score_best = None
    rank_fiery = 111
    rank_best = 222


class FakeComment(_Voteable):
    vote_cls = FakeCommentVote
    fk = "comment_id"
    author_id = Col("author_id")
    score_hot = None
    rank_hot = 333


class FakeModAction(Row):
    pass


class FakeBoard(Row):
    defaults = {"id": 5, "name": "testguild", "mods": dict}

    def has_mod(self, user):
        if user is None:
            return None
        return self.mods.get(user.id)


class FakeMod(Row):
    defaults = {"perm_full": False}


# ------------------------------------------------------------------ the build
def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


def build_legacy(monkeypatch):
    monkeypatch.setenv("MASTER_KEY", "test-master-key")

    app = Flask("legacy-test")
    app.config["SERVER_NAME"] = "localhost"
    app.config["FORCE_HTTPS"] = 0
    app.secret_key = "test-secret"
    db = FakeDB()

    @app.before_request
    def _attach_db():
        g.db = db

    class _JL:
        def joinedload(self, *a):
            return self

    def get_post(pid, v=None, no_text=False, **kw):
        from ruqqus.helpers.base36 import base36decode
        row = g.db.query(FakeSubmission).filter_by(id=base36decode(pid)).first()
        if not row:
            abort(404)
        return row

    def get_comment(cid, v=None, no_text=False, **kw):
        from ruqqus.helpers.base36 import base36decode
        row = g.db.query(FakeComment).filter_by(id=base36decode(cid)).first()
        if not row:
            abort(404)
        return row

    def get_board(bid, **kw):
        row = g.db.query(FakeBoard).filter_by(id=int(bid)).first()
        if not row:
            abort(404)
        return row

    def get_guild(name, **kw):
        row = g.db.query(FakeBoard).filter_by(name=name).first()
        if not row:
            abort(404)
        return row

    notifications = []

    classes = _module(
        "ruqqus.classes",
        ClientAuth=FakeClientAuth, User=FakeUser, Vote=FakeVote,
        CommentVote=FakeCommentVote, Submission=FakeSubmission,
        Comment=FakeComment, ModAction=FakeModAction,
        ModRelationship=types.SimpleNamespace(board=None),
        Subscription=types.SimpleNamespace(board=None),
        joinedload=lambda *a: _JL())

    stubs = {
        "gevent": _module("gevent", sleep=lambda s: None),
        "ruqqus.__main__": _module(
            "ruqqus.__main__", Base=object, app=app, db_session=lambda: db),
        "ruqqus.classes": classes,
        "ruqqus.helpers.get": _module(
            "ruqqus.helpers.get", get_post=get_post, get_comment=get_comment,
            get_board=get_board, get_guild=get_guild),
        "ruqqus.helpers.alerts": _module(
            "ruqqus.helpers.alerts",
            send_notification=lambda *a, **k: notifications.append(a)),
        "ruqqus.helpers.sanitize": _module("ruqqus.helpers.sanitize"),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for name in ("ruqqus.helpers.wrappers", "ruqqus.helpers.security",
                 "ruqqus.helpers.session_helpers", "ruqqus.helpers.base36"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    wrappers = importlib.import_module("ruqqus.helpers.wrappers")

    spec = importlib.util.spec_from_file_location(
        "votes_under_test", ROOT / "ruqqus" / "routes" / "votes.py")
    votes = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "votes_under_test", votes)
    spec.loader.exec_module(votes)

    return Legacy(app, db, wrappers, votes, notifications)


class Legacy:
    def __init__(self, app, db, wrappers, votes, notifications):
        self.app = app
        self.db = db
        self.wrappers = wrappers
        self.votes = votes
        self.notifications = notifications
        self.client = app.test_client()

    def add_route(self, rule, view, methods=("GET", "POST")):
        self.app.add_url_rule(rule, endpoint=rule, view_func=view,
                              methods=list(methods))

    def add_user(self, **kw):
        return self.db.insert(FakeUser(**kw))

    def login(self, user, session_id="sid"):
        with self.client.session_transaction() as s:
            s["user_id"] = user.id
            s["login_nonce"] = user.login_nonce
            if session_id:
                s["session_id"] = session_id

    def formkey(self, user, session_id="sid"):
        from ruqqus.helpers.security import generate_hash
        return generate_hash(f"{session_id}+{user.id}+{user.login_nonce}")

    def add_token(self, user, token="tok", expires=None, application=None,
                  **kw):
        client = FakeClientAuth(
            access_token=token, user=user,
            access_token_expire_utc=expires or int(time.time()) + 3600,
            application=application or FakeApplication(), **kw)
        return self.db.insert(client)

    @staticmethod
    def bearer(token="tok"):
        return {"Authorization": f"Bearer {token}"}
