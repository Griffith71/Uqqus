"""The site's own count of requests for pages (helpers/traffic.py, helpers/traffic_store.py): what counts, the bounds an
attacker can't get past in Redis or the table, a flush that fails half way, and the promise that nothing says who. The real
app is tests/pg/test_traffic.py and the browser scripts/ui/traffic.spec.ts."""
import json
import random
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from fake_redis import FakeRedis
from ruqqus.helpers import retention
from ruqqus.helpers import traffic as rules
from ruqqus.helpers import traffic_store as store

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
NOW = 1_800_000_000 + 12 * 3600          # in the middle of a UTC day
DAY = 86400
TODAY = NOW // DAY
OWN = {"laba.example", "go.laba.example"}
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
MIGRATION = ROOT / "scripts" / "migrations" / "2026-12-12_site_traffic.sql"
NOT_PAGES = json.loads((ROOT / "tests" / "data" / "traffic_not_pages.json").read_text(encoding="utf-8"))["endpoints"]
# crawlers that plainly say what they are, as they say it: each of these once reached the count (the fediverse's previews fan
# out, one shared link being a request per server)
CRAWLERS = [
    "Mediapartners-Google", "AdsBot-Google (+http://www.google.com/adsbot.html)", "GoogleOther", "APIs-Google (+https://developers.google.com/webmasters/APIs-Google.html)",
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36 (compatible; Google-InspectionTool/1.0;)",
    "Mozilla/5.0 (Linux; Android 11; Pixel 2; DuplexWeb-Google/1.0) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36 Google-Read-Aloud",
    "Mastodon/4.2.0 (http.rb/5.1.1; +https://mastodon.social/)", "Pleroma 2.6.1; https://pleroma.example <admin@pleroma.example>", "Akkoma 3.10.0; https://akkoma.example",
    "Misskey/2024.2.0 (https://misskey.example)", "GoToSocial/0.15.0 (+https://gts.example)", "Friendica 'Yellow Archangel' 2024.03", "Lemmy/0.19.3; +https://lemmy.example",
    "Pixelfed/0.11.13 (https://pixelfed.example)", "PeerTube/6.0.4 (+https://peertube.example)", "Firefish/1.0.5", "Sharkey/2024.3",
    "Bluesky Cardyb/1.1", "Iframely/1.3.1 (+https://iframely.com/docs/about)", "Feedly/1.0", "ia_archiver", "StatusCake", "Site24x7", "GTmetrix",
    "Cloudflare-Healthchecks/1.0", "Cloudflare-AlwaysOnline/1.0", "Scrapy/2.11.0 (+https://scrapy.org)", "Mozilla/4.5 (compatible; HTTrack 3.0x; Windows 98)",
    "axios/1.6.8", "python-httpx/0.27.0", "ChatGPT-User", "Claude-User", "Perplexity-User",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
]
BROWSERS = [
    BROWSER,
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 Edg/120.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/300.0.598 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 320.0.0.34.109 (iPhone14,5; iOS 17_1; en_GB; en; scale=3.00; 1170x2532; 547912345)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/445.0.0.31.113;FBBV/552020540;FBDV/iPhone14,5]",
    "Mozilla/5.0 (Linux; Android 13; SM-A536B; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0 Mobile Safari/537.36 musical_ly_2023",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def hit(method="GET", endpoint="post_base36id_with_comments", path="/post/abc", status=200, mimetype="text/html",
        embedded=False, **headers):
    sent = {"User-Agent": BROWSER, **{k.replace("_", "-"): v for k, v in headers.items()}}
    return rules.hit_of(method, endpoint, path, status, mimetype, embedded, sent, OWN, NOW)


# ---- what counts ----

def test_a_real_page_counts_and_says_only_four_things():
    assert hit(Referer="https://news.example/a/b?c=d", CF_IPCountry="GB") == (TODAY, "post", "news.example", "GB")
    assert rules.Hit._fields == ("day", "kind", "referrer", "country")


def test_an_allowlist_and_not_an_exclude_list():
    assert hit(endpoint="a_route_added_tomorrow") is None
    assert hit(endpoint=None) is None


