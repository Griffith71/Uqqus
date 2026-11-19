"""The sites where the site's own checks used to ban an account now quarantine it, on the real app and Postgres (routes/posts.py, comments.py, settings.py,
profile_edit_pages.py; helpers/auto_quarantine.py; the owner, 2026-11-17 and 18: an automatic check only QUARANTINES, an admin decides what follows).

- a spam burst of posts, of comments and of edits to a comment puts the account and its alts on hold (never banned), the similar posts and comments are removed as
  before with PUBLIC removal entries (an anonymous post's too: nothing automatic names an account any more), the staff's entry names the account and the trigger, the
  public line names nobody, the person is told with the review link and a held account is refused the next try;
- a link to a banned site (reasons 4, 7 and 9) in a new post, in a comment, in the text of a post and in an edit puts the account on hold with the trigger of its kind
  (a link about minors is a level-4 entry), and the hold stands (the old reason-4 text-link ban was lost to an abort);
- an autoban bad link in a post or an edit holds the account and sends it to its notifications as before (an admin, whom nothing holds, gets the refusal any bad link gets);
- the SEO-spam check on a bio, in Settings and in the pop-up, holds the account and keeps the bio;
- an admin is never held by a burst (only the similar posts go);
- the held account's nothing-public: its profile is an ordinary one;
- a shared draft's publish (the edit path) with an autoban or a malicious link holds an ordinary publisher, refuses an admin the way any bad link is refused, and
  records no revision either way;
- an account the signup guard holds (kind 0) that trips a site check becomes an automatic hold (kind 1), which no email releases; a second check on an account
  already held writes nothing and tells nobody; a CDN 451 on a held account's picture still leaves its level-4 record.
That a limited QR sign-in is only refused is held in tests/pg/test_qr_login.py."""
import io
import secrets
import time

import gevent
import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import BASE, HEADERS, add_post, address, admin_of, entries, get, guild, notices_of, post, rows

JSON = {**HEADERS, "Accept": "application/json"}
HELD = "put your account on hold"
APPEAL = "/appeal/quarantine"


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


def api(client, member, path, **form):
    return client.post(path, base_url=BASE, headers=JSON, environ_base={"REMOTE_ADDR": address()}, data={"formkey": member.formkey(client), **form})


def set_user(member, **values):
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE users SET " + ", ".join(f"{k} = :{k}" for k in values) + " WHERE id = :id"), {**values, "id": member.id})
    db.commit()


def fresh(make_member):
    """An account a minute old, whose bio is a new account's and whose spam threshold is the plain one (an older account's is two or three times it)."""
    member = make_member()
    set_user(member, created_utc=int(time.time()) - 60)
    return member


def link_alt(member, alt):
    db = member.db_session()
    db.rollback()
    db.execute(text("INSERT INTO alts (user1, user2) VALUES (:a, :b)"), {"a": member.id, "b": alt.id})
    db.commit()


def state(real_app, member):
    return rows(real_app, "SELECT quarantined_utc, quarantine_kind, is_banned, unban_utc FROM users WHERE id = :u", u=member.id)[0]


def held(real_app, member):
    now = state(real_app, member)
    return now["quarantined_utc"] > 0 and now["quarantine_kind"] == 1 and not now["is_banned"]


def staff_entry(real_app, member):
    (entry,) = entries(real_app, action="quarantine_user", target_user_id=member.id)
    return entry


def told(real_app, member):
    return [body for body in notices_of(real_app, member.id) if HELD in body]


def submit(client, member, **form):
    form.setdefault("title", "a title " + secrets.token_hex(6))
    form.setdefault("body", "words " + secrets.token_hex(6))
    return api(client, member, "/api/vue/submit", **form)


def made_post(real_app, client, member, **form):
    """(id, base 36 id, title) of a post made through the real route."""
    from ruqqus.helpers.base36 import base36encode
    form.setdefault("title", "a title " + secrets.token_hex(6))
    answer = submit(client, member, post_to_profile="1", **form)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    (found,) = rows(real_app, "SELECT id FROM submissions_aux WHERE title = :t", t=form["title"])
    return found["id"], base36encode(found["id"]), form["title"]


def comment(client, member, post36, body):
    return api(client, member, "/api/comment", parent_fullname=f"t2_{post36}", submission=post36, body=body)


