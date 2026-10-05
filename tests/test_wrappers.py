"""Characterization tests for the auth/permission decorators in
ruqqus/helpers/wrappers.py and the formkey helpers in helpers/security.py.
They pin what the code does TODAY; "suspected bug" tests document behaviour
that probably should change. Error pages are Werkzeug defaults here (the real
app's errorhandlers are not loaded), so only status codes are asserted for
abort()s.
"""
import re
import time
from pathlib import Path

import pytest

from legacy_harness import FakeBoard, FakeComment, FakeMod, FakeModAction, FakeSubmission

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app_(legacy):
    w = legacy.wrappers

    def mk(deco, name, **kw):
        def view(v=None, **kwargs):
            return {"user": v.username if v else None,
                    "kwargs": sorted(k for k in kwargs)}

        def handler(v=None, **kwargs):
            r = view(v=v, **kwargs)
            from flask import jsonify
            return jsonify(r)
        handler.__name__ = name
        legacy.add_route(f"/{name}", deco(handler))

    mk(w.auth_required, "req")
    mk(w.auth_desired, "des")
    mk(w.is_not_banned, "nb")
    mk(w.admin_level_required(3), "adm")
    legacy.user = legacy.add_user(id=1, username="alice")
    return legacy


# ------------------------------------------------------------- auth_required
def test_auth_required_logged_out_is_401(app_):
    assert app_.client.get("/req").status_code == 401


def test_auth_required_logged_in_passes_v_and_sets_headers(app_):
    app_.login(app_.user)
    r = app_.client.get("/req")
    assert r.status_code == 200
    assert r.get_json() == {"user": "alice", "kwargs": []}
    assert r.headers["Cache-Control"] == "private"
    assert r.headers["Access-Control-Allow-Origin"] == "localhost"


def test_auth_required_does_not_block_banned_users(app_):
    """CURRENT BEHAVIOUR: unlike is_not_banned, banned users pass."""
    app_.user.is_banned = 1
    app_.login(app_.user)
    assert app_.client.get("/req").status_code == 200


def test_auth_required_rejects_stale_login_nonce(app_):
    app_.login(app_.user)
    app_.user.login_nonce = 5
    assert app_.client.get("/req").status_code == 401


def test_auth_required_rejects_deleted_user(app_):
    app_.login(app_.user)
    app_.user.is_deleted = True
    assert app_.client.get("/req").status_code == 401


def test_auth_required_session_with_null_user_id_is_401(app_):
    with app_.client.session_transaction() as s:
        s["user_id"] = None
    assert app_.client.get("/req").status_code == 401


def test_auth_required_session_for_unknown_user_id_is_logged_in_as_none(app_):
    """CURRENT BEHAVIOUR: unknown user -> (None, None) via v falsy, 401."""
    with app_.client.session_transaction() as s:
        s["user_id"] = 999
    assert app_.client.get("/req").status_code == 401


def test_session_nonce_missing_defaults_to_zero(app_):
    with app_.client.session_transaction() as s:
        s["user_id"] = 1
    assert app_.client.get("/req").status_code == 200
    app_.user.login_nonce = 1
    assert app_.client.get("/req").status_code == 401


def test_auth_required_sets_g_v_and_user_client_none(app_):
    seen = {}

    def view(v):
        from flask import g
        seen["g_v"] = g.v
        seen["client"] = v.client
        return "ok"
    app_.add_route("/seen", app_.wrappers.auth_required(view))
    app_.login(app_.user)
    assert app_.client.get("/seen").status_code == 200
    assert seen == {"g_v": app_.user, "client": None}


# ------------------------------------------------------------ bearer tokens
def test_v1_path_uses_token_not_session_and_passes_c_kwarg(app_):
    app_.add_route("/api/v1/req", app_.wrappers.auth_required(
        lambda v, c: f"{v.username}:{c.access_token}"))
    app_.login(app_.user)
    assert app_.client.get("/api/v1/req").status_code == 401  # cookie ignored
    app_.add_token(app_.user, token="abc")
    r = app_.client.get("/api/v1/req", headers=app_.bearer("abc"))
    assert (r.status_code, r.get_data(as_text=True)) == (200, "alice:abc")


