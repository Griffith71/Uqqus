"""Feedback about the site, on the real app and Postgres: a member sends it (with a screenshot), an admin reads it, answers
it and closes it, the sender is told, and nobody else can see any of it (helpers/site_feedback.py, routes/site_feedback.py)."""
import io
import json
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-feedback/1.0 (Test)"


def address():
    # the hourly limit is per member, but the address is also kept in Redis between runs: each call its own
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={"User-Agent": AGENT},
                                   environ_base={"REMOTE_ADDR": address()}, **kw)


def turned_away(response):
    """Not let in: 401/403, or what this app answers a visitor or a refused form key with, a redirect to the sign-in."""
    return response.status_code in (401, 403) or (response.status_code == 302 and "/login" in response.headers["Location"])


def png(size=(640, 400), colour=(30, 120, 200)):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def admin_of(make_member, level=3):
    admin = make_member()
    db = admin.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": admin.id})
    db.commit()
    return admin


def send(member, here=None, **fields):
    here = here or member.browser()
    data = {"formkey": member.formkey(here), "kind": "design", "message": "The sidebar is too grey.",
            "context": json.dumps({"path": "/home?token=SECRET", "theme": "dark", "density": "compact", "width": 390,
                                   "height": 844, "lang": "en", "cookie": "leak"})}
    data.update(fields)
    return call(here, "post", "/api/feedback", data=data, content_type="multipart/form-data")


def rows(member):
    db = member.db_session()
    db.expire_all()
    return db.execute(text("SELECT id, kind, message, page, context, status, response, resolved_utc "
                           "FROM site_feedback WHERE user_id = :u ORDER BY id"), {"u": member.id}).fetchall()


def told(member):
    db = member.db_session()
    db.expire_all()
    return [r[0] for r in db.execute(text(
        "SELECT a.body FROM notifications n JOIN comments_aux a ON a.id = n.comment_id WHERE n.user_id = :u ORDER BY n.id"),
        {"u": member.id}).fetchall()]


def test_a_member_sends_feedback_with_a_screenshot_and_only_admins_read_it(make_member, real_app):
    member, admin, other = make_member(), admin_of(make_member), make_member()
    shot = png()
    answer = send(member, image=(io.BytesIO(shot), "shot.png"))
    assert answer.status_code == 200, answer.get_data(as_text=True)
    assert "notifications" in answer.get_json()["message"]

    (item,) = rows(member)
    assert (item.kind, item.message, item.page, item.status) == ("design", "The sidebar is too grey.", "/home", "open")
    context = json.loads(item.context)
    assert context == {"path": "/home", "theme": "dark", "density": "compact", "width": 390, "height": 844, "lang": "en",
                       "browser": AGENT}                                  # the whitelist: no token, no cookie
    db = member.db_session()
    stored = db.execute(text("SELECT mime, data FROM site_feedback_images WHERE feedback_id = :f"), {"f": item.id}).fetchone()
    assert stored.mime in ("image/webp", "image/jpeg") and bytes(stored.data) != shot       # encoded afresh

    boss = admin.browser()
    page = call(boss, "get", "/admin/feedback")
    assert page.status_code == 200 and "The sidebar is too grey." in page.get_data(as_text=True)
    assert f"/admin/feedback/{item.id}/image" in page.get_data(as_text=True)
    picture = call(boss, "get", f"/admin/feedback/{item.id}/image")
    assert picture.status_code == 200 and picture.mimetype == stored.mime and picture.data == bytes(stored.data)
    assert "no-store" in picture.headers["Cache-Control"] and picture.headers["X-Content-Type-Options"] == "nosniff"
    assert picture.headers["Content-Security-Policy"].startswith("sandbox")
    assert "feedback" in call(boss, "get", "/admin").get_data(as_text=True)

    # a member who is not an admin (not even the sender), and a visitor, read nothing
    for who in (member.browser(), other.browser()):
        assert call(who, "get", "/admin/feedback").status_code == 403
        assert call(who, "get", f"/admin/feedback/{item.id}/image").status_code == 403
    assert turned_away(call(member.app.test_client(), "get", f"/admin/feedback/{item.id}/image"))