def made_comment(real_app, client, member, post36, body):
    from ruqqus.helpers.base36 import base36encode
    answer = comment(client, member, post36, body)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    (found,) = rows(real_app, "SELECT id FROM comments_aux WHERE body = :b", b=body)
    return found["id"], base36encode(found["id"])


def banned_domain(real_app, reason):
    domain = f"bad{secrets.token_hex(4)}.example"
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.execute(text("INSERT INTO domains (domain, can_submit, can_comment, reason) VALUES (:d, false, false, :r)"), {"d": domain, "r": reason})
    db.commit()
    return domain


def bad_link(real_app, autoban=True):
    link = f"bad{secrets.token_hex(4)}.example/spam"
    _, db_session = real_app
    db = db_session()
    db.rollback()
    db.execute(text("INSERT INTO badlinks (reason, link, autoban) VALUES (1, :l, :a)"), {"l": link, "a": autoban})
    db.commit()
    return link


def publish_lines(real_app):
    """The cycle's step that writes the public lines (`auto_quarantine.publish_due`), run as if just past its delay: a hold writes the staff entry only."""
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    written = auto_quarantine.publish_due(db, now=int(time.time()) + auto_quarantine.PUBLISH_DELAY + 1)
    db.commit()
    return written


def public_line_names_nobody(real_app, staff, reason):
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])          # nothing public in the request itself
    publish_lines(real_app)
    (line,) = entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])
    assert (line["audience"], line["target_user_id"], line["target_submission_id"], line["target_comment_id"], line["board_id"], line["reason"]) == (0, None, None, None, None, reason)
    return line


# --- the spam guards -------------------------------------------------------------------------------------------------------------

def test_a_spam_burst_of_posts_puts_the_account_and_its_alts_on_hold_removes_the_similar_post_publicly_and_bans_nobody(real_app, make_member, monkeypatch):
    app, _ = real_app
    monkeypatch.setitem(app.config, "SPAM_SIMILAR_COUNT_THRESHOLD", 1)            # one similar post in a day is the limit for an account under a day old
    member, alt = fresh(make_member), make_member()
    link_alt(member, alt)
    here = member.browser()
    base = "the very same words again and again " + secrets.token_hex(4)
    first_id, first36, _ = made_post(real_app, here, member, title=base + " 0", body="the very same body again and again", anonymous="true")   # an ANONYMOUS post

    answer = submit(here, member, title=base + " 1", body="the very same body again and again", post_to_profile="1")
    assert answer.status_code in (302, 303) and answer.headers["Location"].endswith("/notifications"), answer.get_data(as_text=True)[:200]

    # held, never banned: the account and its alt
    assert held(real_app, member) and held(real_app, alt)
    assert state(real_app, member)["is_banned"] == 0 and state(real_app, alt)["is_banned"] == 0 and state(real_app, member)["unban_utc"] == 0
    assert not entries(real_app, action="suspend_user", target_user_id=member.id) and not entries(real_app, action="ban_user", target_user_id=member.id)
    # the similar post was removed, as before, and the removal is a PUBLIC entry that names nobody, anonymous post or not
    (removed,) = rows(real_app, "SELECT s.is_banned, a.ban_reason FROM submissions s JOIN submissions_aux a ON a.id = s.id WHERE s.id = :i", i=first_id)       # ban_reason is the aux row's
    assert removed["is_banned"] and removed["ban_reason"].startswith("Automatic spam removal")
    removal = [e for e in entries(real_app, action="ban_post", target_submission_id=first_id)]
    assert len(removal) == 1 and removal[0]["audience"] == 0 and removal[0]["target_user_id"] is None and removal[0]["reason"] == "Automatic spam check"
    # the staff's entry names the account and the trigger, in counts only; the public line names nobody
    staff = staff_entry(real_app, member)
    assert staff["audience"] == 2 and staff["staff_note"] == "spam | 1 similar posts and 0 similar links in a day" and staff["actor_role"] == "system"
    public_line_names_nobody(real_app, staff, "Automatic spam check")
    assert len(entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])) == 1
    # the alt: a staff-only entry, no public line, no notice
    alt_entry = staff_entry(real_app, alt)
    assert alt_entry["audience"] >= 2 and alt_entry["staff_note"] == f"spam | alt of @{member.username}" and alt_entry["linked_entry_id"] == staff["id"]
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=alt_entry["id"]) and told(real_app, alt) == []
    # the person is told: the rule, the generic reason and the way to ask for a review
    (notice,) = told(real_app, member)
    assert APPEAL in notice and "Automatic spam check" in notice and "similar" not in notice and member.username not in notice
    # nothing public names either account
    (public_line,) = entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])
    visitor = real_app[0].test_client()
    page = get(visitor, f"/log/{public_line['id']}").get_data(as_text=True)                 # by id: a line dated to the hour sorts below the hour's other entries, off page one
    assert "Automatic spam check" in page
    page += get(visitor, "/log?type=sanction").get_data(as_text=True)
    assert member.username not in page and alt.username not in page
    profile = get(real_app[0].test_client(), f"/@{member.username}")
    assert profile.status_code == 200 and b"on hold" not in profile.data

    # a held account is refused its next try, and nothing more is written or sent
    again = submit(here, member, title=base + " 2", body="the very same body again and again", post_to_profile="1")
    assert again.status_code == 403 and "An automatic check put your account on hold" in again.get_json()["error"]
    assert len(entries(real_app, action="quarantine_user", target_user_id=member.id)) == 1 and len(told(real_app, member)) == 1
    assert len(entries(real_app, action="automatic_quarantine", linked_entry_id=staff["id"])) == 1


