"""On the real app: which requests the count of page requests takes (helpers/traffic.py), that nothing about it can reach a
reader, Redis's own behaviour for the bounds, the flush and report on Postgres, and who may read the page. The pure rules are
tests/test_traffic.py. Redis here is the shared one, so the tests that use it write under a day long past, and the ones about
what counts capture the hits instead of sending them."""
import json
import logging
import re
import secrets
import time
from pathlib import Path

import pytest
from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
LONG_AGO = 1_000_000_000
LONG_AGO_DAY = LONG_AGO // 86400


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, path, method="get", **headers):
    sent = {"User-Agent": AGENT, "Accept-Language": "en-GB,en;q=0.9"}
    sent.update({key.replace("_", "-"): value for key, value in headers.items()})
    return getattr(client, method)(path, base_url=BASE, headers=sent, environ_base={"REMOTE_ADDR": addr()})


class Capture:
    def __init__(self):
        self.hits = []

    def put(self, hit):
        self.hits.append(hit)


@pytest.fixture
def captured(real_app, monkeypatch):
    import ruqqus.__main__ as main
    capture = Capture()
    monkeypatch.setattr(main, "_traffic", capture)
    return capture


def test_every_allowlisted_endpoint_exists_and_answers_get(real_app):
    from ruqqus.helpers import traffic
    app, _ = real_app
    get_rules = {rule.endpoint for rule in app.url_map.iter_rules() if "GET" in rule.methods}
    assert not set(traffic.ENDPOINT_KINDS) - get_rules


def test_every_endpoint_the_unit_tests_call_not_a_page_exists(real_app):
    app, _ = real_app
    named = json.loads((Path(__file__).resolve().parent.parent / "data" / "traffic_not_pages.json").read_text(encoding="utf-8"))["endpoints"]
    assert not set(named) - {rule.endpoint for rule in app.url_map.iter_rules()}


@pytest.mark.parametrize("path, kind", [("/", "for_you"), ("/all", "all"), ("/login", "sign_in"), ("/help/privacy", "help"),
                                        ("/browse", "explore"), ("/trending", "trending"), ("/happening", "happening")])
def test_a_page_opened_counts_once_with_its_kind(real_app, captured, path, kind):
    app, _ = real_app
    assert call(app.test_client(), path, Referer="https://news.example/a?b=c", CF_IPCountry="GB").status_code == 200
    assert [tuple(h)[1:] for h in captured.hits] == [(kind, "news.example", "GB")]


@pytest.mark.parametrize("path", ["/api/v1/all/listing", "/inpage/all", "/inpage/trending", "/inpage/happening", "/help/privacy?embed=1", "/all?embed=1",
                                  "/healthz", "/readyz", "/robots.txt", "/ads.txt", "/manifest.json", "/no-such-page-here", "/random",
                                  "/@no_such_account_here", "/assets/style/main.css", "/admin/traffic", "/admin/signups"])
def test_what_is_not_a_page_opened_does_not_count(real_app, captured, path):
    app, _ = real_app
    call(app.test_client(), path)
    assert captured.hits == [], path


def test_the_answer_to_the_video_question_does_not_count(real_app, captured):
    app, _ = real_app
    call(app.test_client(), "/consent/video", method="post")
    assert captured.hits == []


@pytest.mark.parametrize("headers", [{"X_Background_Fetch": "1"}, {"Sec_Fetch_Dest": "empty"}, {"Sec_Fetch_Dest": "iframe"}, {"Sec_Purpose": "prefetch"},
                                     {"Purpose": "prefetch"}, {"User_Agent": "Googlebot/2.1"}, {"User_Agent": "curl/8.4"}, {"User_Agent": ""}])
def test_a_fetch_a_prefetch_or_a_robot_does_not_count(real_app, captured, headers):
    app, _ = real_app
    assert call(app.test_client(), "/all", **headers).status_code == 200
    assert captured.hits == []


