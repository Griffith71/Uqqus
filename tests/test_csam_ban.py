"""check_csam must persist the author/alt ban and the post removal.

Regression test for `db.add(v)` with an undefined `v` in check_csam(), which
raised NameError before db.commit() so a positive CSAM match never saved the
ban. Runs without Postgres, S3, Cloudflare or the app's heavy dependencies:
those modules are replaced by stubs, and db_session() by a fake session that
behaves like SQLAlchemy in the two ways that matter here - only state that was
add()ed and commit()ed counts, and add() refuses objects owned by another
session (the caller's request session).
"""
import importlib
import sys
import types

import pytest


class FakeSession:
    def __init__(self, rows):
        self.rows = rows  # {(cls, id): object} owned by this session
        for obj in rows.values():
            obj._session = self
        self.pending = []
        self.committed = {}  # {(cls, id): snapshot of attributes}
        self.closed = False

    def get(self, cls, ident):
        return self.rows.get((cls, ident))

    def add(self, obj):
        owner = getattr(obj, "_session", None)
        if owner is not None and owner is not self:
            raise RuntimeError("Object is already attached to another session")
        obj._session = self
        self.pending.append(obj)

    def commit(self):
        for obj in self.pending:
            snap = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
            self.committed[(type(obj), obj.id)] = snap
        self.pending = []

    def close(self):
        self.closed = True


class User:
    def __init__(self, id, alt_ids=()):
        self.id = id
        self.is_banned = 0
        self.ban_reason = None
        self.unban_utc = 0
        self._alt_ids = alt_ids

    def alts_threaded(self, db):
        return [db.rows[(User, i)] for i in self._alt_ids]


class Submission:
    def __init__(self, id, author, url):
        self.id = id
        self.author_id = author.id
        self.author = author
        self.url = url
        self.base36id = "abc"
        self.is_banned = False


class Response:
    def __init__(self, status_code):
        self.status_code = status_code

    def iter_content(self, size):
        yield b"image-bytes"


BUCKET = "i.example.test"
URL = f"https://{BUCKET}/post/abc/xyz"


@pytest.fixture
def aws(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # phash path writes a temp file to cwd
    for key in ("CLOUDFLARE_KEY", "CLOUDFLARE_ZONE",
                "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)

    def stub(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)

    stub("boto3", client=lambda *a, **k: object())
    stub("piexif", remove=lambda *a: None)
    stub("PIL", Image=types.SimpleNamespace(open=None))
    stub("PIL.Image", open=None)
    stub("imagehash", phash=None)
    stub("sqlalchemy", func=None)
    stub("gevent")
    stub("ruqqus.__main__", db_session=None)
    stub("ruqqus.classes.images", BadPic=None)
    stub("ruqqus.classes.user", User=User)
    stub("ruqqus.classes.submission", Submission=Submission)
    stub("ruqqus.helpers.base36", hex2bin=lambda h: h)
    monkeypatch.delitem(sys.modules, "ruqqus.helpers.aws", raising=False)

    module = importlib.import_module("ruqqus.helpers.aws")
    yield module
    sys.modules.pop("ruqqus.helpers.aws", None)


def setup_case(aws, monkeypatch, status_code):
    # Rows as loaded by check_csam's own session ...
    db_author = User(1, alt_ids=(2, 3))
    db_post = Submission(10, db_author, URL)
    db = FakeSession({
        (User, 1): db_author,
        (User, 2): User(2),
        (User, 3): User(3),
        (Submission, 10): db_post,
    })
    # ... and the caller's copies, still attached to the request session.
    caller_session = object()
    post = Submission(10, User(1, alt_ids=(2, 3)), URL)
    post._session = post.author._session = caller_session

    deleted = []
    monkeypatch.setattr(aws, "db_session", lambda: db)
    monkeypatch.setattr(aws, "delete_file", deleted.append)
    monkeypatch.setattr(aws.requests, "get",
                        lambda *a, **k: Response(status_code))
    return post, db, deleted


def test_cloudflare_451_ban_is_committed(aws, monkeypatch):
    post, db, deleted = setup_case(aws, monkeypatch, 451)

    aws.check_csam(post)

    for uid in (1, 2, 3):
        row = db.committed[(User, uid)]
        assert row["is_banned"] == 1
        assert row["ban_reason"] == "Sexualizing Minors"
    assert db.committed[(Submission, 10)]["is_banned"] is True
    assert deleted == ["post/abc/xyz"]
    assert db.closed


def test_phash_match_ban_is_committed(aws, monkeypatch):
    post, db, deleted = setup_case(aws, monkeypatch, 200)
    bad = types.SimpleNamespace(ban_reason="Known CSAM hash", ban_time=3)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: bad)
    monkeypatch.setattr(aws.time, "time", lambda: 1_000_000)

    aws.check_csam(post)

    unban = 1_000_000 + 60 * 60 * 24 * 3
    for uid in (1, 2, 3):
        row = db.committed[(User, uid)]
        assert row["is_banned"] == 1
        assert row["ban_reason"] == "Known CSAM hash"
        assert row["unban_utc"] == unban
    assert db.committed[(Submission, 10)]["is_banned"] is True
    assert deleted == ["post/abc/xyz"]
    assert db.closed


def test_phash_permanent_ban_has_no_unban_time(aws, monkeypatch):
    post, db, _ = setup_case(aws, monkeypatch, 200)
    bad = types.SimpleNamespace(ban_reason="Known CSAM hash", ban_time=0)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: bad)

    aws.check_csam(post)

    for uid in (1, 2, 3):
        assert db.committed[(User, uid)]["unban_utc"] == 0
        assert db.committed[(User, uid)]["is_banned"] == 1


def test_no_match_bans_nobody(aws, monkeypatch):
    post, db, deleted = setup_case(aws, monkeypatch, 200)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: None)

    aws.check_csam(post)

    assert db.committed == {}
    assert deleted == []