def test_a_burst_of_similar_comments_holds_the_account_and_its_alts_and_removes_them_publicly(real_app, make_member, monkeypatch):
    app, _ = real_app
    monkeypatch.setitem(app.config, "COMMENT_SPAM_COUNT_THRESHOLD", 1)
    member, alt = fresh(make_member), make_member()
    link_alt(member, alt)
    here = member.browser()
    _, post36, _ = made_post(real_app, here, member)
    base = "the very same comment over and over " + secrets.token_hex(4)
    made = [made_comment(real_app, here, member, post36, f"{base} {i}") for i in range(2)]

    answer = comment(here, member, post36, f"{base} 2")
    assert answer.status_code == 403 and answer.get_json() == {"error": "Too much spam!"}
    assert held(real_app, member) and held(real_app, alt) and state(real_app, member)["is_banned"] == 0
    for comment_id, _ in made:
        assert rows(real_app, "SELECT is_banned FROM comments WHERE id = :i", i=comment_id)[0]["is_banned"]
        (removal,) = entries(real_app, action="ban_comment", target_comment_id=comment_id)
        assert removal["audience"] == 0 and removal["target_user_id"] is None and removal["reason"] == "Automatic spam check"
    staff = staff_entry(real_app, member)
    assert staff["staff_note"] == "spam | 2 similar comments in a day" and staff["audience"] == 2
    public_line_names_nobody(real_app, staff, "Automatic spam check")
    assert staff_entry(real_app, alt)["staff_note"] == f"spam | alt of @{member.username}" and told(real_app, alt) == []
    (notice,) = told(real_app, member)
    assert APPEAL in notice


def test_a_burst_of_similar_comment_edits_holds_the_account_and_its_alts_and_removes_them_publicly(real_app, make_member, monkeypatch):
    app, _ = real_app
    monkeypatch.setitem(app.config, "SPAM_SIMILAR_COUNT_THRESHOLD", 1)
    member, alt = fresh(make_member), make_member()
    link_alt(member, alt)
    here = member.browser()
    _, post36, _ = made_post(real_app, here, member)
    base = "the very same comment over and over " + secrets.token_hex(4)
    made = [made_comment(real_app, here, member, post36, f"{base} {i}") for i in range(2)]

    answer = api(here, member, f"/edit_comment/{made[0][1]}", body=f"{base} 3")
    assert answer.status_code == 403 and answer.get_json() == {"error": "Too much spam!"}
    assert held(real_app, member) and held(real_app, alt) and state(real_app, member)["is_banned"] == 0
    assert all(rows(real_app, "SELECT is_banned FROM comments WHERE id = :i", i=cid)[0]["is_banned"] for cid, _ in made)
    assert all(entries(real_app, action="ban_comment", target_comment_id=cid)[0]["audience"] == 0 for cid, _ in made)
    staff = staff_entry(real_app, member)
    assert staff["staff_note"].startswith("spam | ") and "similar comments in a day" in staff["staff_note"]
    public_line_names_nobody(real_app, staff, "Automatic spam check")
    assert len(told(real_app, member)) == 1