def test_closing_it_with_a_response_tells_the_sender_once(make_member, real_app):
    member, admin = make_member(), admin_of(make_member)
    send(member)
    (item,) = rows(member)
    boss = admin.browser()
    before = len(told(member))
    form = {"formkey": admin.formkey(boss), "status": "seen", "response": ""}
    assert call(boss, "post", f"/admin/feedback/{item.id}/status", data=form).status_code == 302
    assert rows(member)[0].status == "seen" and len(told(member)) == before         # seen, no response: no notice

    form.update(status="fixed", response="Lightened in the next update.")
    answer = call(boss, "post", f"/admin/feedback/{item.id}/status", data={**form, "back": "/admin/feedback?status=fixed"})
    assert answer.status_code == 302 and answer.headers["Location"].endswith("/admin/feedback?status=fixed")
    done = rows(member)[0]
    assert (done.status, done.response) == ("fixed", "Lightened in the next update.") and done.resolved_utc > 0
    notices = told(member)
    assert len(notices) == before + 1 and "marked fixed" in notices[-1] and "Lightened in the next update." in notices[-1]
    assert "The sidebar is too grey" in notices[-1]

    # the same answer sent again tells nobody twice
    call(boss, "post", f"/admin/feedback/{item.id}/status", data=form)
    assert len(told(member)) == before + 1
    # a return address elsewhere is not followed
    away = call(boss, "post", f"/admin/feedback/{item.id}/status", data={**form, "back": "https://evil.example/"})
    assert away.headers["Location"].endswith("/admin/feedback")
    # a non-admin cannot change anything, and a change needs the form key
    assert call(member.browser(), "post", f"/admin/feedback/{item.id}/status", data=form).status_code == 403
    assert turned_away(call(boss, "post", f"/admin/feedback/{item.id}/status", data={"status": "open"}))
    assert rows(member)[0].status == "fixed"


def test_what_cannot_be_sent_is_refused_and_nothing_is_kept(make_member, real_app):
    member = make_member()
    here = member.browser()
    visitor = member.app.test_client()
    assert turned_away(call(visitor, "post", "/api/feedback", data={"kind": "bug", "message": "x"}))
    assert turned_away(call(here, "post", "/api/feedback", data={"kind": "bug", "message": "x"}))           # no form key
    assert send(member, here, kind="spam").status_code == 400
    assert send(member, here, message="   ").status_code == 400
    html = send(member, here, image=(io.BytesIO(b"<html><script>alert(1)</script></html>"), "shot.png"))
    svg = send(member, here, image=(io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'/>"), "shot.svg"))
    assert html.status_code == 400 and svg.status_code == 400 and "picture" in html.get_json()["error"].lower()
    huge = send(member, here, image=(io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024)), "huge.png"))
    assert huge.status_code in (400, 413)
    assert rows(member) == []
    # a sheet with no screenshot is fine
    assert send(member, here).status_code == 200 and len(rows(member)) == 1


def test_a_day_has_a_limit_and_a_banned_member_cannot_send(make_member, real_app):
    from ruqqus.helpers import site_feedback
    member = make_member()
    db = member.db_session()
    for _ in range(site_feedback.DAILY_LIMIT):
        site_feedback.add(db, member.id, "bug", "again", "/", "{}", int(time.time()) - 60)
    db.commit()
    answer = send(member)
    assert answer.status_code == 429 and len(rows(member)) == site_feedback.DAILY_LIMIT

    banned = make_member()
    db = banned.db_session()
    db.execute(text("UPDATE users SET is_banned = 1 WHERE id = :u"), {"u": banned.id})
    db.commit()
    assert turned_away(send(banned)) and rows(banned) == []


def test_the_sheet_is_drawn_for_members_and_not_for_visitors(make_member, real_app):
    member = make_member()
    # the home feed (default.html) and the pages with their own shell (header.html): the one menu, the sheet, its script
    for path in ("/", "/settings/profile", "/submit"):
        answer = call(member.browser(), "get", path)
        assert answer.status_code == 200, path
        page = answer.get_data(as_text=True)
        # the menu's item, and on the pages with their own shell (header.html) the phone menu's too
        assert 'id="siteFeedbackModal"' in page and page.count("data-feedback-open") == (1 if path == "/" else 2), path
        assert "js/site_feedback.js" in page and 'id="toast-post-success-text"' in page, path
        assert page.count('class="dropdown-divider" role="separator"') == 3 and "fa-bullhorn" in page, path
        after_profile = page.split("account-menu-profile", 1)[1]
        menu = after_profile[:after_profile.index("Log out") + 7]
        order = [menu.index(word) for word in ("Create post", "Creator Studio", "Circles", "Inbox", "Chat", "Spaces",
                                               "History", "Settings", "Send feedback", "Log out")]
        assert order == sorted(order), path                                       # the groups, in order
    assert "siteFeedbackModal" not in call(member.app.test_client(), "get", "/").get_data(as_text=True)
    assert call(member.browser(), "get", "/help").status_code == 200              # the help shell has no menu and no sheet


def test_closing_the_account_takes_the_feedback_and_the_export_has_it(make_member, real_app):
    from ruqqus.helpers import account_cleanup, data_export
    member, other = make_member(), make_member()
    send(member, image=(io.BytesIO(png()), "shot.png"))
    send(other)
    db = member.db_session()
    exported = data_export.build(db, member.id)["feedback"]
    assert [row["message"] for row in exported] == ["The sidebar is too grey."] and "data" not in exported[0]
    assert account_cleanup.close_account(db, member.id, int(time.time())) == []
    db.commit()
    assert rows(member) == [] and len(rows(other)) == 1
    left = db.execute(text("SELECT COUNT(*) FROM site_feedback_images i LEFT JOIN site_feedback f ON f.id = i.feedback_id "
                           "WHERE f.id IS NULL")).scalar()
    assert left == 0
