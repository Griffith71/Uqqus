"""Starting chat over (ruqqus/helpers/chat_key_reset.py): what an upload may hold, the grant's rules, and the wiring of the two
routes (tests/pg/test_chat_key_reset.py runs them on the real app with the chat server faked)."""
import base64
import json
import re
from pathlib import Path

from ruqqus.helpers import chat_key_reset as kr

ROOT = Path(__file__).resolve().parents[1]
MXID = "@ruqqus_7:ruqqus.local"
NOW = 1_800_000_000


def b64(raw):
    return base64.b64encode(raw).decode().rstrip("=")


def key(n):
    return b64(bytes([n]) * 32)


def sig(n):
    return b64(bytes([n]) * 64)


def upload(mxid=MXID):
    """The shape matrix-js-sdk's resetEncryption sends to /keys/device_signing/upload (the master key signed by the device,
    the other two by the master key)."""
    master, ssk, usk = key(1), key(2), key(3)
    return {
        "master_key": {"user_id": mxid, "usage": ["master"], "keys": {f"ed25519:{master}": master},
                       "signatures": {mxid: {"ed25519:UQabcdefgh": sig(4)}}},
        "self_signing_key": {"user_id": mxid, "usage": ["self_signing"], "keys": {f"ed25519:{ssk}": ssk},
                             "signatures": {mxid: {f"ed25519:{master}": sig(5)}}},
        "user_signing_key": {"user_id": mxid, "usage": ["user_signing"], "keys": {f"ed25519:{usk}": usk},
                             "signatures": {mxid: {f"ed25519:{master}": sig(6)}}},
    }


def text(body):
    return json.dumps(body)


def test_the_sdks_upload_is_accepted_as_it_is_and_in_parts():
    assert kr.check_upload(text(upload()), MXID) == upload()
    assert kr.check_upload(text(upload()).encode(), MXID) == upload()
    for kind in kr.KINDS:
        assert kr.check_upload(text({kind: upload()[kind]}), MXID)


def test_another_accounts_keys_are_refused():
    assert kr.check_upload(text(upload("@ruqqus_8:ruqqus.local")), MXID) is None
    body = upload()
    body["self_signing_key"]["user_id"] = "@ruqqus_8:ruqqus.local"
    assert kr.check_upload(text(body), MXID) is None


def test_anything_more_than_the_keys_is_refused():
    for extra in ({"auth": {"type": "m.login.dummy"}}, {"device_keys": {}}, {"one_time_keys": {}}):
        assert kr.check_upload(text({**upload(), **extra}), MXID) is None
    body = upload()
    body["master_key"]["unsigned"] = {}
    assert kr.check_upload(text(body), MXID) is None


def test_badly_shaped_keys_are_refused():
    cases = []
    body = upload(); body["master_key"]["usage"] = ["self_signing"]; cases.append(body)
    body = upload(); body["master_key"]["keys"] = {"ed25519:" + key(1): key(9)}; cases.append(body)
    body = upload(); body["master_key"]["keys"] = {"curve25519:" + key(1): key(1)}; cases.append(body)
    body = upload(); body["master_key"]["keys"][f"ed25519:{key(9)}"] = key(9); cases.append(body)
    body = upload(); body["master_key"]["keys"] = {"ed25519:short": "short"}; cases.append(body)
    body = upload(); del body["self_signing_key"]["signatures"]; cases.append(body)
    body = upload(); body["user_signing_key"]["signatures"] = {MXID: {"ed25519:x": "not a signature"}}; cases.append(body)
    body = upload(); body["user_signing_key"]["signatures"] = {"no-at": {f"ed25519:{key(1)}": sig(1)}}; cases.append(body)
    for body in cases:
        assert kr.check_upload(text(body), MXID) is None


def test_not_json_empty_or_too_big_is_refused():
    assert kr.check_upload(None, MXID) is None
    assert kr.check_upload("", MXID) is None
    assert kr.check_upload("{}", MXID) is None
    assert kr.check_upload("[]", MXID) is None
    assert kr.check_upload("not json", MXID) is None
    assert kr.check_upload(b"\xff\xfe", MXID) is None
    assert kr.check_upload("[" * 4000 + "]" * 4000, MXID) is None          # nested past Python's recursion limit, inside 8 KB
    padded = upload()
    padded["master_key"]["signatures"] = {f"@x{i}:a": {f"ed25519:k{i}": sig(1)} for i in range(80)}
    assert len(text(padded)) > kr.MAX_BODY
    assert kr.check_upload(text(padded), MXID) is None


def test_a_grant_is_kept_only_by_its_hash():
    grant = kr.new_grant()
    assert len(grant) >= 43 and grant != kr.new_grant()
    assert grant not in kr.redis_key(grant) and kr.redis_key(grant).startswith("chat:kr:")