def test_an_admin_is_never_held_by_a_burst_only_the_similar_post_goes(real_app, make_member, monkeypatch):
    app, _ = real_app
    monkeypatch.setitem(app.config, "SPAM_SIMILAR_COUNT_THRESHOLD", 1)
    admin = admin_of(make_member)
    set_user(admin, created_utc=int(time.time()) - 60)
    here = admin.browser()
    base = "the very same words again and again " + secrets.token_hex(4)
    first_id, _, _ = made_post(real_app, here, admin, title=base + " 0", body="the very same body again and again")
    answer = submit(here, admin, title=base + " 1", body="the very same body again and again", post_to_profile="1")
    assert answer.status_code in (302, 303)
    assert rows(real_app, "SELECT is_banned FROM submissions WHERE id = :i", i=first_id)[0]["is_banned"]
    assert state(real_app, admin)["quarantined_utc"] == 0 and state(real_app, admin)["quarantine_kind"] == 0 and state(real_app, admin)["is_banned"] == 0
    assert not entries(real_app, action="quarantine_user", target_user_id=admin.id) and told(real_app, admin) == []


# --- a link to a banned site ------------------------------------------------------------------------------------------------------

TRIGGERS = {4: ("link_malicious", 2), 7: ("link_minors", 3), 9: ("link_illegal", 2)}


@pytest.mark.parametrize("reason", [4, 7, 9])
def test_a_link_to_a_banned_site_in_a_new_post_holds_the_account_with_the_trigger_of_its_kind(real_app, make_member, reason):
    member = fresh(make_member)
    domain = banned_domain(real_app, reason)
    answer = submit(member.browser(), member, url=f"https://{domain}/x")
    assert answer.status_code == 400, answer.get_data(as_text=True)[:300]                      # refused as ever
    assert held(real_app, member)
    trigger, audience = TRIGGERS[reason]
    staff = staff_entry(real_app, member)
    assert staff["staff_note"] == f"{trigger} | a link to {domain} on a new post" and staff["audience"] == audience
    public_line_names_nobody(real_app, staff, "Automatic link check")
    (notice,) = told(real_app, member)
    assert APPEAL in notice and "Automatic link check" in notice and domain not in notice
    # nothing is made, and nothing in the old words
    assert not rows(real_app, "SELECT 1 FROM submissions WHERE author_id = :u", u=member.id)
    assert "suspended" not in " ".join(notices_of(real_app, member.id)).lower()


@pytest.mark.parametrize("reason", [4, 7, 9])
def test_a_link_to_a_banned_site_in_a_comment_holds_the_account_with_the_trigger_of_its_kind(real_app, make_member, reason):
    member = fresh(make_member)
    here = member.browser()
    _, post36, _ = made_post(real_app, here, member)
    domain = banned_domain(real_app, reason)
    answer = comment(here, member, post36, f"see [this](https://{domain}/z)")
    assert answer.status_code == 403                                                              # refused as ever: 403 when the link holds the account
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0
    trigger, audience = TRIGGERS[reason]
    staff = staff_entry(real_app, member)
    assert staff["staff_note"] == f"{trigger} | a link to {domain} in a comment" and staff["audience"] == audience
    public_line_names_nobody(real_app, staff, "Automatic link check")
    assert len(told(real_app, member)) == 1


@pytest.mark.parametrize("reason", [1, 3])
def test_another_kind_of_banned_site_is_only_refused(real_app, make_member, reason):
    member = fresh(make_member)
    here = member.browser()
    _, post36, _ = made_post(real_app, here, member)
    domain = banned_domain(real_app, reason)
    assert comment(here, member, post36, f"see [this](https://{domain}/z)").status_code == 400
    assert submit(here, member, url=f"https://{domain}/x").status_code == 400
    assert state(real_app, member)["quarantined_utc"] == 0 and not entries(real_app, action="quarantine_user", target_user_id=member.id)