def test_non_v1_path_ignores_authorization_header(app_):
    app_.add_token(app_.user)
    assert app_.client.get("/req", headers=app_.bearer()).status_code == 401


def test_v1_token_must_have_two_parts_and_not_be_expired(app_):
    app_.add_route("/api/v1/req", app_.wrappers.auth_required(
        lambda v, c: "ok"))
    app_.add_token(app_.user, token="live")
    app_.add_token(app_.user, token="old", expires=int(time.time()) - 5)
    for h in ({}, {"Authorization": "live"}, {"Authorization": "Bearer old"}):
        assert app_.client.get("/api/v1/req", headers=h).status_code == 401
    # scheme word is not checked, only the second whitespace-separated part
    r = app_.client.get("/api/v1/req", headers={"Authorization": "Whatever live"})
    assert r.status_code == 200


def test_v1_token_user_nonce_and_deleted_not_checked(app_):
    """CURRENT BEHAVIOUR: token auth skips login_nonce/is_deleted checks."""
    app_.add_route("/api/v1/req", app_.wrappers.auth_required(
        lambda v, c: "ok"))
    app_.user.is_deleted = True
    app_.user.login_nonce = 9
    app_.add_token(app_.user)
    assert app_.client.get("/api/v1/req", headers=app_.bearer()).status_code == 200


# -------------------------------------------------------------- auth_desired
def test_auth_desired_logged_out_gets_v_none_and_public_cache(app_):
    r = app_.client.get("/des")
    assert r.status_code == 200
    assert r.get_json() == {"user": None, "kwargs": []}
    assert r.headers["Cache-Control"] == "public"
    assert "Access-Control-Allow-Origin" not in r.headers


def test_auth_desired_logged_in_private_and_cors(app_):
    app_.login(app_.user)
    r = app_.client.get("/des")
    assert r.get_json()["user"] == "alice"
    assert r.headers["Cache-Control"] == "private"
    assert r.headers["Access-Control-Allow-Origin"] == "localhost"


def test_auth_desired_banned_user_still_gets_page(app_):
    app_.user.is_banned = 1
    app_.login(app_.user)
    assert app_.client.get("/des").get_json()["user"] == "alice"


def test_auth_desired_bad_session_is_treated_as_logged_out(app_):
    app_.login(app_.user)
    app_.user.login_nonce = 3
    r = app_.client.get("/des")
    assert r.get_json()["user"] is None and r.headers["Cache-Control"] == "public"


def test_auth_desired_v1_invalid_token_is_logged_out_not_401(app_):
    app_.add_route("/api/v1/des", app_.wrappers.auth_desired(
        lambda v: str(v)))
    r = app_.client.get("/api/v1/des", headers=app_.bearer("nope"))
    assert (r.status_code, r.get_data(as_text=True)) == (200, "None")


def test_auth_desired_returns_error_tuple_unchanged(app_):
    app_.add_route("/des-err", app_.wrappers.auth_desired(
        lambda v: ("nope", 418)))
    assert app_.client.get("/des-err").status_code == 418


# ---------------------------------------------------------------- is_not_banned
def test_is_not_banned_matrix(app_):
    assert app_.client.get("/nb").status_code == 401
    app_.login(app_.user)
    assert app_.client.get("/nb").status_code == 200
    app_.user.is_banned = 1
    assert app_.client.get("/nb").status_code == 403
    app_.user.unban_utc = int(time.time()) + 100
    assert app_.client.get("/nb").status_code == 403
    app_.user.unban_utc = int(time.time()) - 100
    assert app_.client.get("/nb").status_code == 200


# --------------------------------------------------------- check_ban_evade
def test_ban_evade_zero_does_nothing(app_):
    app_.login(app_.user)
    app_.client.get("/req")
    assert app_.user.ban_evade == 0 and app_.db.commits == 0