@pytest.mark.parametrize("endpoint", NOT_PAGES)
def test_nothing_that_polls_redirects_or_is_a_fragment_is_on_the_list(endpoint):
    assert endpoint not in rules.ENDPOINT_KINDS
    routes = "".join(p.read_text(encoding="utf-8") for p in (APP / "routes").glob("*.py"))
    assert re.search(rf"\bdef {endpoint}\(", routes), f"{endpoint} is no route: this case would pin nothing"


def test_the_wikis_front_door_counts():
    assert rules.ENDPOINT_KINDS["guild_wiki_index"] == "wiki"     # the Wiki tab links to it, and it answers the page `index`


def test_only_a_successful_get_of_html_counts():
    assert hit(method="HEAD") is None
    assert hit(method="POST") is None
    for status in (301, 302, 304, 403, 404, 429, 500):
        assert hit(status=status) is None
    assert hit(mimetype="application/json") is None


def test_one_endpoint_answering_at_the_api_or_a_fragment_address_does_not_count():
    for path in ("/api/v1/all/listing", "/api/v2/submissions", "/inpage/all", "/admin/x", "/assets/x", "/embed/post/1"):
        assert hit(endpoint="front_all", path=path) is None
    assert hit(endpoint="front_all", path="/all")


def test_a_frame_and_a_script_fetching_a_page_do_not_count():
    assert hit(embedded=True) is None
    assert hit(X_Background_Fetch="1") is None
    assert hit(Sec_Fetch_Dest="empty") is None
    assert hit(Sec_Fetch_Dest="iframe") is None
    assert hit(Sec_Fetch_Dest="document")


def test_prefetches_and_robots_do_not_count():
    assert hit(Purpose="prefetch") is None
    assert hit(Sec_Purpose="prefetch;prerender") is None
    for agent in ("Googlebot/2.1", "curl/8.0", "", "python-requests/2.3", "facebookexternalhit/1.1"):
        assert hit(User_Agent=agent) is None


@pytest.mark.parametrize("agent", CRAWLERS)
def test_a_crawler_that_says_what_it_is_does_not_count(agent):
    assert hit(User_Agent=agent) is None


@pytest.mark.parametrize("agent", BROWSERS)
def test_a_person_in_a_browser_or_an_apps_own_browser_counts(agent):
    assert hit(User_Agent=agent)


def test_the_homes_kind_says_it_is_the_home_page_too():
    """`/` and `/for_you` are one endpoint and a visitor's `/` is All: the row can't be called For You alone."""
    assert rules.ENDPOINT_KINDS["for_you"] == "for_you" and rules.KIND_LABELS["for_you"] == "Home and For You"


def test_days_are_utc():
    assert rules.hit_of("GET", "for_you", "/", 200, "text/html", False, {"User-Agent": BROWSER}, OWN, TODAY * DAY - 1).day == TODAY - 1
    assert rules.hit_of("GET", "for_you", "/", 200, "text/html", False, {"User-Agent": BROWSER}, OWN, TODAY * DAY).day == TODAY


# ---- country and referrer ----

@pytest.mark.parametrize("raw,want", [
    ("GB", "GB"), ("US", "US"), ("T1", "unknown"), ("XX", "unknown"), ("gb", "unknown"), ("GBR", "unknown"), ("G", "unknown"),
    ("", "unknown"), (None, "unknown"), ("GB\n", "unknown"), ("ÉÉ", "unknown"), ("G1", "unknown"), ("<b", "unknown"),
])
def test_a_country_is_two_capital_letters_or_unknown(raw, want):
    assert rules.country_of(raw) == want