@pytest.mark.parametrize("place", ["new post", "post edit", "comment edit"])
def test_a_malicious_link_in_the_text_holds_the_account_and_the_hold_stands(real_app, make_member, place):
    """The old ban was `v.ban(...)` then `abort(403)`, and the abort's rollback took the ban back; the refusal is an ordinary 403 now and the hold is committed first."""
    member = fresh(make_member)
    here = member.browser()
    domain = banned_domain(real_app, 4)
    text_link = f"see [this](https://{domain}/y)"
    if place == "new post":
        answer = submit(here, member, body=text_link)
    else:
        _, post36, title = made_post(real_app, here, member)
        if place == "post edit":
            answer = api(here, member, f"/edit_post/{post36}", title=title, body=text_link)
        else:
            _, comment36 = made_comment(real_app, here, member, post36, "an honest comment " + secrets.token_hex(4))
            answer = api(here, member, f"/edit_comment/{comment36}", body=text_link)
    assert answer.status_code == 403, answer.get_data(as_text=True)[:300]
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0, "the hold stood"
    staff = staff_entry(real_app, member)
    assert staff["staff_note"].startswith("link_malicious | a link to " + domain) and staff["audience"] == 2
    assert len(told(real_app, member)) == 1


@pytest.mark.parametrize("reason", [7, 9])
def test_only_the_worst_kind_of_link_in_the_text_of_a_post_holds_the_account(real_app, make_member, reason):
    """As it was: reasons 7 and 9 ban for a link, a comment or a comment's link, but a post's TEXT only for reason 4."""
    member = fresh(make_member)
    domain = banned_domain(real_app, reason)
    answer = submit(member.browser(), member, body=f"see [this](https://{domain}/y)")
    assert answer.status_code == 403
    assert state(real_app, member)["quarantined_utc"] == 0 and not entries(real_app, action="quarantine_user", target_user_id=member.id)


# --- an autoban bad link -------------------------------------------------------------------------------------------------------------

def test_an_autoban_bad_link_in_a_new_post_holds_the_account_and_sends_it_to_its_notifications(real_app, make_member):
    member = fresh(make_member)
    link = bad_link(real_app)
    answer = submit(member.browser(), member, url=f"https://{link}")
    assert answer.status_code in (302, 303) and answer.headers["Location"].endswith("/notifications")
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0
    staff = staff_entry(real_app, member)
    assert staff["staff_note"] == f"spam | a link on the autoban list ({link}) in a new post"
    public_line_names_nobody(real_app, staff, "Automatic spam check")
    assert len(told(real_app, member)) == 1


def test_an_autoban_bad_link_in_an_edit_holds_the_account_and_sends_it_to_its_notifications(real_app, make_member):
    member = fresh(make_member)
    here = member.browser()
    _, post36, title = made_post(real_app, here, member)
    link = bad_link(real_app)
    answer = api(here, member, f"/edit_post/{post36}", title=title, body=f"[x](https://{link})")
    assert answer.status_code in (302, 303) and answer.headers["Location"].endswith("/notifications")
    assert held(real_app, member)
    assert staff_entry(real_app, member)["staff_note"] == f"spam | a link on the autoban list ({link}) in an edit of a post"
    assert len(told(real_app, member)) == 1


def test_an_ordinary_bad_link_is_only_refused(real_app, make_member):
    member = fresh(make_member)
    link = bad_link(real_app, autoban=False)
    assert submit(member.browser(), member, url=f"https://{link}").status_code == 400
    assert state(real_app, member)["quarantined_utc"] == 0


# --- the profile ---------------------------------------------------------------------------------------------------------------------------

def test_seo_spam_in_a_new_accounts_bio_in_settings_holds_it_puts_the_earlier_bio_back_and_tells_the_person(real_app, make_member):
    member = fresh(make_member)
    answer = api(member.browser(), member, "/api/vue/settings/profile", bio="[my page](https://spam-ci.example/)")
    assert answer.status_code == 200 and answer.get_json() == {"message": "Your bio has been updated."}
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0
    assert "spam-ci.example" not in (rows(real_app, "SELECT bio_html FROM users WHERE id = :u", u=member.id)[0]["bio_html"] or ""), "the links never reach the profile"
    staff = staff_entry(real_app, member)
    assert staff["staff_note"] == "seo_spam | links in the bio of a new account" and staff["audience"] == 2
    public_line_names_nobody(real_app, staff, "Automatic spam check")
    (notice,) = told(real_app, member)
    assert APPEAL in notice