def test_head_does_not_count(real_app, captured):
    app, _ = real_app
    assert call(app.test_client(), "/all", method="head").status_code == 200
    assert captured.hits == []


def test_a_member_is_counted_the_same_with_nothing_of_theirs(real_app, captured, make_member):
    browser = make_member().browser()
    assert call(browser, "/all", CF_IPCountry="T1").status_code == 200
    assert [tuple(h)[1:] for h in captured.hits] == [("all", "direct", "unknown")]


@pytest.mark.parametrize("referer, country", [("http://[bad", "<script>"), ("\xff\xfe", "\xff"), ("x" * 8000, "G" * 500), ("https://" + "a." * 900 + "com", "")])
def test_a_bad_header_never_reaches_a_reader(real_app, captured, referer, country):
    app, _ = real_app
    assert call(app.test_client(), "/help/privacy", Referer=referer, CF_IPCountry=country).status_code == 200
    assert [tuple(h)[1:] for h in captured.hits] == [("help", "other", "unknown")]


def test_a_visitors_first_page_gets_no_cookie_from_being_counted(real_app, captured):
    app, _ = real_app
    answer = call(app.test_client(), "/help/privacy", Referer="https://news.example/")
    assert len(captured.hits) == 1
    assert "session_ruqqus" not in " ".join(answer.headers.getlist("Set-Cookie"))


def test_the_counter_breaking_never_reaches_a_reader(real_app, monkeypatch):
    import ruqqus.__main__ as main
    app, _ = real_app

    class Broken:
        def put(self, hit):
            raise RuntimeError("anything at all")
    monkeypatch.setattr(main, "_traffic", Broken())
    assert call(app.test_client(), "/help/privacy").status_code == 200

    def explodes(*args, **kwargs):
        raise RuntimeError("the rules themselves")
    monkeypatch.setattr(main.traffic_rules, "hit_of", explodes)
    assert call(app.test_client(), "/help/privacy").status_code == 200


# ---- the sender: Redis down or hanging costs a page nothing ----

def test_redis_refusing_connections_costs_a_count_and_nothing_else(real_app, monkeypatch):
    import gevent
    import redis
    import ruqqus.__main__ as main
    from ruqqus.helpers import traffic_sender, traffic_store
    app, _ = real_app
    sender = traffic_sender.Sender(redis.Redis(host="127.0.0.1", port=1), traffic_store.record)
    monkeypatch.setattr(main, "_traffic", sender)
    assert call(app.test_client(), "/help/privacy").status_code == 200
    gevent.sleep(0.2)
    assert not sender.runner.dead                       # still there for when Redis comes back
    assert sender.queue.qsize() == 0


def test_redis_hanging_holds_no_page_and_the_queue_has_an_end(real_app, monkeypatch):
    import gevent
    import ruqqus.__main__ as main
    from ruqqus.helpers import traffic, traffic_sender
    app, _ = real_app
    monkeypatch.setattr(traffic_sender, "SEND_TIMEOUT", 0.2)
    monkeypatch.setattr(traffic_sender, "MAX_WAITING", 5)
    sent = []

    def hangs(r, hits):
        sent.append(len(hits))
        gevent.sleep(60)
    sender = traffic_sender.Sender(object(), hangs)
    monkeypatch.setattr(main, "_traffic", sender)
    started = time.monotonic()
    assert call(app.test_client(), "/help/privacy").status_code == 200
    assert time.monotonic() - started < 5
    hit = traffic.Hit(1, "post", "direct", "GB")
    gevent.sleep(0.05)                                  # the sender is now stuck on the first batch
    for _ in range(50):
        sender.put(hit)                                 # none of these waits, and past the end they are dropped
    assert sender.queue.qsize() == 5
    gevent.sleep(0.5)
    assert len(sent) >= 2 and not sender.runner.dead    # it gave up on a stuck batch and went on


def wait_for_a_try(sent, gevent):
    for _ in range(100):
        if sent:
            return
        gevent.sleep(0.02)