@pytest.mark.parametrize("raw,want", [
    (None, "direct"), ("", "direct"), ("   ", "direct"),
    ("https://laba.example/post/1?x=y", "internal"), ("http://www.laba.example/", "internal"),
    ("https://go.laba.example/abc", "internal"), ("https://LABA.example:8443/", "internal"),
    ("https://news.example/secret/path?token=abc#frag", "news.example"), ("https://WWW.News.Example.:443/", "news.example"),
    ("https://alice:hunter2@news.example/", "news.example"), ("https://sub.news.example/", "sub.news.example"),
    ("https://xn--bcher-kva.example/", "xn--bcher-kva.example"),
    ("https://203.0.113.5/", "other"), ("http://[2001:db8::1]/", "other"), ("https://1.1/", "other"), ("https://localhost/", "other"),
    ("android-app://com.reddit.frontpage/", "other"), ("about:blank", "other"), ("ftp://news.example/", "other"), ("-", "other"),
    ("news.example", "other"), ("http://[bad", "other"), ("https://bücher.example/", "other"), ("https://exa_mple.com/", "other"),
    ("https://-bad.example/", "other"), ("https://a" + "b" * 120 + ".example/", "other"), ("https://news.example/" + "x" * 3000, "other"),
    ("javascript:alert(1)", "other"), ("https://<script>.example/", "other"), ("https://%6e%65%77%73.example/", "other"),
    # an address inside a name is still somebody's address, and a private name is somebody's machine
    ("https://1.2.3.4.nip.io/", "other"), ("https://203-0-113-5.sslip.io/", "other"), ("https://app.c0a80101.nip.io/", "other"), ("https://2a01-4f8-c17-b8f--2.sslip.io/", "other"),
    ("https://host-203-0-113-5.isp.example/", "other"), ("https://ip-10-0-0-1.ec2.example/", "other"), ("https://x.1.2.3.4.example/", "other"),
    ("https://alice.local/", "other"), ("https://my-laptop.home.arpa/", "other"), ("https://nas.lan/", "other"), ("https://wiki.internal/", "other"), ("https://wiki.intranet/", "other"),
    ("https://dev.localhost/", "other"), ("https://a.test/", "other"), ("https://a.invalid/", "other"), ("https://pc.localdomain/", "other"), ("https://pc.home/", "other"), ("https://pc.corp/", "other"),
    # and what only looks like one stays a site
    ("https://1password.com/", "1password.com"), ("https://24-7.example/", "24-7.example"), ("https://web3.example/", "web3.example"), ("https://local.example/", "local.example"),
    ("https://1-800-flowers.com/", "1-800-flowers.com"), ("https://192.example/", "192.example"),
])
def test_a_referrer_is_a_normalised_host_and_nothing_else(raw, want):
    assert rules.referrer_of(raw, OWN) == want


def test_no_answer_ever_holds_a_path_a_query_or_a_long_value():
    for _ in range(2000):
        raw = "https://" + "".join(random.choices("abcdefghij.-_/?#:@%[]", k=random.randint(0, 200)))
        answer = rules.referrer_of(raw, OWN)
        assert len(answer) <= rules.MAX_HOST and not set(answer) & set("/?#:@%[]_ ")


# ---- the table, built from the migration ----

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in re.sub(r"--.*", "", MIGRATION.read_text(encoding="utf-8")).split(";"):
            if statement.strip():
                conn.execute(text(statement.replace("public.", "")))
        conn.execute(text("CREATE TABLE users (id integer primary key, created_utc integer, is_bot boolean)"))
        conn.execute(text("CREATE TABLE submissions (id integer primary key, created_utc integer, repost_id integer, is_space boolean, is_bot boolean)"))
        conn.execute(text("CREATE TABLE comments (id integer primary key, created_utc integer, parent_submission integer, is_bot boolean)"))
        conn.execute(text("CREATE TABLE comment_forwardrels (id integer primary key, promoted_submission_id integer)"))
    with Session(engine) as session:
        yield session


def rows(db, dimension, day=TODAY):
    return {v: n for v, n in db.execute(text("SELECT value, requests FROM site_traffic WHERE day=:d AND dimension=:x"),
                                        {"d": day, "x": dimension}).all()}


def H(kind="post", referrer="direct", country="GB", day=TODAY):
    return rules.Hit(day, kind, referrer, country)


