"""What the signup guard counts in Redis (ruqqus/helpers/signup_signals.py), on tests/fake_redis.py: bursts over the
last hour, outcome tallies, what held accounts tried, the admins' settings and the throwaway-domain list."""
from ruqqus.helpers import signup_risk
from ruqqus.helpers import signup_signals as ss
from tests.fake_redis import FakeRedis

NOW = 1_800_000_000


def test_a_burst_counts_the_last_hour_and_forgets_older_sign_ups():
    r = FakeRedis()
    for minutes_ago in (70, 50, 20, 1):
        ss.note(r, "pfx", "net", NOW - minutes_ago * 60)
    assert ss.recent(r, "pfx", "net", NOW) == 3
    assert ss.recent(r, "pfx", "other", NOW) == 0


def test_a_sign_up_counts_its_network_and_the_site_and_only_a_telling_shape():
    r = FakeRedis()
    ss.note_sign_up(r, "net", "a9{5}", NOW)
    ss.note_sign_up(r, "net", "a", NOW)
    assert ss.recent(r, "pfx", "net", NOW) == 2
    assert ss.recent(r, "all", "site", NOW) == 2
    assert ss.recent(r, "shape", "a9{5}", NOW) == 1
    assert ss.recent(r, "shape", "a", NOW) == 0


def test_no_redis_reads_zero_and_writes_nothing():
    assert ss.recent(None, "pfx", "net") == 0
    ss.note_sign_up(None, "net", "a9{5}")
    ss.tally(None, "allow")
    ss.note_attempt(None, 1, "post")
    assert ss.tallies(None, 24) == {} and ss.attempts(None, 1) == {}
    assert ss.settings(None)["quarantine_at"] == signup_risk.QUARANTINE_AT


def test_tallies_add_up_the_hours_asked_for():
    r = FakeRedis()
    ss.tally(r, "allow", NOW)
    ss.tally(r, "allow", NOW - 3600)
    ss.tally(r, "honeypot", NOW - 30 * 3600)
    assert ss.tallies(r, 24, NOW) == {"allow": 2}
    assert ss.tallies(r, 7 * 24, NOW) == {"allow": 2, "honeypot": 1}


def test_what_a_held_account_tried_is_counted_per_action():
    r = FakeRedis()
    ss.note_attempt(r, 7, "post")
    ss.note_attempt(r, 7, "post")
    ss.note_attempt(r, 7, "vote")
    assert ss.attempts(r, 7) == {"post": 2, "vote": 1}
    ss.forget_attempts(r, 7)
    assert ss.attempts(r, 7) == {}


def test_settings_have_defaults_and_bounds():
    r = FakeRedis()
    assert ss.settings(r) == {"quarantine_at": 50, "surge": False, "surge_ceiling": 60}
    ss.save_settings(r, 0, True, 99999)
    assert ss.settings(r) == {"quarantine_at": 1, "surge": True, "surge_ceiling": 10000}
    r.set(ss.QUARANTINE_AT_KEY, "junk")
    assert ss.settings(r)["quarantine_at"] == 50


def test_the_network_name_is_a_keyed_hash():
    one = ss.network_hash("203.0.113.0/24", "secret")
    assert one == ss.network_hash("203.0.113.0/24", "secret") and len(one) == 16
    assert one != ss.network_hash("203.0.113.0/24", "other secret")
    assert "203" not in one


def test_throwaway_domains_come_from_the_list():
    domains = ss.disposable_domains()
    assert len(domains) > 5000 and "mailinator.com" in domains
    assert not any(line.startswith("#") for line in domains)
    assert ss.disposable("someone@mailinator.com") and ss.disposable("x@sub.mailinator.com")
    for good in ("someone@gmail.com", "a@outlook.com", "a@hotmail.com", "a@proton.me", "a@icloud.com", "", None):
        assert not ss.disposable(good), good
