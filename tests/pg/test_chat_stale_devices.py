"""Stale site devices (chat plan, phase 2, build step 7: helpers/chat_signouts.py `prune_stale`, a step of scripts/recomputes.py) on
the real app and a real Postgres, with the chat server faked: a site chat device nobody has used for 90 days is revoked and its `listed`
sign-out runs, the account's most recently used chat device survives however old, a suspended account is pruned like anyone's (the
owner's decision 3 adds no exception), the browser that held a pruned device is told `stale`, and without the migration the step is a
logged no-op. The step is called as the cycle calls it (prune, then the queue); tests/test_chat_signouts.py pins its place in the
cycle's source and holds the rules on SQLite."""
import logging
import time

from sqlalchemy import text

from .test_chat_signout_routes import mint, post, queue_rows, revoked, server, set_user, with_chat  # noqa: F401  (server is a fixture)

DAY = 86400


def used(member, device_id, ago_days):
    db = member.db_session()
    db.execute(text("UPDATE chat_site_devices SET last_used_utc = :t WHERE user_id = :u AND device_id = :d"),
               {"t": int(time.time()) - ago_days * DAY, "u": member.id, "d": device_id})
    db.commit()


def cycle():
    """What scripts/recomputes.py's two chat sign-out steps do: the queue (run_due), then the pruning; and the queue again, as the
    next cycle runs it."""
    from ruqqus.__main__ import db_session
    from ruqqus.helpers import chat_signouts
    import ruqqus.helpers.matrix_client as matrix
    db = db_session()
    db.rollback()
    chat_signouts.run_due(db, matrix)
    pruned = chat_signouts.prune_stale(db, int(time.time()))
    chat_signouts.run_due(db, matrix)
    return pruned


def test_an_extra_device_unused_for_ninety_days_is_signed_out_and_the_most_recent_survives(make_member, server):
    member = make_member()
    mxid = with_chat(member)
    a, b, c = member.browser(), member.browser(), member.browser()
    old_chat, old_call, recent_chat = mint(member, a), mint(member, a, kind="call"), mint(member, b)
    fresh = mint(member, c)
    server["devices"][mxid]["ELEMENTAPP1"] = "Element Web"
    used(member, old_chat, 91)
    used(member, old_call, 120)
    used(member, recent_chat, 95)                # stale too, but the account's most recently used chat device once `fresh` is gone
    used(member, fresh, 1)
    set_user(member, is_banned=1, unban_utc=int(time.time()) + 3 * DAY)     # suspended: pruned like anyone's (decision 3)

    assert cycle() >= 3
    assert revoked(member) == {old_chat: "stale", old_call: "stale", recent_chat: "stale"}
    assert [(r[0], r[2], r[3] > 0) for r in queue_rows(member)] == [("listed", "stale", True)] * 3, "each a listed sign-out, done"
    assert set(server["devices"][mxid]) == {fresh, "ELEMENTAPP1"}, "the chat server deleted them; an outside app is never touched"

    # the browser that held a pruned device: a running page's renewal is told it went, and its next page gets a new device, told why
    gone = post(a, "/api/chat/token", {"formkey": member.formkey(a), "mode": "renew", "device_id": old_chat})
    assert gone.status_code == 409 and gone.get_json() == {"device_gone": True, "reason": "stale"}
    set_user(member, is_banned=0, unban_utc=0)
    again = post(a, "/api/chat/token", {"formkey": member.formkey(a), "mode": "boot", "device_id": old_chat})
    assert again.status_code == 200 and again.get_json()["previous"] == {"device_id": old_chat, "reason": "stale"}


def test_the_only_chat_device_is_never_pruned_however_old(make_member, server):
    member = make_member()
    mxid = with_chat(member)
    only = mint(member, member.browser())
    used(member, only, 400)
    cycle()
    assert revoked(member) == {} and queue_rows(member) == [] and only in server["devices"][mxid]
    assert mint(member, member.browser(), device_id=None)          # and a second browser is served as ever


def test_an_account_with_a_sign_out_pending_is_left_for_a_later_cycle(make_member, server):
    member = make_member()
    mxid = with_chat(member)
    old, new = mint(member, member.browser()), mint(member, member.browser())
    used(member, old, 100)
    server["down"] = True                        # the pending row: a sign-out the chat server couldn't do yet
    db = member.db_session()
    db.execute(text("INSERT INTO chat_device_signouts (user_id, mxid, scope, device_ids, spare_after_ms, reason, created_utc, next_try_utc) "
                    "VALUES (:u, :m, 'listed', 'UQNOTHING000', 0, 'signed_out_here', :t, :t)"),
               {"u": member.id, "m": mxid, "t": int(time.time())})
    db.commit()
    cycle()
    assert old not in revoked(member)
    server["down"] = False
    db.execute(text("UPDATE chat_device_signouts SET next_try_utc = 0 WHERE user_id = :u AND done_utc = 0"), {"u": member.id})
    db.commit()
    cycle()                                      # the queue finishes the pending row first, then the account is pruned
    assert revoked(member) == {old: "stale"} and new in server["devices"][mxid] and old not in server["devices"][mxid]


# --- the migration not applied yet (renames the table and puts it back) ------------------------------------------------------------

def test_without_the_migration_the_step_is_a_logged_no_op(pg_engine, monkeypatch, caplog, real_app):
    from ruqqus.__main__ import db_session
    from ruqqus.helpers import chat_signouts
    with pg_engine.connect() as conn:
        conn.execute(text("ALTER TABLE chat_device_signouts RENAME TO chat_device_signouts_away"))
    try:
        monkeypatch.setattr(chat_signouts, "_ready", [])
        db = db_session()
        db.rollback()
        with caplog.at_level(logging.WARNING):
            assert chat_signouts.prune_stale(db, int(time.time())) is None
        assert "stale chat devices not pruned: migration missing" in caplog.text
        db.execute(text("SELECT 1"))             # the session is still usable
        db.rollback()
    finally:
        with pg_engine.connect() as conn:
            conn.execute(text("ALTER TABLE chat_device_signouts_away RENAME TO chat_device_signouts"))
        monkeypatch.setattr(chat_signouts, "_ready", [])