def test_a_flush_moves_counts_into_the_table_and_empties_redis(db):
    r = FakeRedis()
    store.record(r, [H(), H(referrer="internal"), H(kind="all", referrer="news.example", country="unknown"),
                     H(referrer="news.example"), H(referrer="other")])
    assert store.flush(db, r, NOW) == 5
    assert rows(db, "kind") == {"post": 4, "all": 1}
    assert rows(db, "country") == {"GB": 4, "unknown": 1}
    assert rows(db, "referrer") == {"direct": 1, "internal": 1, "news.example": 2, "other": 1}
    assert r.data == {}
    assert store.flush(db, r, NOW) == 0                 # nothing new: nothing changes
    assert rows(db, "kind") == {"post": 4, "all": 1}


def test_flushes_add_up(db):
    r = FakeRedis()
    store.record(r, [H(referrer="a.example")])
    store.flush(db, r, NOW)
    store.record(r, [H(referrer="a.example"), H(referrer="b.example")])
    store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 3}
    assert rows(db, "referrer") == {"a.example": 2, "b.example": 1}


def test_each_day_goes_to_its_own_day_and_a_job_down_for_days_catches_up(db):
    r = FakeRedis()
    store.record(r, [H(day=TODAY - 3), H(day=TODAY - 3), H(day=TODAY)])
    store.flush(db, r, NOW)
    assert rows(db, "kind", TODAY - 3) == {"post": 2} and rows(db, "kind") == {"post": 1}


def test_what_arrives_during_a_flush_is_not_lost(db):
    r = FakeRedis()
    store.record(r, [H()])
    real_hgetall = r.hgetall

    def arrives_meanwhile(key):
        store.record(r, [H(kind="all")])
        r.hgetall = real_hgetall
        return real_hgetall(key)
    r.hgetall = arrives_meanwhile
    store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 1}
    store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 1, "all": 1}


# ---- the bounds, against headers chosen by an attacker ----

def random_hits(n):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i in range(n):
        referer = f"https://{''.join(random.choices('abcdefghijklmnop', k=12))}.example/{i}"
        country = "".join(random.choices(letters + "0129ab", k=random.choice((1, 2, 2, 2, 3))))
        got = rules.hit_of("GET", "for_you", "/", 200, "text/html", False,
                           {"User-Agent": BROWSER, "Referer": referer, "CF-IPCountry": country}, OWN, NOW)
        yield got


def test_random_headers_cannot_grow_redis_between_flushes():
    r = FakeRedis()
    hits = list(random_hits(5000))
    for start in range(0, len(hits), 50):
        store.record(r, hits[start:start + 50])
    assert len(r.data[store.live_key(TODAY, store.HOSTS)]) <= rules.LIVE_HOSTS + 1
    assert len(r.data[store.live_key(TODAY, store.FIXED)]) <= len(rules.KIND_LABELS) + 26 * 26 + 1 + 2


def test_random_headers_cannot_grow_the_table_and_no_count_is_lost(db):
    r = FakeRedis()
    for _ in range(4):                                  # four flushes' worth, each full
        hits = list(random_hits(1500))
        for start in range(0, len(hits), 50):
            store.record(r, hits[start:start + 50])
        store.flush(db, r, NOW)
    referrers = rows(db, "referrer")
    assert len(referrers) <= rules.DAILY_HOSTS + 1      # the hosts and `other` (nothing here was direct or internal)
    assert sum(referrers.values()) == 6000 == sum(rows(db, "kind").values()) == sum(rows(db, "country").values())
    assert len(rows(db, "country")) <= 26 * 26 + 1


def test_a_real_site_outlasts_a_flood_of_names_seen_once(db):
    r = FakeRedis()
    store.record(r, [H(referrer="real.example")] * 40)
    store.record(r, [h for h in random_hits(3000)])
    store.flush(db, r, NOW)
    assert rows(db, "referrer")["real.example"] == 40


def test_merging_keeps_the_largest_and_never_changes_a_days_total():
    existing = {"direct": 5, "other": 1, **{f"s{i}.example": 1 for i in range(5)}}
    merged = rules.merge_referrers(existing, {"new.example": 9, "direct": 1}, cap=3)
    assert merged["new.example"] == 9 and merged["direct"] == 6
    assert len([v for v in merged if v.endswith(".example")]) == 3
    assert sum(merged.values()) == sum(existing.values()) + 10


# ---- a flush that fails half way ----

