"""check_csam_url must persist the uploader's and alts' ban and the removal, through the one ban routine.

Regression test for `db.add(v)` with an undefined `v`, which raised NameError before db.commit() so a positive CSAM match never saved the
ban (in `check_csam(post)`, which nothing called and the Log's slice D4 removed: every scan now goes through `check_csam_url`, whose caller
brings the content's own removal). Since the Log's slice D2 the ban itself is `scan_bans.ban_uploader` (-> `User.ban`, tested on the real app in
tests/pg/test_scan_bans.py) and the removal is written to the Log (`action_log.record_image_check_removal`): both are replaced here by
fakes that record what they were asked, so this file checks the checks - what they hand over, in which session, and that the
whole thing is committed. Runs without Postgres, S3, Cloudflare or the app's heavy dependencies:
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
        self.board_id = 2
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
        parent, _, child = name.rpartition(".")
        if parent in sys.modules:        # `from package import module` finds the attribute first: point it at the stub too
            monkeypatch.setattr(sys.modules[parent], child, mod, raising=False)

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

    asked = types.SimpleNamespace(bans=[], removals=[])

    def ban_uploader(db, user, *, days=0, detail="", legal=False):
        """What User.ban does to the row, in the session it was given; the Log's entries are the real routine's."""
        asked.bans.append({"db": db, "user": user, "days": days, "detail": detail, "legal": legal})
        for account in [user] + list(user.alts_threaded(db)):
            account.is_banned = 1
            account.ban_reason = "Automatic image check"
            account.unban_utc = 1_000_000 + 86400 * days if days else 0
            db.add(account)

    stub("ruqqus.helpers.scan_bans", ban_uploader=ban_uploader)
    stub("ruqqus.helpers.action_log",
         record_image_check_removal=lambda db, **kw: asked.removals.append({"db": db, **kw}))
    monkeypatch.delitem(sys.modules, "ruqqus.helpers.aws", raising=False)

    module = importlib.import_module("ruqqus.helpers.aws")
    module.asked = asked
    yield module
    sys.modules.pop("ruqqus.helpers.aws", None)


# --- an uploaded picture checked at its address (check_csam_url) ---------------------------------------------------------------

def url_case(aws, monkeypatch, status_code, qr_limited=False):
    db_member = User(1, alt_ids=(2,))
    db = FakeSession({(User, 1): db_member, (User, 2): User(2)})
    member = User(1)                              # the request session's copy, which the scan never writes
    member._session = object()
    member.qr_limited = qr_limited
    removed, deleted = [], []
    monkeypatch.setattr(aws, "db_session", lambda: db)
    monkeypatch.setattr(aws, "delete_file", deleted.append)
    monkeypatch.setattr(aws.requests, "get", lambda *a, **k: Response(status_code))
    return member, db, removed, deleted


def test_a_cdn_451_on_an_uploaded_picture_bans_through_the_routine_removes_the_content_and_commits(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 451)

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    (ban,) = aws.asked.bans
    assert ban["user"] is db.rows[(User, 1)] and ban["legal"] is True and ban["days"] == 0 and ban["db"] is db
    assert removed == ["content"] and deleted == ["post/abc/xyz"]
    for uid in (1, 2):
        assert db.committed[(User, uid)]["is_banned"] == 1
    assert db.closed


def test_a_banned_list_match_on_an_uploaded_picture_suspends_for_the_lists_days(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 200)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: types.SimpleNamespace(ban_reason="Spam picture", ban_time=2))

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    (ban,) = aws.asked.bans
    assert ban["days"] == 2 and ban["legal"] is False and "Spam picture" in ban["detail"]
    assert removed == ["content"] and deleted == ["post/abc/xyz"]


def test_a_limited_qr_sign_in_is_refused_the_picture_but_bans_nobody_on_the_lists_word(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 200, qr_limited=True)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: types.SimpleNamespace(ban_reason="Spam picture", ban_time=2))

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    assert aws.asked.bans == [] and removed == ["content"] and deleted == ["post/abc/xyz"]
