"""check_csam_url must persist the uploader's and alts' hold and the removal, through the one routine that holds them.

Regression test for `db.add(v)` with an undefined `v`, which raised NameError before db.commit() so a positive CSAM match never saved the
consequence (in `check_csam(post)`, which nothing called and the Log's slice D4 removed: every scan now goes through `check_csam_url`, whose caller
brings the content's own removal). Since the owner's decision of 2026-11-17 the consequence is a QUARANTINE, never a ban: the hold itself is
`scan_bans.quarantine_uploader` (-> `auto_quarantine.hold`, tested on SQLite in tests/test_scan_bans.py and on the real app in
tests/pg/test_scan_bans.py) and the removal is written to the Log (`action_log.record_image_check_removal`): both are replaced here by
fakes that record what they were asked, so this file checks the checks - what they hand over, in which session, that the notice is sent only
AFTER the commit, and that the whole thing is committed. Runs without Postgres, S3, Cloudflare or the app's heavy dependencies:
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
        self.quarantined_utc = 0
        self.quarantine_kind = 0
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

    asked = types.SimpleNamespace(holds=[], removals=[], tells=[])

    def quarantine_uploader(db, user, *, detail="", legal=False):
        """What `auto_quarantine.hold` does to the rows, in the session it was given; the Log's entries are the real routine's. Nobody is banned."""
        asked.holds.append({"db": db, "user": user, "detail": detail, "legal": legal})
        for account in [user] + list(user.alts_threaded(db)):
            account.quarantined_utc = 1_000_000
            account.quarantine_kind = 1
            db.add(account)

    def tell(db):
        """The notice goes after the commit: what had been committed when it was asked for is recorded."""
        asked.tells.append({"db": db, "committed": {key: dict(snap) for key, snap in db.committed.items()}, "closed": db.closed})

    stub("ruqqus.helpers.scan_bans", quarantine_uploader=quarantine_uploader, tell=tell)
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


def test_a_cdn_451_on_an_uploaded_picture_holds_through_the_routine_removes_the_content_and_commits_before_the_notice(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 451)

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    (hold,) = aws.asked.holds
    assert hold["user"] is db.rows[(User, 1)] and hold["legal"] is True and hold["db"] is db
    assert "days" not in hold and "451" in hold["detail"]
    assert removed == ["content"] and deleted == ["post/abc/xyz"]
    for uid in (1, 2):
        row = db.committed[(User, uid)]
        assert row["quarantine_kind"] == 1 and row["quarantined_utc"] > 0 and row["is_banned"] == 0          # held, never banned
    (told,) = aws.asked.tells
    assert told["db"] is db and told["closed"] is False                                       # the notice needs the session open...
    assert told["committed"][(User, 1)]["quarantine_kind"] == 1                              # ...and goes after the commit
    assert db.closed


def test_a_banned_list_match_on_an_uploaded_picture_holds_the_uploader_with_no_days_to_serve(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 200)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: types.SimpleNamespace(ban_reason="Spam picture", ban_time=2))

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    (hold,) = aws.asked.holds
    assert hold["legal"] is False and "Spam picture" in hold["detail"] and "days" not in hold
    assert removed == ["content"] and deleted == ["post/abc/xyz"]
    for uid in (1, 2):
        row = db.committed[(User, uid)]
        assert row["quarantine_kind"] == 1 and row["is_banned"] == 0
    assert len(aws.asked.tells) == 1 and aws.asked.tells[0]["committed"][(User, 1)]["quarantine_kind"] == 1


def test_a_limited_qr_sign_in_is_refused_the_picture_but_holds_nobody_on_the_lists_word(aws, monkeypatch):
    member, db, removed, deleted = url_case(aws, monkeypatch, 200, qr_limited=True)
    monkeypatch.setattr(aws, "check_phash", lambda db_, name: types.SimpleNamespace(ban_reason="Spam picture", ban_time=2))

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    assert aws.asked.holds == [] and removed == ["content"] and deleted == ["post/abc/xyz"]
    assert (User, 1) not in db.committed and (User, 2) not in db.committed


def test_the_cdns_451_holds_the_uploader_however_they_signed_in(aws, monkeypatch):
    """The one exception to a limited sign-in's protection (the owner, 2026-11-18): illegal content has reached the site by then."""
    member, db, removed, deleted = url_case(aws, monkeypatch, 451, qr_limited=True)

    aws.check_csam_url(URL, member, lambda: removed.append("content"))

    (hold,) = aws.asked.holds
    assert hold["legal"] is True and hold["user"] is db.rows[(User, 1)]
    assert removed == ["content"] and deleted == ["post/abc/xyz"]
    assert db.committed[(User, 1)]["quarantine_kind"] == 1 and db.committed[(User, 1)]["is_banned"] == 0