class FailsAt:
    """A session that raises on the nth write, as a database going away does."""

    def __init__(self, session, nth):
        self.session, self.left = session, nth

    def execute(self, statement, *args, **kwargs):
        if str(statement).startswith(("INSERT", "DELETE")):
            self.left -= 1
            if self.left <= 0:
                raise RuntimeError("the database went away")
        return self.session.execute(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.session, name)


@pytest.mark.parametrize("nth", [1, 2, 3, 5])
def test_a_database_failure_loses_nothing_and_counts_nothing_twice(db, nth):
    r = FakeRedis()
    store.record(r, [H(), H(referrer="a.example"), H(kind="all", country="US")])
    with pytest.raises(RuntimeError):
        store.flush(FailsAt(db, nth), r, NOW)
    assert rows(db, "kind") == {} and db.execute(text("SELECT COUNT(*) FROM site_traffic_flushes")).scalar() == 0
    store.record(r, [H(kind="all")])                     # keeps counting meanwhile, into a new key
    store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 2, "all": 1}     # the batch that failed, once
    store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 2, "all": 2}     # then what came after it
    assert r.data == {}


def test_redis_failing_after_the_commit_does_not_count_twice(db):
    r = FakeRedis()
    store.record(r, [H(), H()])
    real_delete = r.delete
    calls = []

    def breaks_on_tidying(*keys):
        if any("held" in key for key in keys) and not calls:
            calls.append(1)
            raise ConnectionError("redis went away")
        return real_delete(*keys)
    r.delete = breaks_on_tidying
    with pytest.raises(ConnectionError):
        store.flush(db, r, NOW)
    assert rows(db, "kind") == {"post": 2}
    assert r.get(store.POINTER)                          # the batch is still aside, and in the ledger
    store.record(r, [H(kind="all")])                     # must not be moved into the batch that is about to be deleted
    assert store.flush(db, r, NOW) == 1
    assert rows(db, "kind") == {"post": 2, "all": 1}
    assert r.data == {}


def test_redis_failing_while_moving_keys_carries_on_next_time(db):
    r = FakeRedis()
    store.record(r, [H(day=TODAY - 1), H()])
    real = r.renamenx
    seen = []

    def breaks_second_time(a, b):
        seen.append(a)
        if len(seen) == 2:
            raise ConnectionError("redis went away")
        return real(a, b)
    r.renamenx = breaks_second_time
    with pytest.raises(ConnectionError):
        store.flush(db, r, NOW)
    r.renamenx = real
    assert not r.get(store.FLUSH_LOCK)                   # the lock doesn't stay behind
    assert store.flush(db, r, NOW) == 2
    assert rows(db, "kind", TODAY - 1) == {"post": 1} and rows(db, "kind") == {"post": 1}


def test_a_second_flush_at_the_same_moment_does_nothing(db):
    r = FakeRedis()
    store.record(r, [H()])
    r.set(store.FLUSH_LOCK, "1")
    assert store.flush(db, r, NOW) is None
    assert rows(db, "kind") == {} and store.live_key(TODAY, store.FIXED) in r.data


def test_counts_read_as_bytes_too():
    assert store.counts_of({b"k:post": b"2", b"c:GB": b"2"}, [(b"a.example", 1.0), (b"*", 3.0)]) == {
        "kind": {"post": 2}, "country": {"GB": 2}, "referrer": {"a.example": 1, "other": 2}}


# ---- the report ----

