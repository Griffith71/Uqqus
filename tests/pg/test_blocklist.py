"""Blocking, and moving a block list in and out as a file, on the real app: the Block button and the import share one routine
(helpers/blocklist.py), so what a block does (follows, circles, chat) happens for every imported name too."""
import io
import secrets
import time

BASE = "http://localhost"


def address():
    # the day's import limit is per address and kept in Redis between runs: each call its own, from a large range
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={"User-Agent": "ci-blocklist"},
                                   environ_base={"REMOTE_ADDR": kw.pop("ip", None) or address()}, **kw)


def person(make_member):
    """A member who is not the site's own account (id 1, which can never be blocked)."""
    member = make_member()
    while member.id == 1:
        member = make_member()
    return member


def follow(member, target):
    from ruqqus.classes import Follow
    db = member.db_session()
    db.add(Follow(user_id=member.id, target_id=target.id, created_utc=int(time.time())))
    db.commit()


def follows(member, target):
    from ruqqus.classes import Follow
    db = member.db_session()
    db.expire_all()
    return db.query(Follow).filter_by(user_id=member.id, target_id=target.id).first() is not None


def blocked(member, target):
    from ruqqus.classes import UserBlock
    db = member.db_session()
    db.expire_all()
    return db.query(UserBlock).filter_by(user_id=member.id, target_id=target.id).first() is not None


def muted(member, target):
    from ruqqus.classes import UserMute
    db = member.db_session()
    db.expire_all()
    return db.query(UserMute).filter_by(user_id=member.id, target_id=target.id).first() is not None


def test_the_block_button_keeps_its_answers(make_member):
    me, them = person(make_member), person(make_member)
    here = me.browser()
    form = {"formkey": me.formkey(here)}

    first = call(here, "post", "/settings/block", data={**form, "username": them.username})
    assert first.status_code == 200 and first.get_json() == {"message": f"@{them.username} blocked."}
    assert blocked(me, them)

    again = call(here, "post", "/settings/block", data={**form, "username": them.username})
    assert again.status_code == 409 and them.username in again.get_json()["error"]
    assert call(here, "post", "/settings/block", data={**form, "username": me.username}).get_json() == {"error": "You can't block yourself."}
    assert call(here, "post", "/settings/block", data={**form, "username": "nobody_by_this_name"}).status_code == 404


def test_blocking_ends_the_follows_both_ways(make_member):
    me, them = person(make_member), person(make_member)
    follow(me, them)
    follow(them, me)
    here = me.browser()
    assert call(here, "post", "/settings/block", data={"formkey": me.formkey(here), "username": them.username}).status_code == 200
    assert not follows(me, them) and not follows(them, me)


def test_export_lists_blocked_and_muted_accounts_and_nothing_else(make_member):
    me, b, m, gone = person(make_member), person(make_member), person(make_member), person(make_member)
    here = me.browser()
    form = {"formkey": me.formkey(here)}
    call(here, "post", "/settings/block", data={**form, "username": b.username})
    call(here, "post", "/settings/mute", data={**form, "username": m.username})
    call(here, "post", "/settings/block", data={**form, "username": gone.username})
    db = gone.db_session()
    deleted = gone.user()
    deleted.is_deleted = True
    db.add(deleted)
    db.commit()

    out = call(here, "get", "/settings/blocks/export")
    assert out.status_code == 200 and out.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in out.headers["Content-Disposition"] and out.headers["Cache-Control"] == "no-store"
    body = out.get_data(as_text=True)
    assert body.splitlines() == ["kind,username", f"block,{b.username}", f"mute,{m.username}"]
    assert "example.com" not in body and gone.username not in body


def test_export_needs_a_sign_in(real_app):
    app, _ = real_app
    assert call(app.test_client(), "get", "/settings/blocks/export").status_code in (302, 401)


def test_an_import_blocks_and_mutes_through_the_same_routine_and_reports_counts_only(make_member):
    me, b, m, gone, already = (person(make_member) for _ in range(5))
    follow(me, b)
    follow(b, me)
    here = me.browser()
    form = {"formkey": me.formkey(here)}
    call(here, "post", "/settings/block", data={**form, "username": already.username})
    db = gone.db_session()
    deleted = gone.user()
    deleted.is_deleted = True
    db.add(deleted)
    db.commit()

    names = "\n".join(["kind,username", f"block,{b.username}", f"mute,{m.username}", f"block,{already.username}",
                       f"block,{gone.username}", f"block,{me.username}", "block,no_such_account_xyz", "block,x", "mute,ab!"])
    answer = call(here, "post", "/settings/blocks/import", data={**form, "names": names})
    assert answer.status_code == 302
    assert answer.headers["Location"].endswith("&done=1.1.1.5"), answer.headers["Location"]     # blocked, muted, already, not found

    assert blocked(me, b) and muted(me, m) and not blocked(me, gone) and not blocked(me, me)
    assert not follows(me, b) and not follows(b, me), "an imported block ends follows like the Block button"

    page = call(here, "get", answer.headers["Location"]).get_data(as_text=True)
    assert "Imported: 1 blocked, 1 muted, 1 already on your list, 5 not found." in page


def test_an_import_takes_an_uploaded_file(make_member):
    me, b = person(make_member), person(make_member)
    here = me.browser()
    upload = (io.BytesIO(f"kind,username\nblock,{b.username}\n".encode()), "list.csv")
    answer = call(here, "post", "/settings/blocks/import", data={"formkey": me.formkey(here), "file": upload},
                  content_type="multipart/form-data")
    assert answer.status_code == 302 and answer.headers["Location"].endswith("&done=1.0.0.0")
    assert blocked(me, b)


def test_too_many_names_or_none_import_nothing(make_member):
    me, b = person(make_member), person(make_member)
    here = me.browser()
    form = {"formkey": me.formkey(here)}
    many = "\n".join([b.username] + [f"user{i:03}" for i in range(200)])
    answer = call(here, "post", "/settings/blocks/import", data={**form, "names": many})
    assert answer.headers["Location"].endswith("&problem=count") and not blocked(me, b)
    page = call(here, "get", answer.headers["Location"]).get_data(as_text=True)
    assert "Import at most 200 names at a time." in page

    none = call(here, "post", "/settings/blocks/import", data={**form, "names": "  \n# nothing\n"})
    assert none.headers["Location"].endswith("&problem=empty")


def test_an_import_needs_the_form_key_and_is_limited_to_five_a_day(make_member):
    me, b = person(make_member), person(make_member)
    here = me.browser()
    refused = call(here, "post", "/settings/blocks/import", data={"names": b.username})     # no form key: the 401 page redirect
    assert refused.status_code == 302 and refused.headers["Location"].startswith("/login")
    assert not blocked(me, b)

    ip = address()
    statuses = [call(here, "post", "/settings/blocks/import", ip=ip,
                     data={"formkey": me.formkey(here), "names": "nobody_xyz"}).status_code for _ in range(6)]
    assert statuses[:5] == [302] * 5 and statuses[5] == 429, statuses


def test_the_page_offers_the_file_buttons(make_member):
    me = person(make_member)
    page = call(me.browser(), "get", "/settings/blocks").get_data(as_text=True)
    assert 'href="/settings/blocks/export"' in page and 'id="importBlocklistModal"' in page
    guilds = call(me.browser(), "get", "/settings/blocks?show=guilds").get_data(as_text=True)
    assert "importBlocklistModal" not in guilds