def test_seo_spam_in_the_popup_holds_the_account_puts_the_earlier_bio_back_and_tells_the_person(real_app, make_member):
    """The pop-up's save goes on after the hold (its pictures are taken too), so the person is told once the request's commit has gone through."""
    member = fresh(make_member)
    client = member.browser()
    answer = client.post("/settings/profile/edit", base_url=BASE, headers=JSON, environ_base={"REMOTE_ADDR": address()},
                         data={"formkey": member.formkey(client), "bio": "[my page](https://spam-ci.example/)"})
    assert answer.status_code == 200 and answer.get_json()["ok"] and "bio" in answer.get_json()["saved"]
    assert held(real_app, member)
    assert "spam-ci.example" not in (rows(real_app, "SELECT bio_html FROM users WHERE id = :u", u=member.id)[0]["bio_html"] or ""), "the links never reach the profile"
    for _ in range(50):
        if told(real_app, member):
            break
        gevent.sleep(0.1)
    assert len(told(real_app, member)) == 1
    assert staff_entry(real_app, member)["staff_note"] == "seo_spam | links in the bio of a new account"


def test_a_bio_with_a_malicious_link_holds_the_account_in_settings_and_the_refusal_stands(real_app, make_member):
    member = fresh(make_member)
    domain = banned_domain(real_app, 4)
    answer = api(member.browser(), member, "/api/vue/settings/profile", bio=f"[a](https://{domain}/)")
    assert answer.status_code == 401 and domain in answer.get_json()["error"]
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0
    assert staff_entry(real_app, member)["staff_note"].startswith("link_malicious | ")
    assert len(told(real_app, member)) == 1


# --- a shared draft's publish -------------------------------------------------------------------------------------------------------------------

def shared_post(real_app, member, words="the first words"):
    """A published shared draft that `member` is the one author of, with a newer revision waiting to be published: (doc id, post id, the post's text)."""
    made = post(member.browser(), member, "/collab/new")
    assert made.status_code == 302, made.status_code
    doc = int(made.headers["Location"].rstrip("/").rsplit("/", 1)[-1])
    board_id, _ = guild(real_app)
    post_id, _ = add_post(member, board_id, title="A shared post " + secrets.token_hex(3), body=f"<p>{words}</p>")
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE collab_docs SET post_id = :p, title = :t, revision = 2, published_revision = 1 WHERE id = :d"),
               {"p": post_id, "t": "A shared post " + secrets.token_hex(3), "d": doc})
    db.commit()
    return doc, post_id, words


def draft_says(member, doc, body):
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE collab_docs SET body = :b WHERE id = :d"), {"b": body, "d": doc})
    db.commit()


def publish(member, doc):
    client = member.browser()
    return client.post(f"/api/collab/{doc}/publish", base_url=BASE, headers=JSON, environ_base={"REMOTE_ADDR": address()}, data={"formkey": member.formkey(client)})


def nothing_was_published(real_app, doc, post_id, words):
    assert rows(real_app, "SELECT count(*) AS n FROM collab_publishes WHERE doc_id = :d", d=doc)[0]["n"] == 0, "no revision was recorded"
    assert rows(real_app, "SELECT published_revision FROM collab_docs WHERE id = :d", d=doc)[0]["published_revision"] == 1
    assert rows(real_app, "SELECT body FROM submissions_aux WHERE id = :p", p=post_id)[0]["body"] == words, "the live post is as it was"


@pytest.mark.parametrize("link", ["autoban", "malicious"])
def test_an_ordinary_publisher_with_a_bad_link_in_the_draft_is_held_and_nothing_is_published(real_app, make_member, link):
    """The hold is committed inside the edit, before the refusal (a publish rolls a refused edit back), and the publish then refuses the held account."""
    member = fresh(make_member)
    doc, post_id, was = shared_post(real_app, member)
    if link == "autoban":
        found = bad_link(real_app)
        draft_says(member, doc, f"see [x](https://{found})")
    else:
        found = banned_domain(real_app, 4)
        draft_says(member, doc, f"see [x](https://{found}/y)")

    answer = publish(member, doc)

    assert answer.status_code == 400, answer.get_data(as_text=True)[:300]
    if link == "autoban":
        assert answer.get_json()["error"] == "Your account can't publish right now."
    else:
        assert found in answer.get_json()["error"]                                       # the ordinary refusal of a malicious link, as an edit gets it
    assert held(real_app, member) and state(real_app, member)["is_banned"] == 0
    nothing_was_published(real_app, doc, post_id, was)
    staff = staff_entry(real_app, member)
    assert staff["staff_note"].startswith("spam | a link on the autoban list" if link == "autoban" else "link_malicious | a link to " + found)
    assert len(told(real_app, member)) == 1