def test_the_report_fills_every_day_and_sits_beside_the_sites_own_tables(db):
    r = FakeRedis()
    store.record(r, [H(), H(referrer="a.example", country="US"), H(day=TODAY - 2, kind="all")])
    store.flush(db, r, NOW)
    db.execute(text("INSERT INTO users VALUES (1, :t, 0), (2, :t, 1), (3, :old, 0)"), {"t": NOW - 5, "old": NOW - 400 * DAY})
    db.execute(text("INSERT INTO submissions VALUES (1, :t, NULL, 0, 0), (2, :t, 1, 0, 0), (3, :t, 0, 1, 0), (4, :t, 0, 0, 1), (5, :y, 0, 0, 0)"),
               {"t": NOW - 5, "y": NOW - DAY})
    db.execute(text("INSERT INTO comments VALUES (1, :t, 1, 0), (2, :t, NULL, 0)"), {"t": NOW - 5})
    # a forwarded comment is a post of its own with no repost_id: the same words are already under Comments
    db.execute(text("INSERT INTO submissions VALUES (6, :t, 0, 0, 0)"), {"t": NOW - 5})
    db.execute(text("INSERT INTO comment_forwardrels VALUES (1, 6)"))
    db.commit()
    data = store.report(db, NOW, 7)
    assert [row["day"] for row in data["series"]] == list(range(TODAY - 6, TODAY + 1))
    assert data["requests"] == 3 and data["signups"] == 1 and data["posts"] == 2 and data["comments"] == 1
    assert data["series"][-1]["requests"] == 2 and data["series"][-1]["height"] == 100 and data["series"][-3]["height"] == 50
    assert data["series"][0]["height"] == 0
    assert data["kinds"] == {"post": 2, "all": 1} and data["countries"]["US"] == 1 and data["referrers"]["a.example"] == 1
    assert data["last_flush_utc"] == NOW


def test_the_window_is_7_28_or_90_and_stops_at_its_first_day(db):
    assert [rules.parse_days(x) for x in ("7", "28", "90", 90, "30", "", None, "x", "-7", "٧")] == [7, 28, 90, 90, 28, 28, 28, 28, 28, 28]
    db.execute(text("INSERT INTO site_traffic VALUES (:a, 'kind', 'post', 1), (:b, 'kind', 'post', 10)"), {"a": TODAY - 6, "b": TODAY - 7})
    db.commit()
    assert store.report(db, NOW, 7)["requests"] == 1 and store.report(db, NOW, 28)["requests"] == 11


def test_an_empty_report_draws():
    assert rules.bar_heights([0, 0]) == [0, 0] and rules.bar_heights([]) == []
    assert rules.ranked({"a": 1, "b": 999}, 1000) == [
        {"value": "b", "requests": 999, "width": 99.9, "share": "100"}, {"value": "a", "requests": 1, "width": 0.1, "share": "<1"}]
    assert rules.day_label(TODAY) == "15 Jan"


def test_a_long_table_stops_at_its_top_rows_and_still_adds_up():
    hosts = {f"h{i:02}.example": 100 - i for i in range(30)}
    counts = {**hosts, "direct": 500, "internal": 1, "other": 7}
    total = sum(counts.values())
    rows = rules.ranked_top(counts, total, 15, always=("direct", "internal"))
    assert [row["value"] for row in rows][:2] == ["direct", "h00.example"]
    assert "internal" in [row["value"] for row in rows]                         # the site's own rows are never rolled up
    assert sum(row["requests"] for row in rows) == total
    named = [row for row in rows if row["value"].endswith(".example")]
    assert len(named) == 15 and named[-1]["value"] == "h14.example"
    last = rows[-1]
    assert last["value"] == "other" and last["more"] == 15 and last["requests"] == 7 + sum(100 - i for i in range(15, 30))
    assert abs(sum(row["width"] for row in rows) - 100) < 1


def test_a_short_table_has_no_last_row_unless_there_is_something_to_put_in_it():
    assert [row["value"] for row in rules.ranked_top({"GB": 5, "US": 3}, 8, 15)] == ["GB", "US"]
    rows = rules.ranked_top({"direct": 5, "other": 3}, 8, 15, always=("direct",))
    assert [(row["value"], row["requests"], row["more"]) for row in rows] == [("direct", 5, 0), ("other", 3, 0)]
    assert rules.ranked_top({}, 0, 15) == []


def test_retention_takes_days_after_400_and_batches_after_14(db):
    db.execute(text("INSERT INTO site_traffic VALUES (:old, 'kind', 'post', 1), (:kept, 'kind', 'post', 2)"),
               {"old": TODAY - 401, "kept": TODAY - 400})
    db.execute(text("INSERT INTO site_traffic_flushes VALUES ('old', :a), ('new', :b)"), {"a": NOW - 15 * DAY, "b": NOW - 13 * DAY})
    db.commit()
    done = retention.prune(db, NOW)
    assert done["old traffic counts"] == 1 and done["old traffic batches"] == 1
    assert rules.KEEP_DAYS == retention.TRAFFIC_DAYS == 400 and retention.TRAFFIC_BATCH_DAYS > store.LIVE_DAYS + 1
    assert [d for d, in db.execute(text("SELECT day FROM site_traffic")).all()] == [TODAY - 400]


