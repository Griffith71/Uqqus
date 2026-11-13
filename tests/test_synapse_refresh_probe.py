"""What the site's expiring chat tokens count on from Synapse, as a live probe recorded it (chat plan, phase 2, build step 5, gap 23):
tests/data/synapse_refresh_probe.json holds the dev Synapse's real answers to an appservice login with `refresh_token: true` on a
chosen UQ device id, two /refresh calls, a reuse of a refresh token whose successor's access token was used, and a device delete. This
file checks the recording's shape, replays its answers through the real helpers/matrix_client.py (so a status the module maps wrongly
fails here, not in production), and holds homeserver.yaml to the one lifetime setting the site needs."""
import json
import re
from pathlib import Path

from tests import test_matrix_client_tokens as stubbed

ROOT = Path(__file__).resolve().parent.parent
answer = stubbed.answer
client = stubbed.client          # the fixture: the real matrix_client module with its HTTP stubbed
PROBE = json.loads((ROOT / "tests" / "data" / "synapse_refresh_probe.json").read_text(encoding="utf-8"))
STEPS = PROBE["steps"]


def find(start):
    [hit] = [s for s in STEPS if s["step"].startswith(start)]
    return hit


def test_the_recording_holds_no_real_token():
    raw = json.dumps(PROBE)
    assert not re.search(r"sy[tr]_[A-Za-z0-9]", raw), "every token is replaced by a label"
    for step in STEPS:
        for name in ("access_token", "refresh_token"):
            if name in step["body"]:
                assert re.fullmatch(r"[A-Z]{1,2}\d", step["body"][name]), step["step"]


def test_a_login_asking_for_a_refresh_token_on_a_chosen_id_gets_one_that_expires_in_an_hour():
    login = find("login with refresh_token: true")
    assert login["status"] == 200 and login["body"]["device_id"] == PROBE["device_id"]
    assert re.fullmatch(r"UQ[A-Z2-7]{10}", PROBE["device_id"]), "the site's own shape of device id"
    assert login["body"]["refresh_token"] == "R1" and login["body"]["access_token"] == "A1"
    assert 3_500_000 <= login["body"]["expires_in_ms"] <= 3_600_000, "refreshable_access_token_lifetime: 1h"
    plain = find("login without refresh_token")
    assert plain["status"] == 200 and "refresh_token" not in plain["body"] and "expires_in_ms" not in plain["body"], \
        "a login that doesn't ask keeps a token that never expires (outside apps that don't refresh are unchanged)"


def test_each_refresh_rotates_both_tokens_and_reports_the_lifetime():
    first, second = find("refresh R1 (the first /refresh)"), find("refresh R2 (the second /refresh)")
    assert first["status"] == second["status"] == 200
    assert (first["body"]["access_token"], first["body"]["refresh_token"]) == ("A2", "R2")
    assert (second["body"]["access_token"], second["body"]["refresh_token"]) == ("A3", "R3")
    assert first["body"]["expires_in_ms"] == second["body"]["expires_in_ms"] == 3_600_000


def test_a_refresh_token_reused_after_its_successors_access_token_was_used_is_forbidden():
    assert find("whoami with A2")["status"] == 200
    reuse = find("refresh R1 again")
    assert reuse["status"] == 403 and reuse["body"]["errcode"] == "M_FORBIDDEN"
    gone = find("refresh R1 after the second /refresh")
    assert gone["status"] == 401 and gone["body"]["errcode"] == "M_UNKNOWN_TOKEN", "the rotation keeps two pairs, then deletes"


def test_after_the_device_is_deleted_its_tokens_are_unknown():
    assert find("delete the device (appservice)")["status"] == 200
    for start in ("whoami with A3 after the delete", "refresh R3 after the delete"):
        step = find(start)
        assert step["status"] == 401 and step["body"]["errcode"] == "M_UNKNOWN_TOKEN", start


def test_a_rotated_away_token_looked_up_before_answers_until_its_own_expiry_even_after_the_delete():
    """Why the docs say a copied token stops working "within an hour", not at the sign-out: Synapse caches a token's lookup, the
    rotation deletes the old row without clearing that cache, and a device delete only clears the tokens still in its table. The
    expiry is checked after the cached lookup, so the hour still holds. A token never looked up before its row went is just gone."""
    assert find("whoami with A1 (rotated away")["status"] == 401
    assert find("chain B: whoami with B1 after its rows were deleted")["status"] == 200
    assert find("chain B: delete the device")["status"] == 200
    assert find("chain B: whoami with B1 after the delete")["status"] == 200


def test_every_label_that_says_what_happened_agrees_with_the_recorded_answer():
    """The labels were once written as predictions before the probe ran, and two said the opposite of the answer beside them
    (A1 "still answers" with a 401, B1 after the delete "the delete clears the cache" with a 200). A label that says what a
    request did must say what it did."""
    told = 0
    for step in STEPS:
        label = step["step"]
        if "still answers" in label or "answers until" in label:
            assert step["status"] == 200, label
            told += 1
        if "refused" in label:
            assert step["status"] >= 400, label
            told += 1
        assert "clears the cache)" not in label or step["status"] >= 400, label
    assert told >= 3, "the cached-lookup steps say what they observed"


def test_the_recorded_answers_go_through_the_real_refresh_as_the_token_route_expects(client):
    ok = find("refresh R1 (the first /refresh)")["body"]
    client.stub(answer(200, json.dumps(ok).encode()))
    assert client.refresh("R1") == ok
    for start in ("refresh R1 again", "refresh R1 after the second /refresh", "refresh R3 after the delete"):
        step = find(start)
        client.stub(answer(step["status"], json.dumps(step["body"]).encode()))
        try:
            client.refresh("R")
        except client.RefreshGone:
            continue
        raise AssertionError(start + ": must be RefreshGone (the list check, then a new login), not a 503 loop")
    login = find("login with refresh_token: true")["body"]
    http = client.stub(answer(200, json.dumps(login).encode()))
    got = client.login_as(PROBE["mxid"], PROBE["device_id"])
    assert got["expires_in_ms"] == login["expires_in_ms"] and got["refresh_token"] == "R1"
    [(_, _, sent)] = http.calls
    assert sent["json"]["refresh_token"] is True and sent["json"]["device_id"] == PROBE["device_id"], "asked as the probe asked"
    assert sent["json"]["type"] == "m.login.application_service"


def test_homeserver_yaml_sets_the_hour_and_nothing_that_would_cut_apps_short():
    lines = [line.split("#", 1)[0].strip() for line in (ROOT / "synapse" / "homeserver.yaml").read_text(encoding="utf-8").splitlines()]
    assert "refreshable_access_token_lifetime: 1h" in lines
    for name in ("session_lifetime", "nonrefreshable_access_token_lifetime", "refresh_token_lifetime"):
        assert not any(line.startswith(name + ":") for line in lines), name
    assert PROBE["config"] == {"refreshable_access_token_lifetime": "1h"}
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "matrixdotorg/synapse:" + PROBE["synapse"].split(":", 1)[1] in compose, "recorded against the Synapse the site runs"
