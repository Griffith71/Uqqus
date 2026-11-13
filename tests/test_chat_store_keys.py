"""The chat store keys (ruqqus/helpers/chat_store_keys.py, chat plan phase 2 section 1.1): the fixed vectors, made apart from the
module with node's crypto (tests/data/chat_store_key_vectors.json), and every property the browser's locked stores rely on."""
import base64
import json
import re
from pathlib import Path

import pytest

from ruqqus.helpers import chat_store_keys as k

VECTORS = json.loads((Path(__file__).resolve().parent / "data" / "chat_store_key_vectors.json").read_text())["vectors"]
MX = "@ruqqus_5:chat.localhost"


def raw(b64):
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: f"{v['mxid']}/{v['device'][:12]}")
def test_the_fixed_vectors(v):
    assert k.b64(k.store_key(v["secret"], v["mxid"], v["device"])) == v["key"]
    assert k.b64(k.waiting_key(v["secret"], v["mxid"])) == v["waiting"]
    assert k.key_id(v["secret"]) == v["kid"]
    assert len(raw(v["key"])) == 32 and len(raw(v["waiting"])) == 32 and len(v["key"]) == 43
    assert re.fullmatch(r"[0-9a-f]{12}", v["kid"])


def test_the_vectors_cover_the_cases_that_matter():
    assert len({v["secret"] for v in VECTORS}) >= 3 and any(not v["secret"].isascii() for v in VECTORS), "utf-8 secrets are pinned"
    assert {("@a:b", "x"), ("@ab:c", "x")} <= {(v["mxid"], v["device"]) for v in VECTORS}, "the length-prefix case is pinned"
    assert any(len(v["device"]) == 64 for v in VECTORS) and any(k.is_site_made(v["device"]) for v in VECTORS)


def test_every_key_is_32_bytes_and_43_characters():
    key = k.store_key("s", MX, "UQABCDEFGHIJ")
    assert isinstance(key, bytes) and len(key) == 32 and len(k.b64(key)) == 43 and "=" not in k.b64(key)
    assert len(k.waiting_key("s", MX)) == 32


def test_the_length_prefix_keeps_inputs_apart():
    # without the prefix, "@a" + "b:x" and "@ab" + ":x" would hash the same bytes
    assert k.store_key("s", "@a:bc", "d") != k.store_key("s", "@a:b", "cd")
    assert k.store_key("s", "@ab:c", "x") != k.store_key("s", "@a:b", "cx")


def test_store_waiting_and_kid_all_differ():
    store, waiting = k.store_key("s", MX, "UQABCDEFGHIJ"), k.waiting_key("s", MX)
    assert store != waiting and k.key_id("s") not in (store.hex(), waiting.hex()) and not store.hex().startswith(k.key_id("s"))


def test_each_depends_on_the_secret_the_mxid_and_the_device():
    base = k.store_key("s", MX, "UQABCDEFGHIJ")
    assert base != k.store_key("t", MX, "UQABCDEFGHIJ")
    assert base != k.store_key("s", "@ruqqus_6:chat.localhost", "UQABCDEFGHIJ")
    assert base != k.store_key("s", MX, "UQABCDEFGHIK")
    assert k.waiting_key("s", MX) != k.waiting_key("t", MX) != k.waiting_key("t", "@ruqqus_6:chat.localhost")
    assert k.waiting_key("s", MX) == k.waiting_key("s", MX), "the waiting key is the account's, whatever the device"
    assert k.key_id("s") != k.key_id("t")


def test_the_raw_secret_is_never_the_key():
    import hashlib
    import hmac
    flat = hmac.new(b"s", b"store\x00" + MX.encode() + b"UQABCDEFGHIJ", hashlib.sha256).digest()
    assert k.store_key("s", MX, "UQABCDEFGHIJ") != flat


def test_chat_store_secret_falls_back_to_secret_key():
    assert k.secret_of({"CHAT_STORE_SECRET": "c", "SECRET_KEY": "m"}) == "c"
    assert k.secret_of({"CHAT_STORE_SECRET": "", "SECRET_KEY": "m"}) == "m"
    assert k.secret_of({"SECRET_KEY": "m"}) == "m"
    with pytest.raises(ValueError):
        k.secret_of({"CHAT_STORE_SECRET": "", "SECRET_KEY": None})
    with pytest.raises(ValueError):
        k.secret_of({})


@pytest.mark.parametrize("device", ["", "a b", "a/b", "ü", "A" * 65, "abc\n", None, 5])
def test_a_bad_device_raises(device):
    with pytest.raises(ValueError):
        k.store_key("s", MX, device)
    assert not k.valid_device(device)


@pytest.mark.parametrize("mxid", ["", "ruqqus_5:x", None, "@" + "x" * 300])
def test_a_bad_mxid_raises(mxid):
    with pytest.raises(ValueError):
        k.store_key("s", mxid, "UQABCDEFGHIJ")
    with pytest.raises(ValueError):
        k.waiting_key("s", mxid)


@pytest.mark.parametrize("secret", ["", None])
def test_an_empty_secret_raises(secret):
    for call in (lambda: k.store_key(secret, MX, "UQABCDEFGHIJ"), lambda: k.waiting_key(secret, MX), lambda: k.key_id(secret)):
        with pytest.raises(ValueError):
            call()


def test_new_device_ids_are_the_sites_and_never_a_synapse_id():
    made = {k.new_device_id() for _ in range(200)}
    assert len(made) == 200
    for device in made:
        assert k.is_site_made(device) and k.valid_device(device) and len(device) == 12
    assert not k.is_site_made("ABCDEFGHIJ"), "Synapse's own ids are 10 letters"
    assert not k.is_site_made("UQABCDEFGHIJ\n") and not k.is_site_made("uqABCDEFGHIJ") and not k.is_site_made("UQABCDEFGH01")