# ---- nothing says who ----

PERSONAL = re.compile(r"\b(user_id|author_id|username|email|ip|addr|address|remote_addr|user_agent|session|cookie|path|url|"
                      r"token|name|display_name)\b", re.I)


def statements(source):
    return re.findall(r'text\(((?:\s*"[^"]*"\s*)+)\)', source)


def test_no_statement_reads_or_writes_who_or_where():
    found = statements(read("ruqqus", "helpers", "traffic_store.py"))
    assert len(found) >= 11
    for statement in found:
        assert not PERSONAL.search(statement), statement


def test_every_statement_on_the_sites_own_tables_only_counts():
    source = read("ruqqus", "helpers", "traffic_store.py")
    for statement in statements(source):
        if re.search(r"FROM (users|submissions|comments)\b", statement):
            assert re.match(r'\s*"SELECT created_utc / 86400, COUNT\(\*\) FROM', statement), statement


def test_the_table_has_only_a_day_a_kind_a_value_and_a_number():
    columns = re.findall(r"^\s+(\w+) (?:integer|bigint|character)", read("scripts", "migrations", MIGRATION.name), re.M)
    assert columns == ["day", "dimension", "value", "requests", "batch", "flushed_utc"]
    assert "REFERENCES" not in MIGRATION.read_text(encoding="utf-8")


def test_the_rules_are_pure_and_the_store_never_reads_a_request():
    rules_source = read("ruqqus", "helpers", "traffic.py")
    assert "flask" not in rules_source and "sqlalchemy" not in rules_source and "redis" not in rules_source.split('"""', 2)[2]
    store_source = read("ruqqus", "helpers", "traffic_store.py")
    assert "flask" not in store_source and "request." not in store_source


def test_the_hook_runs_last_touches_no_session_or_database_and_can_not_raise():
    main = read("ruqqus", "__main__.py")
    hook = main.split("def _count_request(response):", 1)[1].split("\n\n\n", 1)[0]
    assert "try:" in hook and "except Exception" in hook
    for word in ("session", "g.db", "g.", "remote_addr", "cookies", ".set_cookie", "redis", " r."):
        assert word not in hook, word
    after = main.split("def after_request(response):", 1)[1].split('@app.route("/<path:path>", subdomain="www")', 1)[0]
    assert after.rstrip().endswith("_count_request(response)\n\n    return response")
    assert "_traffic = traffic_sender.Sender(r, traffic_store.record)" in main


def test_the_sender_only_queues_on_the_request_and_drops_what_it_can_not_send():
    source = read("ruqqus", "helpers", "traffic_sender.py")
    put = source.split("def put(self, hit):", 1)[1].split("def _next_batch", 1)[0]
    assert "put_nowait" in put and "self.record" not in put and "self.r." not in put
    assert "except Full" in put and "gevent.Timeout(SEND_TIMEOUT)" in source and "except (Exception, gevent.Timeout)" in source
    # given False, a timeout ends the `with` silently and a hung Redis, the likeliest way to lose counts, logs nothing
    assert "SEND_TIMEOUT, False" not in source


# ---- the wiring ----

def test_the_feed_says_a_fetch_is_not_a_page_opened():
    source = read("ruqqus", "assets", "js", "infinite_scroll.js")
    fetch_page = source.split("function fetchPage(url) {", 1)[1].split("\n  }", 1)[0]
    assert f"'{rules.MARKER_HEADER}': '1'" in fetch_page
    assert len(re.findall(r"\bfetch\(", source)) == 1


