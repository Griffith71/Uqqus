"""Push notifications: the encryption (helpers/web_push.py) against RFC 8291's own worked example, the site's signature
(VAPID) checked with the public key, which addresses are ever sent to, what a notification says (helpers/push_rules.py,
never a message's words), and the subscriptions' SQL (helpers/push_store.py) and sending (helpers/push_send.py) on SQLite."""
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ruqqus.helpers import push_rules, push_send, push_store, web_push

ROOT = Path(__file__).resolve().parent.parent


def key_of(b64):
    return ec.derive_private_key(int.from_bytes(web_push.unb64u(b64), "big"), ec.SECP256R1())


# RFC 8291, Appendix A
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "ua_private": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "body": ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_y"
             "l95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"),
}


def test_a_message_is_encrypted_exactly_as_rfc_8291_says():
    body = web_push.encrypt(RFC["plaintext"], RFC["ua_public"], RFC["auth"], salt=web_push.unb64u(RFC["salt"]),
                            private_key=key_of(RFC["as_private"]))
    assert web_push.b64u(body) == RFC["body"]
    assert web_push.decrypt(body, key_of(RFC["ua_private"]), RFC["auth"]) == RFC["plaintext"]


def test_every_message_is_its_own_and_only_its_browser_opens_it():
    ua = ec.generate_private_key(ec.SECP256R1())
    public = web_push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    auth = web_push.b64u(b"0123456789abcdef")
    one, two = web_push.encrypt(b"hello", public, auth), web_push.encrypt(b"hello", public, auth)
    assert one != two and web_push.decrypt(one, ua, auth) == b"hello"
    with pytest.raises(Exception):
        web_push.decrypt(one, ec.generate_private_key(ec.SECP256R1()), auth)
    assert web_push.keys_ok(public, auth)
    assert not web_push.keys_ok(public[:-4], auth) and not web_push.keys_ok(public, web_push.b64u(b"short"))


def test_the_site_signs_for_the_push_service_alone_and_its_public_key_checks_it():
    private, public = web_push.new_keys()
    found = web_push.configured({"VAPID_PRIVATE_KEY": private, "VAPID_PUBLIC_KEY": public})
    assert found and found[1] == public
    assert web_push.configured({"VAPID_PRIVATE_KEY": private, "VAPID_PUBLIC_KEY": web_push.new_keys()[1]}) is None   # a mismatched pair is off
    assert web_push.configured({}) is None
    header = web_push.vapid_headers("https://fcm.googleapis.com/fcm/send/abc", found[0], public, "https://example.com", now=1000)
    token = header["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    head, claims, sig = token.split(".")
    assert json.loads(web_push.unb64u(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "https://example.com"}
    raw = web_push.unb64u(sig)
    point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), web_push.unb64u(public))
    point.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                 f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))       # raises if the signature isn't the site's
    assert header["Authorization"].endswith(f"k={public}")


def test_only_a_known_push_service_is_ever_sent_to():
    for good in ("https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
                 "https://web.push.apple.com/QK", "https://db5p.notify.windows.com/w/?token=x"):
        assert web_push.endpoint_ok(good), good
    for bad in ("http://fcm.googleapis.com/fcm/send/x", "https://evil.example/x", "https://fcm.googleapis.com.evil.example/x",
                "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x", "https://127.0.0.1/x",
                "https://fcm.googleapis.com", "", None, "https://" + "a" * 2000):
        assert not web_push.endpoint_ok(bad), bad


# --- what a notification says ----------------------------------------------------------------------------------------

def test_a_notification_says_who_and_what_kind_never_the_words():
    assert push_rules.wording(push_rules.MESSAGE, "Ann", True) == "New message from Ann"
    assert push_rules.wording(push_rules.MESSAGE, "Ann", False) == "New message"
    assert push_rules.wording(push_rules.REQUEST, "Ann", True) == "New message request from Ann"
    assert push_rules.wording(push_rules.CALL, "Ann", True, video=True) == "Incoming video call from Ann"
    assert push_rules.wording(push_rules.GROUP_CALL, None, True) == "Incoming call"
    sent = push_rules.payload(push_rules.MESSAGE, "New message", "Laba", "!room")
    assert sent == {"title": "Laba", "body": "New message", "url": "/chat?room=!room", "tag": "chat:!room", "call": False}
    assert push_rules.told_in_group(push_rules.EVERY, push_rules.MESSAGE)
    assert not push_rules.told_in_group(push_rules.CALLS, push_rules.MESSAGE)
    assert push_rules.told_in_group(push_rules.CALLS, push_rules.GROUP_CALL)
    assert not push_rules.told_in_group(push_rules.NOTHING, push_rules.GROUP_CALL)
    assert push_rules.mode("1") == 1 and push_rules.mode("3") is None and push_rules.mode("x") is None


