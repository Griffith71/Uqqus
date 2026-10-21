"""Tests that need a real Postgres (the other tests run on SQLite with the app stubbed). Run them with

    PG_TEST_DATABASE_URL=postgresql://user@host/ci_site   a database loaded from schema.sql and seed-db.sql
    REDIS_URL=redis://localhost                           for /readyz and the rate limiter

CI does this with service containers (.github/workflows/ci.yml). Locally, in the dev stack:

    docker compose exec postgres psql -U postgres -c 'CREATE DATABASE ci_site'
    docker compose exec -T postgres psql -U postgres -d ci_site -v ON_ERROR_STOP=1 -q < schema.sql   (and seed-db.sql)

The variable is not `DATABASE_URL` on purpose: inside the dev container that one names the real dev database, and the
migration test runs every migration file again. A database whose name does not start with `ci_` or `test` is refused.
These tests import the real app, which monkey-patches the standard library, so CI runs them in a process of their own
(`pytest tests/pg`) and the ordinary run ignores this folder.
"""
import os
from urllib.parse import urlparse

import pytest

URL = os.environ.get("PG_TEST_DATABASE_URL", "")


def pytest_ignore_collect(collection_path, config):
    return not URL and collection_path.parent.name == "pg" and collection_path.name != "conftest.py"


def database_name(url):
    return urlparse(url).path.lstrip("/")


@pytest.fixture(scope="session")
def pg_url():
    assert URL, "PG_TEST_DATABASE_URL is not set"
    name = database_name(URL)
    assert name.startswith(("ci_", "test")), f"refusing to run against database {name!r}: its name must start with ci_ or test"
    return URL


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    from sqlalchemy import create_engine, text
    engine = create_engine(pg_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        loaded = conn.execute(text("SELECT to_regclass('public.schema_migrations') IS NOT NULL")).scalar()
    assert loaded, "the database is empty: load schema.sql and seed-db.sql first (see tests/pg/conftest.py)"
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def real_app(pg_url):
    """The real Flask app, bound to the test database. Importing it monkey-patches the standard library."""
    os.environ["DATABASE_URL"] = pg_url
    os.environ["DATABASE_CONNECTION_POOL_URL"] = pg_url
    os.environ.setdefault("MASTER_KEY", "ci-master-key-" + "x" * 30)
    os.environ.setdefault("domain", "localhost")
    os.environ.setdefault("CHECK_MIGRATIONS", "0")
    from ruqqus.__main__ import app, db_session, r
    forget_leftovers(r)
    return app, db_session


def forget_leftovers(r):
    """Redis outlives a database made again from schema.sql, and user ids start over: what an earlier run left under an
    id or an address (posting and sign-in heat, "already emailed today", spent authenticator codes) would answer for
    an account that is not the same one. Cleared once, when the app is first imported. In CI Redis is new anyway."""
    if r is None:
        return
    for pattern in ("throttle:*", "signin_alert:*", "totp:*"):
        for key in r.scan_iter(pattern):
            r.delete(key)


class Member:
    """An account made for a test, and the browsers it is signed in on."""

    def __init__(self, app, db_session, user, password):
        self.app, self.db_session, self.id, self.username, self.password = app, db_session, user.id, user.username, password

    def user(self):
        """The row as the database has it now (a fresh read: routes change it in their own sessions)."""
        from ruqqus.classes import User
        db = self.db_session()
        db.expire_all()
        return db.query(User).filter_by(id=self.id).first()

    def browser(self):
        """A new test client signed in as this account, as the login route leaves a browser."""
        import secrets
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.id
            session["login_nonce"] = self.user().login_nonce
            session["session_id"] = secrets.token_hex(16)
        return client

    @staticmethod
    def formkey(client):
        """The form key a page drawn for this browser would carry (made from its session id and the account's nonce)."""
        from ruqqus.helpers.security import generate_hash
        with client.session_transaction() as session:
            return generate_hash(f"{session['session_id']}+{session['user_id']}+{session['login_nonce']}")


@pytest.fixture
def make_member(real_app):
    """A factory: `make_member()` is a new, 90-day-old account with a known password."""
    import secrets
    import time
    app, db_session = real_app
    from ruqqus.classes import User

    def make(username=None, password="ci-Password-1"):
        username = username or "ci_" + secrets.token_hex(4)
        now = int(time.time())
        db = db_session()
        user = User(username=username, original_username=username, password=password, email=f"{username}@example.com",
                    created_utc=now - 90 * 86400, creation_ip="127.0.0.1", tos_agreed_utc=now - 90 * 86400,
                    following_visibility=0, followers_visibility=0, subscribers_visibility=0, subscriptions_visibility=0)
        db.add(user)
        db.commit()
        return Member(app, db_session, user, password)
    return make
