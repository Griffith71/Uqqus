"""The Log on the real app and Postgres (helpers/action_log.py; the rules and the SQL are tested without Postgres in
tests/test_action_log.py): an admin's ban needs a reason and is kept, a second ban does not overwrite the first, a guild
mod-log row is copied into the Log as it is made with the audience its guild gives it, the staff page shows what the
public page may not, and an admin is never named in a mod-log answer."""
import secrets

from sqlalchemy import text

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-action-log"}


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, member, path, **fields):
    data = {"formkey": member.formkey(client), **fields}
    return client.post(path, base_url=BASE, headers=HEADERS, data=data, environ_base={"REMOTE_ADDR": address()})


def get(client, path):
    return client.get(path, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()})


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def entries(real_app, **where):
    _, db_session = real_app
    db = db_session()
    db.expire_all()
    clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
    return [dict(r._mapping) for r in db.execute(text(f"SELECT * FROM log_entries WHERE {clause} ORDER BY id"), where)]


def guild(real_app, private=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_log_" + secrets.token_hex(3), description="a guild for the log test",
                  description_html="<p>a guild for the log test</p>", creator_id=1, subcat_id=0, is_private=private)
    db.add(board)
    db.commit()
    return board.id, board.name


def mod_action(real_app, **fields):
    from ruqqus.classes import ModAction
    _, db_session = real_app
    db = db_session()
    action = ModAction(**fields)
    db.add(action)
    db.commit()
    return action.id


def test_an_admins_ban_needs_a_public_reason(make_member, real_app):
    admin, target = admin_of(make_member), make_member()
    here = admin.browser()
    refused = post(here, admin, f"/api/ban_user/{target.id}", reason="   ", rule="evasion", days="")
    assert refused.status_code == 400 and not target.user().is_banned
    assert entries(real_app, target_user_id=target.id) == []


def test_a_ban_and_the_ban_after_it_are_both_kept_and_so_is_the_unban(make_member, real_app):
    admin, target = admin_of(make_member), make_member()
    here = admin.browser()
    assert post(here, admin, f"/api/ban_user/{target.id}", reason="Ban evasion", rule="evasion", message="told them", days="3").status_code in (200, 302)
    assert post(here, admin, f"/api/ban_user/{target.id}", reason="Spamming links", rule="illegal", days="").status_code in (200, 302)
    assert post(here, admin, f"/api/unban_user/{target.id}", reason="Appeal accepted").status_code in (200, 302)
    first, second, lifted = entries(real_app, target_user_id=target.id)
    assert (first["action"], first["actor_role"], first["rule_key"], first["reason"], first["staff_note"], first["audience"],
            first["board_id"], first["notice_sent"]) == ("suspend_user", "admin", "evasion", "Ban evasion", "told them", 0, None, True)
    assert (second["action"], second["reason"], second["rule_key"]) == ("ban_user", "Spamming links", "illegal")    # the first is not overwritten
    assert (lifted["action"], lifted["reason"], lifted["actor_id"]) == ("unban_user", "Appeal accepted", admin.id)
    assert first["actor_id"] == admin.id and first["log_type"] == "sanction"


def test_a_mod_log_row_is_copied_with_the_audience_its_guild_gives_it(make_member, real_app):
    gm = make_member()
    public_id, _ = guild(real_app)
    private_id, _ = guild(real_app, private=True)
    for board_id in (public_id, private_id):
        mod_action(real_app, kind="exile_user", user_id=gm.id, board_id=board_id, target_user_id=gm.id, note="off &amp; away")
    (public,) = entries(real_app, board_id=public_id)
    (private,) = entries(real_app, board_id=private_id)
    assert (public["audience"], private["audience"]) == (0, 1)
    assert public["reason"] == "off & away" and public["actor_role"] == "guildmaster" and public["legacy_modaction_id"]
    assert public["log_type"] == "sanction"


def test_a_site_admin_in_a_private_guild_is_the_guilds_and_the_staffs_and_never_the_publics(make_member, real_app):
    admin = admin_of(make_member)
    private_id, _ = guild(real_app, private=True)
    mod_action(real_app, kind="ban_post", user_id=admin.id, board_id=private_id, target_submission_id=0, note="admin action")
    (row,) = entries(real_app, board_id=private_id)
    assert (row["audience"], row["actor_role"]) == (1, "admin")
    _, db_session = real_app
    from ruqqus.helpers import action_log
    db = db_session()
    assert row["id"] not in [r["id"] for r in action_log.fetch(db, level=0)[0]]
    assert row["id"] in [r["id"] for r in action_log.fetch(db, level=0, member_boards=[private_id])[0]]
    assert row["id"] in [r["id"] for r in action_log.fetch(db, level=3)[0]]


def test_a_mod_log_answer_never_names_an_admin(make_member, real_app):
    from ruqqus.classes import ModAction
    admin, gm = admin_of(make_member), make_member()
    board_id, _ = guild(real_app)
    _, db_session = real_app
    db = db_session()
    by_admin = ModAction(kind="ban_post", user_id=admin.id, board_id=board_id, note="admin action")
    by_gm = ModAction(kind="hide_post_from_guild", user_id=gm.id, board_id=board_id, note="off topic")
    db.add_all([by_admin, by_gm])
    db.commit()
    assert by_admin.json["mod"] != admin.username and by_gm.json["mod"] == gm.username


def test_the_staff_page_is_for_admins_only_and_shows_the_private_entries(make_member, real_app):
    admin, member = admin_of(make_member), make_member()
    private_id, private_name = guild(real_app, private=True)
    gm = make_member()
    mod_action(real_app, kind="exile_user", user_id=gm.id, board_id=private_id, target_user_id=member.id, note="seen in the log")
    for client in (real_app[0].test_client(), member.browser()):
        answer = get(client, "/admin/log")
        assert answer.status_code in (401, 403, 404) or (answer.status_code == 302 and "/login" in answer.headers["Location"])
    page = get(admin.browser(), f"/admin/log?guild={private_name}&type=sanction")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "exile_user" in html and private_name in html and "seen in the log" in html
    assert get(admin.browser(), "/admin/log?guild=no_such_guild_here").get_data(as_text=True).count("exile_user") == 0
