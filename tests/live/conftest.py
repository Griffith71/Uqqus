"""Tests against a running site (audit T-01, T-02): the real routes, Postgres, Redis and templates, which the rest of
the suite fakes. Skipped unless LIVE_BASE_URL is set; they write posts, tips and edits, so run them only against a
development database you can restore (scripts/backup.sh).

    LIVE_BASE_URL=http://localhost LIVE_USER_A=alice LIVE_USER_B=bob LIVE_PASSWORD=... \\
        .venv/bin/python -m pytest -q tests/live

User A posts and edits; user B tips A. B needs a few coins and both need to be past the posting throttle. With
TRUSTED_PROXY_HOPS unset the site rate-limits by the real address, so sign-ins are spaced out.
"""
import json
import os
import re
import tempfile
import time

import pytest
import requests

BASE = os.environ.get("LIVE_BASE_URL", "").rstrip("/")


def pytest_collection_modifyitems(config, items):
    if BASE:
        return
    skip = pytest.mark.skip(reason="LIVE_BASE_URL is not set")
    for item in items:
        if "tests/live" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


class Member:
    def __init__(self, username, password):
        self.username = username
        self.s = requests.Session()
        # a signed-in session is kept between runs: sign-ins are rate limited per address
        jar = os.path.join(tempfile.gettempdir(), f"ruqqus_live_{username}.json")
        if os.path.exists(jar):
            self.s.cookies.update(json.load(open(jar)))
            page = self.s.get(f"{BASE}/settings/profile", allow_redirects=False)
            if page.status_code == 200:
                self.formkey = re.search(r'name="formkey" value="([^"]+)"', page.text).group(1)
                return
        # sign-ins are 6 a minute per address, and 30 refusals in a row ban the address for an hour (routes/errors.py),
        # so a refusal is waited out in full rather than retried quickly
        for _ in range(3):
            r = self.s.post(f"{BASE}/login", data={"username": username, "password": password}, allow_redirects=False)
            if r.status_code != 429:
                break
            time.sleep(65)
        assert r.status_code in (301, 302), f"sign-in as {username}: {r.status_code}"
        json.dump(self.s.cookies.get_dict(), open(jar, "w"))
        page = self.s.get(f"{BASE}/settings/profile").text
        self.formkey = re.search(r'name="formkey" value="([^"]+)"', page).group(1)

    def get(self, path, **kw):
        return self.s.get(BASE + path, allow_redirects=False, **kw)

    def post(self, path, data=None, **kw):
        data = dict(data or {})
        data.setdefault("formkey", self.formkey)
        return self.s.post(BASE + path, data=data, allow_redirects=False, **kw)


@pytest.fixture(scope="session")
def alice():
    return Member(os.environ["LIVE_USER_A"], os.environ["LIVE_PASSWORD"])


@pytest.fixture(scope="session")
def bob():
    return Member(os.environ["LIVE_USER_B"], os.environ["LIVE_PASSWORD"])