def test_ban_evade_counter_increments_and_commits_when_roll_misses(app_, monkeypatch):
    monkeypatch.setattr(app_.wrappers.random, "randint", lambda a, b: 30)
    app_.user.ban_evade = 5
    app_.login(app_.user)
    assert app_.client.get("/req").status_code == 200
    assert app_.user.ban_evade == 6
    assert app_.db.commits == 1 and app_.user in app_.db.added


def test_ban_evade_roll_hit_bans_content_and_403s(app_, monkeypatch):
    monkeypatch.setattr(app_.wrappers.random, "randint", lambda a, b: 0)
    app_.user.ban_evade = 5
    post = app_.db.insert(FakeSubmission(id=1, author_id=1))
    post.is_banned = False
    other = app_.db.insert(FakeSubmission(id=2, author_id=1))
    other.is_banned = True
    c = app_.db.insert(FakeComment(id=1, author_id=1))
    c.is_banned = False
    c.post = post
    app_.login(app_.user)
    r = app_.client.get("/req")
    assert r.status_code == 403
    assert app_.user.ban_calls == ["Evading a site-wide ban"]
    assert len(app_.notifications) == 1
    assert post.is_banned and c.is_banned
    assert post.ban_reason.startswith("Ban evasion.")
    actions = app_.db.rows(FakeModAction)
    assert sorted(a.kind for a in actions) == ["ban_comment", "ban_post"]
    assert app_.db.commits == 2


def test_ban_evade_does_not_ban_already_suspended_user_but_increments(app_, monkeypatch):
    monkeypatch.setattr(app_.wrappers.random, "randint", lambda a, b: 0)
    app_.user.ban_evade = 5
    app_.user.is_banned = 1
    app_.login(app_.user)
    assert app_.client.get("/req").status_code == 200  # auth_required allows banned
    assert app_.user.ban_evade == 6 and app_.user.ban_calls == []


def test_ban_evade_runs_for_auth_desired_and_is_not_banned_too(app_, monkeypatch):
    monkeypatch.setattr(app_.wrappers.random, "randint", lambda a, b: 0)
    app_.user.ban_evade = 2
    app_.login(app_.user)
    assert app_.client.get("/des").status_code == 403
    assert app_.client.get("/nb").status_code == 403


def test_admin_level_required_does_not_run_ban_evade_check(app_, monkeypatch):
    """CURRENT BEHAVIOUR: admin routes skip check_ban_evade."""
    monkeypatch.setattr(app_.wrappers.random, "randint", lambda a, b: 0)
    app_.user.ban_evade = 5
    app_.user.admin_level = 3
    app_.login(app_.user)
    assert app_.client.get("/adm").status_code == 200
    assert app_.user.ban_evade == 5


# ------------------------------------------------------- admin_level_required
def test_admin_logged_out_401(app_):
    assert app_.client.get("/adm").status_code == 401


def test_admin_level_thresholds(app_):
    app_.login(app_.user)
    for level, status in ((0, 403), (2, 403), (3, 200), (4, 200)):
        app_.user.admin_level = level
        assert app_.client.get("/adm").status_code == status


def test_admin_success_headers_and_kwargs(app_):
    app_.user.admin_level = 3
    app_.login(app_.user)
    r = app_.client.get("/adm")
    assert r.get_json() == {"user": "alice", "kwargs": []}
    assert r.headers["Cache-Control"] == "private"
    assert r.headers["Access-Control-Allow-Origin"] == "localhost"


def test_admin_banned_admin_is_403_even_if_temp_ban_expired(app_):
    """CURRENT BEHAVIOUR (suspected bug): uses is_banned, not is_suspended,
    so an admin whose temporary ban already expired stays locked out
    (is_not_banned would let them through)."""
    app_.user.admin_level = 5
    app_.user.is_banned = 1
    app_.user.unban_utc = int(time.time()) - 100
    app_.login(app_.user)
    assert app_.client.get("/adm").status_code == 403
    assert app_.client.get("/nb").status_code == 200