# --- the subscriptions -----------------------------------------------------------------------------------------------

@pytest.fixture
def db():
    sql = (ROOT / "scripts" / "migrations" / "2026-12-27_push.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in (s.strip() for s in sql.split(";") if "CREATE" in s):
            conn.execute(text(statement.replace("public.", "").replace("IF NOT EXISTS ", "")
                              .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")))
    with Session(engine) as session:
        yield session


def test_a_browser_tells_whoever_turned_it_on_last_and_a_member_keeps_ten(db):
    push_store.save(db, 1, "https://fcm.googleapis.com/a", "p", "a", 10)
    push_store.save(db, 2, "https://fcm.googleapis.com/a", "p", "a", 11)          # another account on the same browser
    assert push_store.of_users(db, [1, 2]) == {2: [{"id": 2, "endpoint": "https://fcm.googleapis.com/a", "p256dh": "p", "auth": "a"}]}
    for n in range(12):
        push_store.save(db, 3, f"https://fcm.googleapis.com/{n}", "p", "a", 100 + n)
    assert push_store.count(db, 3) == push_store.MAX_PER_USER
    assert push_store.remove(db, 3, "https://fcm.googleapis.com/11") == 1 and push_store.remove(db, 1, "https://fcm.googleapis.com/a") == 0


def test_what_the_push_service_says_is_noted_and_a_gone_browser_forgotten(db):
    push_store.save(db, 1, "https://fcm.googleapis.com/gone", "p", "a", 1)
    push_store.save(db, 1, "https://fcm.googleapis.com/flaky", "p", "a", 1)
    push_store.save(db, 1, "https://fcm.googleapis.com/fine", "p", "a", 1)
    subs = {s["endpoint"].rsplit("/", 1)[1]: s for s in push_store.of_users(db, [1])[1]}
    for _ in range(push_store.MAX_FAILURES - 1):
        push_store.note(db, subs["flaky"]["id"], web_push.FAILED, 5)
    push_store.note(db, subs["gone"]["id"], web_push.GONE, 5)
    push_store.note(db, subs["fine"]["id"], web_push.SENT, 5)
    assert sorted(s["endpoint"].rsplit("/", 1)[1] for s in push_store.of_users(db, [1])[1]) == ["fine", "flaky"]
    push_store.note(db, subs["flaky"]["id"], web_push.FAILED, 5)                  # the tenth in a row
    assert [s["endpoint"].rsplit("/", 1)[1] for s in push_store.of_users(db, [1])[1]] == ["fine"]


def test_sending_posts_the_encrypted_message_to_the_push_service_and_forgets_a_gone_one(db, monkeypatch):
    private, public = web_push.new_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public)
    ua = ec.generate_private_key(ec.SECP256R1())
    ua_public = web_push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    auth = web_push.b64u(b"fedcba9876543210")
    push_store.save(db, 1, "https://fcm.googleapis.com/ok", ua_public, auth, 1)
    push_store.save(db, 1, "https://fcm.googleapis.com/gone", ua_public, auth, 1)
    db.commit()
    seen = []

    class Answer:
        def __init__(self, code):
            self.status_code = code

    def post(url, data, headers, timeout, allow_redirects):
        seen.append((url, headers, data))
        return Answer(410 if url.endswith("/gone") else 201)

    payload = {"title": "Laba", "body": "New message", "url": "/chat"}
    jobs = [(sub, payload) for sub in push_store.of_users(db, [1])[1]]
    outcomes = push_send._send(jobs, "high", "https://example.com", post=post, session=db)
    assert sorted(o for _, o in outcomes) == ["gone", "sent"]
    url, headers, body = seen[0]
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "high" and headers["TTL"] == str(web_push.TTL)
    assert json.loads(web_push.decrypt(body, ua, auth)) == payload               # only that browser can read it
    assert [s["endpoint"] for s in push_store.of_users(db, [1])[1]] == ["https://fcm.googleapis.com/ok"]