def test_a_grant_is_good_for_its_account_its_credentials_and_five_minutes():
    raw = kr.grant_record(7, 3, NOW)
    assert kr.grant_refusal(raw, 7, 3, NOW + 10) is None
    assert kr.grant_refusal(raw, 8, 3, NOW) == "another account"
    assert kr.grant_refusal(raw, 7, 4, NOW) == "credentials changed"
    assert kr.grant_refusal(raw, 7, 3, NOW + kr.GRANT_SECONDS + 1) == "too old"
    assert kr.grant_refusal(None, 7, 3, NOW) == "gone"
    assert kr.grant_refusal("not json", 7, 3, NOW) == "gone"
    assert kr.grant_refusal(kr.grant_record(7, None, NOW), 7, 0, NOW) is None


def test_a_grant_is_its_own_sign_ins():
    here, there = kr.sign_in_of("aaaa"), kr.sign_in_of("bbbb")
    assert "aaaa" not in here and here != there and kr.sign_in_of(None) == ""
    raw = kr.grant_record(7, 3, NOW, here)
    assert kr.grant_refusal(raw, 7, 3, NOW, here) is None
    assert kr.grant_refusal(raw, 7, 3, NOW, there) == "another sign-in"
    assert kr.grant_refusal(raw, 7, 3, NOW, "") == "another sign-in"


def test_what_is_left_of_a_grant():
    raw = kr.grant_record(7, 3, NOW)
    assert kr.seconds_left(raw, NOW + 100) == kr.GRANT_SECONDS - 100
    assert kr.seconds_left(raw, NOW + kr.GRANT_SECONDS + 5) == 0
    assert kr.seconds_left("not json", NOW) == 0


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_only_the_route_uploads_keys_as_a_member():
    callers = [p for p in (ROOT / "ruqqus").rglob("*.py")
               if "upload_signing_keys(" in p.read_text(encoding="utf-8") and p.name != "matrix_client.py"]
    assert [p.name for p in callers] == ["chat_key_reset_pages.py"]
    client = read("ruqqus", "helpers", "matrix_client.py").split("def upload_signing_keys", 1)[1].split("\ndef ", 1)[0]
    assert "_as_headers()" in client and '"user_id": mxid' in client


def test_the_routes_ask_what_every_chat_key_route_asks_and_take_the_grant_once():
    source = read("ruqqus", "routes", "chat_key_reset_pages.py")
    for name in ("chat_key_reset_grant", "chat_signing_keys"):
        head = source.split(f"def {name}(v):", 1)[0].rsplit("@app.route", 1)[1]
        for wrapper in ("@auth_required", "@not_qr_limited", '@not_child_locked("messaging")', "@validate_formkey", "@limiter.limit("):
            assert wrapper in head, (name, wrapper)
    grant = source.split("def chat_key_reset_grant(v):", 1)[1].split("\n@app.route", 1)[0]
    assert "throttle_check_ip(\"chat_key_reset\")" in source
    assert grant.index("verifyPass(") < grant.index("new_grant()")
    assert grant.index("accept_2fa(") < grant.index("new_grant()") and "mfa_backup_store.spend(" in grant
    assert "mfa_removal_code" not in source                     # the older recovery code turns two-factor off: never taken
    upload = source.split("def chat_signing_keys(v):", 1)[1].split("\ndef ", 1)[0]
    assert upload.index("r.getdel(") < upload.index("check_upload(") < upload.index("upload_signing_keys(")
    assert "identity.matrix_user_id" in upload and "request.form.get(\"user" not in upload
    # only a 200 that replaced the master key tells the member; any other answer is never one of the site's own codes
    assert re.search(r"if status != 200:.*?status_code = 502", upload, re.S)
    assert upload.index("if status != 200:") < upload.index("_tell(v)")
    # the grant is put back only when the chat server couldn't be reached or failed
    assert upload.index("_put_back(") < upload.index("if status != 200:")
    grant = source.split("def chat_key_reset_grant(v):", 1)[1].split("\n@app.route", 1)[0]
    assert grant.index("r.set(") < grant.index("g.db.commit()"), "a backup code's use is committed only once the grant is kept"


def test_the_log_entry_is_staff_only_and_the_routes_are_registered():
    from ruqqus.helpers import action_log
    assert "chat_keys_reset" in action_log.STAFF_ACTIONS
    assert action_log.audience_for("chat_keys_reset") == action_log.STAFF
    assert "from .chat_key_reset_pages import *" in read("ruqqus", "routes", "__init__.py")
    email = read("ruqqus", "templates", "email", "chat_reset.html")
    assert 'Chat was started over on your %(site)s account' in email