def test_admin_oauth_client_gets_json_403_before_anything_else(app_):
    app_.add_route("/api/v1/adm", app_.wrappers.admin_level_required(1)(
        lambda v: "secret"))
    app_.user.admin_level = 9
    app_.add_token(app_.user)
    r = app_.client.get("/api/v1/adm", headers=app_.bearer())
    assert (r.status_code, r.get_json()) == (403, {"error": "No admin api access"})


def test_admin_v1_without_token_is_401(app_):
    app_.add_route("/api/v1/adm", app_.wrappers.admin_level_required(1)(
        lambda v: "secret"))
    assert app_.client.get("/api/v1/adm").status_code == 401


def test_admin_tuple_response_status_and_headers_are_dropped(app_):
    """CURRENT BEHAVIOUR (suspected bug): only response[0] of a returned
    tuple is used, so a view returning ("x", 404) or ("x", 201) gives 200."""
    app_.user.admin_level = 3
    app_.login(app_.user)
    w = app_.wrappers
    app_.add_route("/adm-404", w.admin_level_required(1)(lambda v: ("gone", 404)))
    app_.add_route("/adm-hdr", w.admin_level_required(1)(
        lambda v: ("x", 201, {"X-A": "1"})))
    r = app_.client.get("/adm-404")
    assert (r.status_code, r.get_data(as_text=True)) == (200, "gone")
    r = app_.client.get("/adm-hdr")
    assert r.status_code == 200 and "X-A" not in r.headers


def test_admin_level_required_zero_lets_any_logged_in_user(app_):
    app_.add_route("/adm0", app_.wrappers.admin_level_required(0)(lambda v: "ok"))
    app_.login(app_.user)
    assert app_.client.get("/adm0").status_code == 200


# -------------------------------------------------------------- is_guildmaster
@pytest.fixture
def gm(app_):
    w = app_.wrappers
    app_.board = app_.db.insert(FakeBoard(id=5, name="testguild"))
    app_.mod = FakeMod()
    app_.board.mods[1] = app_.mod

    def view(v, board, **kw):
        return {"board": board.name, "kw": sorted(kw)}

    from flask import jsonify

    def mk(rule, *perms):
        def h(v, board, **kw):
            return jsonify(view(v, board, **kw))
        h.__name__ = rule
        app_.add_route(rule, w.auth_required(w.is_guildmaster(*perms)(h)))

    mk("/g/<guildname>/none")
    mk("/g/<guildname>/post", "post")
    mk("/g/<guildname>/two", "post", "access")
    mk("/b/<bid>/none")
    mk("/b/<boardname>/bn")
    mk("/nobid")
    app_.login(app_.user)
    return app_


def test_guildmaster_logged_out_is_401_from_auth_required(app_, gm):
    with gm.client.session_transaction() as s:
        s.clear()
    assert gm.client.get("/g/testguild/none").status_code == 401


def test_guildmaster_by_guildname_non_mod_403_json(gm):
    del gm.board.mods[1]
    r = gm.client.get("/g/testguild/none")
    assert (r.status_code, r.get_json()) == (
        403, {"error": "You aren't a guildmaster of +testguild"})


def test_guildmaster_mod_without_perms_requirement_passes(gm):
    r = gm.client.get("/g/testguild/none")
    assert r.status_code == 200
    assert r.get_json() == {"board": "testguild", "kw": ["guildname"]}


def test_guildmaster_missing_perm_403_json(gm):
    r = gm.client.get("/g/testguild/post")
    assert (r.status_code, r.get_json()) == (
        403, {"error": "Permission `post` required"})


def test_guildmaster_specific_perm_ok_and_full_perm_overrides(gm):
    gm.mod.perm_post = True
    assert gm.client.get("/g/testguild/post").status_code == 200
    r = gm.client.get("/g/testguild/two")
    assert r.get_json() == {"error": "Permission `access` required"}
    gm.mod.perm_full = True
    del gm.mod.perm_post
    assert gm.client.get("/g/testguild/two").status_code == 200


def test_guildmaster_perms_must_all_hold_in_order(gm):
    gm.mod.perm_access = True
    r = gm.client.get("/g/testguild/two")
    assert r.get_json() == {"error": "Permission `post` required"}