def test_scripts_that_fetch_text_only_ask_for_what_is_not_on_the_list():
    """Every script that reads a whole answer as text, and what it asks: none of these endpoints may join the allowlist
    without a marker."""
    assert "/inpage/post_card/" in read("ruqqus", "assets", "js", "inline_composer.js")
    assert "/space/" in read("ruqqus", "assets", "js", "post_spaces.js")            # post_space
    for endpoint in ("post_space", "collab_panel", "get_post_title"):
        assert endpoint not in rules.ENDPOINT_KINDS
    panels = read("ruqqus", "assets", "js", "side_panels.js")
    assert "/notifications?embed=1" in panels and "/chat?embed=1" in panels
    assert "/chat?embed=1" in read("ruqqus", "assets", "js", "share_chat.js")
    assert "url.searchParams.set('embed', '1')" in read("ruqqus", "assets", "js", "embedded.js")


def test_every_allowlisted_endpoint_is_a_real_route_with_a_label():
    routes = "".join(p.read_text(encoding="utf-8") for p in (APP / "routes").glob("*.py"))
    for endpoint in rules.ENDPOINT_KINDS:
        assert re.search(rf"\bdef {endpoint}\(", routes), endpoint
    assert set(rules.ENDPOINT_KINDS.values()) == set(rules.KIND_LABELS)


def test_flush_is_a_step_of_the_recompute_job_with_its_own_try():
    job = read("scripts", "recomputes.py")
    step = job.split("from ruqqus.helpers import traffic_store", 1)[1].split("from ruqqus.helpers import retention", 1)[0]
    assert "traffic_store.flush(db, traffic_redis" in step and "except Exception as e:" in step and "db.rollback()" in step


def test_schema_mirrors_the_migration():
    schema = read("schema.sql")
    assert "CREATE TABLE public.site_traffic (" in schema and "CREATE TABLE public.site_traffic_flushes (" in schema
    assert f"('{MIGRATION.name}')" in schema


def test_the_page_is_level_3_english_and_says_requests_not_people():
    route = read("ruqqus", "routes", "admin_traffic_pages.py")
    assert '@app.get("/admin/traffic")\n@admin_level_required(3)' in route
    page = read("ruqqus", "templates", "admin", "traffic.html")
    assert "These are requests, not people." in page and "CDN" in page and "caches HTML" in page
    assert "_(" not in page and "<a href=\"{{ row" not in page and "| safe" not in page
    assert "/admin/traffic" in read("ruqqus", "templates", "admin", "admin_home.html")
    assert "from .admin_traffic_pages import *" in read("ruqqus", "routes", "__init__.py")


def test_the_page_says_only_the_listed_kinds_count_and_what_a_robot_is():
    page = read("ruqqus", "templates", "admin", "traffic.html")
    foot = page.split('traffic-foot">', 1)[1]
    assert "not every page" in foot and "guildmaster tools" in foot and "history" in foot
    assert "forwarded comments" in foot                             # the posts tile, as _POSTS has it
    assert "the site's list of robots" in page
    # a seven-digit day at the narrowest phone and the largest text scrolls in its own box, never the page
    days = page.split('class="traffic-days"', 1)[1].split("</details>", 1)[0]
    assert '<div class="traffic-scroll" tabindex="0" role="region" aria-label="Day by day">' in days
    sheet = read("ruqqus", "assets", "style", "_admin_traffic.scss")
    assert re.search(r"\.traffic-scroll \{\s+overflow-x: auto;", sheet) and re.search(r"\.traffic-scroll th \{\s+white-space: nowrap;", sheet)


def test_the_stylesheet_is_tokens_only_and_in_both_sheets():
    partial = read("ruqqus", "assets", "style", "_admin_traffic.scss")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|\$[a-z]", partial)
    for sheet in ("main.scss", "main_dark.scss"):
        assert '@import "{ruqquspath}/assets/style/_admin_traffic";' in read("ruqqus", "assets", "style", sheet)


def test_the_privacy_page_says_what_is_counted():
    page = read("ruqqus", "templates", "help", "privacy.html")
    section = page.split('id="requests"', 1)[1].split("<h2", 1)[0]
    assert "requests, not people" in section and "400 days" in section and "No account, name, IP address, browser or page address" in section
    # always there: it isn't behind the switch of the optional Plausible section
    assert page.index('id="requests"') < page.index("{% if 'ANALYTICS_SRC'")