@pytest.mark.parametrize("link", ["autoban", "malicious"])
def test_an_admin_publishing_a_draft_with_a_bad_link_is_refused_like_anyone_and_nothing_is_published(real_app, make_member, link):
    """An admin is never held, so hold() changes nothing: the autoban link must then answer the ordinary refusal and not the redirect to the notifications, which
    the publish took for an edit that went through and recorded as published."""
    admin = admin_of(make_member)
    doc, post_id, was = shared_post(real_app, admin)
    if link == "autoban":
        found = bad_link(real_app)
        draft_says(admin, doc, f"see [x](https://{found})")
    else:
        found = banned_domain(real_app, 4)
        draft_says(admin, doc, f"see [x](https://{found}/y)")

    answer = publish(admin, doc)

    assert answer.status_code == 400, answer.get_data(as_text=True)[:300]
    error = answer.get_json()["error"]
    assert ("is not allowed" in error) if link == "autoban" else (found in error), error
    assert state(real_app, admin)["quarantined_utc"] == 0 and state(real_app, admin)["is_banned"] == 0
    nothing_was_published(real_app, doc, post_id, was)
    assert not entries(real_app, action="quarantine_user", target_user_id=admin.id) and told(real_app, admin) == []


@pytest.mark.parametrize("place", ["new post", "post edit"])
def test_an_admin_with_an_autoban_link_gets_the_ordinary_refusal_and_not_a_bare_redirect(real_app, make_member, place):
    admin = admin_of(make_member)
    here = admin.browser()
    link = bad_link(real_app)
    if place == "new post":
        answer = submit(here, admin, url=f"https://{link}")
        assert answer.status_code == 400 and link in answer.get_data(as_text=True)
    else:
        _, post36, title = made_post(real_app, here, admin)
        answer = api(here, admin, f"/edit_post/{post36}", title=title, body=f"[x](https://{link})")
        assert answer.status_code == 403 and "is not allowed" in answer.get_json()["error"]
    assert state(real_app, admin)["quarantined_utc"] == 0 and not entries(real_app, action="quarantine_user", target_user_id=admin.id)


# --- an account the signup guard holds ----------------------------------------------------------------------------------------------------------

def held_by_the_signup_guard(member):
    """Kind 0: the signup guard's hold, with its undecided review."""
    db = member.db_session()
    db.rollback()
    db.execute(text("UPDATE users SET quarantined_utc = :now, quarantine_kind = 0 WHERE id = :u"), {"now": int(time.time()) - 5, "u": member.id})
    db.execute(text("INSERT INTO signup_reviews (user_id, created_utc, score, reasons, network, name_shape, decision) VALUES (:u, :now, 60, 'fast', 'n', 's', '')"),
               {"u": member.id, "now": int(time.time())})
    db.commit()


@pytest.mark.parametrize("trigger", ["seo", "malicious"])
def test_a_site_check_turns_the_signup_guards_hold_into_an_automatic_one_that_no_email_releases(real_app, make_member, trigger):
    from ruqqus.helpers.security import generate_hash
    member = fresh(make_member)
    held_by_the_signup_guard(member)
    assert state(real_app, member)["quarantine_kind"] == 0 and state(real_app, member)["quarantined_utc"] > 0
    if trigger == "seo":
        answer = api(member.browser(), member, "/api/vue/settings/profile", bio="[my page](https://spam-ci.example/)")
        assert answer.status_code == 200
    else:
        domain = banned_domain(real_app, 4)
        answer = api(member.browser(), member, "/api/vue/settings/profile", bio=f"[a](https://{domain}/)")
        assert answer.status_code == 401

    assert held(real_app, member), "kind 1 now, and not banned"
    assert staff_entry(real_app, member)["staff_note"].startswith("seo_spam | " if trigger == "seo" else "link_malicious | ")
    assert len(told(real_app, member)) == 1

    email = f"guard{secrets.token_hex(3)}@example.com"
    now = int(time.time())
    link = f"/activate?email={email}&id={member.id}&time={now}&token={generate_hash(f'{email}+{member.id}+{now}')}"
    assert member.browser().get(link, base_url=BASE, headers=HEADERS, environ_base={"REMOTE_ADDR": address()}).status_code == 200
    assert held(real_app, member), "verifying an email did not release it"
    assert rows(real_app, "SELECT is_activated FROM users WHERE id = :u", u=member.id)[0]["is_activated"], "the address itself was verified"