def test_guildmaster_has_mod_false_for_banned_board_is_403(gm):
    gm.board.mods[1] = False
    assert gm.client.get("/g/testguild/none").status_code == 403


def test_guildmaster_board_id_from_path_kwarg_bid(gm):
    r = gm.client.get("/b/5/none")
    assert r.status_code == 200 and r.get_json()["board"] == "testguild"


def test_guildmaster_boardname_kwarg_also_accepted(gm):
    assert gm.client.get("/b/testguild/bn").status_code == 200


def test_guildmaster_bid_or_board_id_from_request_values(gm):
    assert gm.client.get("/nobid?bid=5").status_code == 200
    assert gm.client.get("/nobid?board_id=5").status_code == 200
    assert gm.client.post("/nobid", data={"bid": "5"}).status_code == 200


def test_guildmaster_no_board_specified_is_400_json(gm):
    r = gm.client.get("/nobid")
    assert (r.status_code, r.get_json()) == (400, {"error": "no guild specified"})


def test_guildmaster_unknown_guild_is_404(gm):
    assert gm.client.get("/g/nosuch/none").status_code == 404


def test_guildmaster_path_name_takes_precedence_over_form_bid(gm):
    gm.db.insert(FakeBoard(id=6, name="other"))
    r = gm.client.get("/g/testguild/none?bid=6")
    assert r.get_json()["board"] == "testguild"


def test_guildmaster_permanently_banned_mod_403_after_perm_checks(gm):
    gm.user.is_banned = 1
    assert gm.client.get("/g/testguild/none").status_code == 403
    r = gm.client.get("/g/testguild/post")  # perm failure wins (JSON)
    assert r.get_json() == {"error": "Permission `post` required"}


def test_guildmaster_temp_banned_mod_is_NOT_blocked(gm):
    """CURRENT BEHAVIOUR (suspected bug): the ban check is
    `is_banned and not unban_utc`, so any user with an unban date (even a
    future one, i.e. currently suspended) keeps guildmaster powers when the
    route is only under @auth_required."""
    gm.user.is_banned = 1
    gm.user.unban_utc = int(time.time()) + 10_000
    assert gm.client.get("/g/testguild/none").status_code == 200


def test_guildmaster_doc_strings_include_perms(gm):
    w = gm.wrappers

    @w.is_guildmaster("post", "flair")
    def f():
        """doc"""
    assert f.__doc__ == ("<small>guildmaster permissions: "
                         "<code>post, flair</code></small><br>doc")
    assert f.__name__ == "f"

    @w.is_guildmaster()
    def g_():
        """plain"""
    assert g_.__doc__ == "plain"


# ---------------------------------------------------------- validate_formkey
@pytest.fixture
def fk(app_):
    w = app_.wrappers
    app_.add_route("/fk", w.auth_required(w.validate_formkey(
        lambda v: "ok")), methods=("GET", "POST"))
    app_.add_route("/api/v1/fk", w.auth_required(w.validate_formkey(
        lambda v, c: "ok-api")), methods=("GET", "POST"))
    app_.login(app_.user)
    return app_


def test_formkey_valid_is_accepted(fk):
    r = fk.client.post("/fk", data={"formkey": fk.formkey(fk.user)})
    assert (r.status_code, r.get_data(as_text=True)) == (200, "ok")


def test_formkey_missing_is_401(fk):
    assert fk.client.post("/fk").status_code == 401


def test_formkey_empty_string_is_401(fk):
    assert fk.client.post("/fk", data={"formkey": ""}).status_code == 401


def test_formkey_wrong_is_401(fk):
    assert fk.client.post("/fk", data={"formkey": "0" * 32}).status_code == 401


def test_formkey_is_checked_on_get_too_via_query_string(fk):
    assert fk.client.get("/fk").status_code == 401
    assert fk.client.get("/fk?formkey=" + fk.formkey(fk.user)).status_code == 200