@pytest.mark.parametrize("failure", ["hangs", "refuses"])
def test_losing_counts_is_said_in_the_log_once_however_redis_is_failing(real_app, monkeypatch, caplog, failure):
    import gevent
    from ruqqus.helpers import traffic, traffic_sender
    monkeypatch.setattr(traffic_sender, "SEND_TIMEOUT", 0.05)
    sent = []

    def record(r, hits):
        sent.append(len(hits))
        if failure == "refuses":
            raise ConnectionError("no redis")
        gevent.sleep(60)
    sender = traffic_sender.Sender(object(), record)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            before = len(sent)
            sender.put(traffic.Hit(1, "post", "direct", "GB"))
            gevent.sleep(0.15)
            assert len(sent) == before + 1                  # each was tried and given up on
    lines = [r for r in caplog.records if "traffic counts dropped" in r.getMessage()]
    assert len(lines) == 1, "one line, however many batches went"
    assert sender.warned > 0 and not sender.runner.dead
    sender.runner.kill()


# ---- real Redis: the bounds and the flush ----

@pytest.fixture
def redis_client(real_app):
    from ruqqus.__main__ import r
    from ruqqus.helpers import traffic_store as store
    if r is None:
        pytest.skip("no Redis")

    def clean():
        for key in r.scan_iter("traffic:*"):
            text_key = key.decode() if isinstance(key, bytes) else key
            if f":{LONG_AGO_DAY}:" in text_key or f":{LONG_AGO_DAY - 1}:" in text_key:
                r.delete(key)
    clean()
    assert not r.get(store.POINTER), "a batch is waiting: another flush is running against this Redis"
    yield r
    clean()


def test_the_real_redis_keeps_to_the_bounds_and_sets_an_end(redis_client):
    from ruqqus.helpers import traffic, traffic_store as store
    hits = [traffic.Hit(LONG_AGO_DAY, "post", f"h{i}.example", "GB") for i in range(traffic.LIVE_HOSTS + 700)]
    for start in range(0, len(hits), 100):
        store.record(redis_client, hits[start:start + 100])
    hosts = store.live_key(LONG_AGO_DAY, store.HOSTS)
    assert redis_client.zcard(hosts) == traffic.LIVE_HOSTS + 1
    assert redis_client.zscore(hosts, store.TOTAL) == len(hits)
    assert 0 < redis_client.ttl(hosts) <= (store.LIVE_DAYS + 1) * 86400
    assert 0 < redis_client.ttl(store.live_key(LONG_AGO_DAY, store.FIXED)) <= (store.LIVE_DAYS + 1) * 86400


def test_flush_on_real_redis_and_postgres_counts_once_and_loses_nothing(real_app, redis_client):
    from ruqqus.helpers import traffic, traffic_store as store
    _, db_session = real_app
    db = db_session()
    db.execute(text("DELETE FROM site_traffic WHERE day = :d"), {"d": LONG_AGO_DAY})
    db.commit()
    store.record(redis_client, [traffic.Hit(LONG_AGO_DAY, "post", "a.example", "GB"), traffic.Hit(LONG_AGO_DAY, "post", "other", "unknown"),
                                traffic.Hit(LONG_AGO_DAY, "all", "direct", "GB")])
    assert store.flush(db, redis_client, LONG_AGO) == 3
    assert store.flush(db, redis_client, LONG_AGO) == 0
    got = {(d, v): n for d, v, n in db.execute(text("SELECT dimension, value, requests FROM site_traffic WHERE day = :d"), {"d": LONG_AGO_DAY}).all()}
    assert got == {("kind", "post"): 2, ("kind", "all"): 1, ("country", "GB"): 2, ("country", "unknown"): 1,
                   ("referrer", "a.example"): 1, ("referrer", "other"): 1, ("referrer", "direct"): 1}
    assert not redis_client.exists(store.live_key(LONG_AGO_DAY, store.FIXED)) and not redis_client.get(store.POINTER)

    # a commit that went through and a Redis that didn't tidy up: the next flush doesn't count it again
    store.record(redis_client, [traffic.Hit(LONG_AGO_DAY, "post", "direct", "GB")])
    batch = "ci" + secrets.token_hex(4)
    redis_client.set(store.POINTER, batch)
    redis_client.renamenx(store.live_key(LONG_AGO_DAY, store.FIXED), store.held_key(batch, LONG_AGO_DAY, store.FIXED))
    assert 0 < redis_client.ttl(store.held_key(batch, LONG_AGO_DAY, store.FIXED))        # the end travels with it
    db.execute(text("INSERT INTO site_traffic_flushes (batch, flushed_utc) VALUES (:b, :n)"), {"b": batch, "n": LONG_AGO})
    db.commit()
    assert store.flush(db, redis_client, LONG_AGO) == 0
    assert db.execute(text("SELECT requests FROM site_traffic WHERE day = :d AND dimension = 'kind' AND value = 'post'"), {"d": LONG_AGO_DAY}).scalar() == 2
    assert not redis_client.get(store.POINTER)
    db.close()