# --- quiet and idempotent -----------------------------------------------------------------------------------------------------------------------

def test_a_second_check_on_an_account_already_held_writes_nothing_and_tells_nobody(real_app, make_member):
    member = fresh(make_member)
    first = api(member.browser(), member, "/api/vue/settings/profile", bio="[my page](https://spam-ci.example/)")
    assert first.status_code == 200 and held(real_app, member)
    since = state(real_app, member)["quarantined_utc"]
    about = len(entries(real_app, target_user_id=member.id))
    public = len(entries(real_app, action="automatic_quarantine"))
    assert len(told(real_app, member)) == 1

    second = api(member.browser(), member, "/api/vue/settings/profile", bio="[another page](https://spam-two.example/)")

    assert second.status_code == 200 and second.get_json() == {"message": "Your bio has been updated."}
    assert "spam-two.example" not in (rows(real_app, "SELECT bio_html FROM users WHERE id = :u", u=member.id)[0]["bio_html"] or ""), "a held account's bio is put back too"
    assert state(real_app, member)["quarantined_utc"] == since, "the hold was not placed again"
    assert len(entries(real_app, target_user_id=member.id)) == about and len(entries(real_app, action="automatic_quarantine")) == public
    gevent.sleep(0.2)
    assert len(told(real_app, member)) == 1


def test_a_cdn_451_on_a_held_accounts_picture_still_leaves_its_level_4_record_and_nothing_else(real_app, make_member, monkeypatch):
    """The pop-up takes a held account's pictures (a hold is not a suspension), so the child-abuse list's answer for one of them must not vanish: the hold itself
    stays quiet (no second entry, no public line, no notice), the picture's own record is a level-4 detail linked to the hold the account is on."""
    from types import SimpleNamespace

    from PIL import Image

    from ruqqus.helpers import action_log, aws
    app, _ = real_app
    member = fresh(make_member)
    scanned = []
    monkeypatch.setitem(app.config, "S3_BUCKET", "bucket.example")
    monkeypatch.setattr(aws, "BUCKET", "bucket.example")
    monkeypatch.setattr(aws, "upload_file", lambda name, file, resize=None: None)
    monkeypatch.setattr(aws, "delete_file", lambda name: None)
    monkeypatch.setattr("ruqqus.routes.profile_edit_pages.check_csam_url", lambda url, v, remove: scanned.append(url))
    picture = io.BytesIO()
    Image.new("RGB", (24, 12), "red").save(picture, "PNG")
    client = member.browser()
    answer = client.post("/settings/profile/edit", base_url=BASE, headers=JSON, environ_base={"REMOTE_ADDR": address()}, content_type="multipart/form-data",
                         data={"formkey": member.formkey(client), "bio": "[my page](https://spam-ci.example/)", "profile": (io.BytesIO(picture.getvalue()), "me.png")})
    gevent.sleep(0.05)
    assert answer.status_code == 200 and sorted(answer.get_json()["saved"]) == ["bio", "profile"], answer.get_data(as_text=True)[:300]
    assert held(real_app, member) and len(scanned) == 1
    for _ in range(50):
        if told(real_app, member):
            break
        gevent.sleep(0.1)
    staff = staff_entry(real_app, member)
    assert len(told(real_app, member)) == 1 and not entries(real_app, action="suppress_content", target_user_id=member.id)

    removed = []
    monkeypatch.setattr(aws.requests, "get", lambda *a, **k: SimpleNamespace(status_code=451, iter_content=lambda n: [b"x"]))
    aws.check_csam_url(scanned[0], member.user(), lambda: removed.append(1))

    assert removed == [1]                                                                                  # the picture goes
    (detail,) = entries(real_app, action="suppress_content", target_user_id=member.id)
    assert detail["audience"] == action_log.LEGAL and detail["linked_entry_id"] == staff["id"] and "451" in detail["staff_note"]
    assert len(entries(real_app, action="quarantine_user", target_user_id=member.id)) == 1 and len(told(real_app, member)) == 1
    assert state(real_app, member)["is_banned"] == 0 and held(real_app, member)