def test_formkey_bound_to_user_login_nonce_and_session(fk):
    key = fk.formkey(fk.user)
    fk.user.login_nonce = 1
    with fk.client.session_transaction() as s:
        s["login_nonce"] = 1
    assert fk.client.post("/fk", data={"formkey": key}).status_code == 401
    assert fk.client.post(
        "/fk", data={"formkey": fk.formkey(fk.user)}).status_code == 200
    with fk.client.session_transaction() as s:
        s["session_id"] = "rotated"
    assert fk.client.post(
        "/fk", data={"formkey": fk.formkey(fk.user)}).status_code == 401


def test_formkey_skipped_entirely_on_api_v1_paths(fk):
    fk.add_token(fk.user)
    r = fk.client.post("/api/v1/fk", headers=fk.bearer())
    assert (r.status_code, r.get_data(as_text=True)) == (200, "ok-api")


def test_formkey_without_session_id_is_500(fk):
    """CURRENT BEHAVIOUR (suspected bug): KeyError -> 500, not 401."""
    with fk.client.session_transaction() as s:
        del s["session_id"]
    assert fk.client.post("/fk", data={"formkey": "x"}).status_code == 500


def test_formkey_validate_wrapper_requires_v_keyword(app_):
    """validate_formkey must sit under an auth decorator: called without v it
    raises TypeError (-> 500) rather than a clean 401."""
    app_.add_route("/fk-bare", app_.wrappers.validate_formkey(lambda v: "ok"))
    assert app_.client.post("/fk-bare").status_code == 500


# ----------------------------------------------------------------- security.py
def test_generate_hash_is_deterministic_32_hex_and_keyed(legacy, monkeypatch):
    from ruqqus.helpers import security
    h = security.generate_hash("hello")
    assert re.fullmatch(r"[0-9a-f]{32}", h)
    assert security.generate_hash("hello") == h
    assert security.generate_hash("hello2") != h
    monkeypatch.setenv("MASTER_KEY", "other-key")
    assert security.generate_hash("hello") != h


def test_validate_hash_roundtrip_and_mismatch(legacy):
    from ruqqus.helpers import security
    h = security.generate_hash("abc")
    assert security.validate_hash("abc", h) is True
    assert security.validate_hash("abd", h) is False
    assert security.validate_hash("abc", h.upper()) is False


def test_generate_hash_without_master_key_raises_typeerror(legacy, monkeypatch):
    from ruqqus.helpers import security
    monkeypatch.delenv("MASTER_KEY")
    with pytest.raises(TypeError):
        security.generate_hash("x")


def test_validate_hash_non_ascii_raises_typeerror(legacy):
    from ruqqus.helpers import security
    with pytest.raises(TypeError):
        security.validate_hash("abc", "kéy")


def test_hash_password_uses_pbkdf2_sha512_and_verifies(legacy):
    from ruqqus.helpers import security
    h = security.hash_password("pw")
    assert h.startswith("pbkdf2:sha512:")
    assert security.check_password_hash(h, "pw")
    assert not security.check_password_hash(h, "pw2")
    assert security.hash_password("pw") != h  # salted


def test_formkey_message_format(legacy):
    """formkey == generate_hash(f"{session_id}+{user.id}+{login_nonce}"): no
    timestamp is part of it, so formkeys never expire by time (CURRENT BEHAVIOUR)."""
    from ruqqus.helpers.security import generate_hash
    u = legacy.add_user(id=7, login_nonce=3)
    assert legacy.formkey(u, "abc") == generate_hash("abc+7+3")


def test_fake_user_formkey_code_matches_real_user_class():
    """Drift guard: the harness FakeUser replicates these real lines."""
    src = (ROOT / "ruqqus" / "classes" / "user.py").read_text(encoding="utf-8")
    assert "msg = f\"{session['session_id']}+{self.id}+{self.login_nonce}\"" in src
    assert ("return validate_hash(f\"{session['session_id']}+{self.id}+"
            "{self.login_nonce}\", formkey)") in src
    assert "session[\"session_id\"] = token_hex(16)" in src
    assert "self.unban_utc ==\n                                    0 or" in src