def test_the_report_runs_on_postgres(real_app):
    from ruqqus.helpers import traffic_store as store
    _, db_session = real_app
    db = db_session()
    data = store.report(db, int(time.time()), 90)
    assert len(data["series"]) == 90 and isinstance(data["requests"], int)
    assert data["signups"] >= 0 and data["posts"] >= 0 and data["comments"] >= 0
    db.close()


def test_a_forwarded_comment_is_not_counted_as_a_post_too(real_app, make_member):
    """Through the real route: the post it makes has no repost_id, and the same words are already under Comments."""
    from ruqqus.classes import Board
    from ruqqus.helpers import traffic_store as store
    _, db_session = real_app
    db = db_session()
    name = "ci_tr_" + secrets.token_hex(3)
    db.add(Board(name=name, description="a guild for the traffic test", description_html="<p>a guild for the traffic test</p>", creator_id=1,
                 subcat_id=0, is_private=False, is_sensitive=False, created_utc=int(time.time()), color="23a9f2"))
    db.commit()
    member = make_member()
    browser = member.browser()

    def send(path, **data):
        data["formkey"] = member.formkey(browser)
        answer = browser.post(path, base_url=BASE, data=data, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": addr()})
        assert answer.status_code < 300, answer.get_data(as_text=True)[:300]
        return answer

    made = send("/api/vue/submit", title="A post for the traffic test " + secrets.token_hex(3), body="Some words.", post_to_profile="1").get_json()["id"]
    html = send("/api/comment", parent_fullname="t2_" + made, submission=made, body="A comment to forward " + secrets.token_hex(3)).get_json()["html"]
    comment = re.search(r'id="comment-(\w+)"', html).group(1)

    now = int(time.time())

    def posts():
        return store.report(db, now, 7)["posts"]
    db.commit()
    before, rows = posts(), db.execute(text("SELECT COUNT(*) FROM submissions")).scalar()
    send(f"/comment/{comment}/forward", board=name)
    db.commit()
    assert db.execute(text("SELECT COUNT(*) FROM submissions")).scalar() == rows + 1
    assert db.execute(text("SELECT COUNT(*) FROM comment_forwardrels WHERE board_id = (SELECT id FROM boards WHERE name = :n)"), {"n": name}).scalar() == 1
    assert posts() == before
    db.close()


def test_retention_runs_on_postgres(real_app):
    from ruqqus.helpers import retention
    _, db_session = real_app
    db = db_session()
    done = retention.prune(db, int(time.time()))
    assert done["old traffic counts"] >= 0 and done["old traffic batches"] >= 0
    db.close()


# ---- the page ----

def admin(make_member, real_app, level):
    member = make_member()
    _, db_session = real_app
    db = db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": member.id})
    db.commit()
    return member


def test_who_may_read_the_page(real_app, make_member):
    app, _ = real_app
    visitor = call(app.test_client(), "/admin/traffic")
    assert visitor.status_code == call(app.test_client(), "/admin/signups").status_code   # the neighbours' refusal: to sign in
    assert visitor.status_code == 302 and "/login" in visitor.headers["Location"]
    assert call(make_member().browser(), "/admin/traffic").status_code == 403
    assert call(admin(make_member, real_app, 2).browser(), "/admin/traffic").status_code == 403
    assert call(admin(make_member, real_app, 3).browser(), "/admin/traffic").status_code == 200


@pytest.mark.parametrize("days, rows", [("7", 7), ("28", 28), ("90", 90), ("1000", 28), ("x", 28)])
def test_the_page_says_what_it_is_and_shows_the_window(real_app, make_member, days, rows):
    page = call(admin(make_member, real_app, 3).browser(), f"/admin/traffic?days={days}").get_data(as_text=True)
    assert "These are requests, not people." in page and "caches HTML" in page
    assert page.count('class="traffic-bar-col"') == rows
    assert f'aria-current="page">{rows} days' in page


def test_a_referrer_is_escaped_and_never_a_link(real_app, make_member):
    from ruqqus.helpers import traffic as rules
    assert rules.referrer_of("https://<img src=x>.example/", set()) == "other"
    _, db_session = real_app
    db = db_session()
    day = int(time.time()) // 86400
    db.execute(text("INSERT INTO site_traffic (day, dimension, value, requests) VALUES (:d, 'referrer', 'evil<b>.example', 3), "
                    "(:d, 'kind', 'post', 3) ON CONFLICT DO NOTHING"), {"d": day})
    db.commit()
    page = call(admin(make_member, real_app, 3).browser(), "/admin/traffic?days=7").get_data(as_text=True)
    assert "evil&lt;b&gt;.example" in page and "evil<b>" not in page and 'href="https://evil' not in page
    db.execute(text("DELETE FROM site_traffic WHERE day = :d AND value = 'evil<b>.example'"), {"d": day})
    db.commit()
    db.close()


def test_the_long_tables_add_up_and_say_what_their_last_row_holds(real_app, make_member):
    _, db_session = real_app
    db = db_session()
    day = int(time.time()) // 86400 - 1
    values = [("referrer", f"h{i:02}.ci-traffic.example", 1000 - i) for i in range(30)] + [("referrer", "other", 40)]
    values += [("country", code, 1000 - i) for i, code in enumerate(f"Q{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")]
    db.execute(text("DELETE FROM site_traffic WHERE day = :d"), {"d": day})
    for dimension, value, n in values:
        db.execute(text("INSERT INTO site_traffic (day, dimension, value, requests) VALUES (:d, :x, :v, :n)"), {"d": day, "x": dimension, "v": value, "n": n})
    db.commit()
    page = call(admin(make_member, real_app, 3).browser(), "/admin/traffic?days=7").get_data(as_text=True)
    db.execute(text("DELETE FROM site_traffic WHERE day = :d"), {"d": day})
    db.commit()
    db.close()

    def row_after(heading, label):
        section = page.split(f">{heading}</h2>", 1)[1].split("</section>", 1)[0]
        cell = section.split(label, 1)[1]
        return section, int(re.search(r'traffic-num">([\d,]+)<', cell).group(1).replace(",", ""))

    section, rest = row_after("Referrers", ">Everything else<")
    assert rest == 40 + sum(1000 - i for i in range(15, 30))
    assert "h14.ci-traffic.example" in section and "h15.ci-traffic.example" not in section
    assert "The 15 largest" in section
    section, rest = row_after("Countries", ">11 more countries<")
    assert rest == sum(1000 - i for i in range(15, 26))
    assert ">QO<" in section and "QP" not in section


def test_the_admin_home_links_it(real_app, make_member):
    page = call(admin(make_member, real_app, 3).browser(), "/admin").get_data(as_text=True)
    assert 'href="/admin/traffic"' in page
