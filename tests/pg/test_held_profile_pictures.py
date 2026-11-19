"""What an account the site's own checks put on hold may do to its profile, on the real app and Postgres (routes/settings.py, routes/profile_edit_pages.py;
helpers/quarantine.py `picture_refusal`, `User.can_upload_avatar` / `can_upload_banner`). The owner, 2026-11-17 and 18: an automatic check only QUARANTINES, and the
suspension it replaced had closed the picture routes (`is_not_banned`).

- a held account (kind 1) can't upload a profile picture or a banner, in Settings or in the Edit profile pop-up: it is told so in the site's own words for a hold
  (not the Tor sentence), nothing reaches the bucket, and the rest of the pop-up's save changes nothing either (a refusal changes nothing);
- its text is still its own (name, details, bio) and so is taking a picture down;
- the signup guard's hold (kind 0) keeps its pictures: that hold waits for a person to look at the account;
- the SEO-spam check puts a new account whose bio holds a link on hold and the bio that tripped it is NOT kept: the account goes back to the one it had (in Settings and
  in the pop-up), the rest of the save stays, an account already held reverts too, and an account no hold applies to (an admin) keeps what it wrote."""
import importlib
import io
import time

import gevent
import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import BASE, address, admin_of, rows

HEADERS = {"User-Agent": "ci-held-profile-pictures"}
HELD_WORDS = "An automatic check put your account on hold"
SPAM_BIO = "[my page](https://spam-ci.example/)"


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


class Storage:
    """What the bucket was asked, in order: ("upload", key) and ("delete", key)."""

    def __init__(self):
        self.calls = []

    def keys(self, what):
        return [key for kind, key in self.calls if kind == what]


@pytest.fixture
def storage(real_app, monkeypatch):
    from ruqqus.helpers import aws
    fake = Storage()
    monkeypatch.setattr(aws, "upload_file", lambda name, file, resize=None: fake.calls.append(("upload", name)))
    monkeypatch.setattr(aws, "delete_file", lambda name: fake.calls.append(("delete", name)))
    for module in ("profile_edit_pages", "settings"):
        # the module itself: `ruqqus.routes.settings` as an attribute path reaches a function the package star-imports
        monkeypatch.setattr(importlib.import_module(f"ruqqus.routes.{module}"), "check_csam_url", lambda url, v, remove: None)
    return fake


def png():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (24, 12), "red").save(out, "PNG")
    return out.getvalue()


def update(member, **columns):
    from ruqqus.classes import User
    db = member.db_session()
    db.rollback()
    db.query(User).filter_by(id=member.id).update(columns, synchronize_session=False)
    db.commit()


def hold_by_a_check(real_app, member):
    """Kind 1: an automatic check's hold, through the module that is the only place that writes one."""
    from ruqqus.classes import User
    from ruqqus.helpers import auto_quarantine
    _, db_session = real_app
    db = db_session()
    db.rollback()
    assert auto_quarantine.hold(db, db.get(User, member.id), "spam", detail="six similar posts in a minute", notify=False)
    db.commit()


def hold_by_the_signup_guard(member):
    """Kind 0: the signup guard's hold."""
    update(member, quarantined_utc=int(time.time()) - 5, quarantine_kind=0)


def held_member(real_app, make_member):
    member = make_member()
    hold_by_a_check(real_app, member)
    return member


def state(real_app, member):
    return rows(real_app, "SELECT quarantined_utc, quarantine_kind, is_banned, bio, bio_html, bio_severity, profile_job, has_profile, has_banner "
                          "FROM users WHERE id = :u", u=member.id)[0]


def edit(client, member, files=None, **fields):
    """A multipart POST to the Edit profile pop-up's route."""
    data = {"formkey": member.formkey(client), **fields}
    for name, (payload, filename) in (files or {}).items():
        data[name] = (io.BytesIO(payload), filename)
    answer = client.post("/settings/profile/edit", base_url=BASE, headers=HEADERS, data=data, content_type="multipart/form-data",
                         environ_base={"REMOTE_ADDR": address()})
    gevent.sleep(0.05)              # the after-commit work (an old picture's file, a notice) runs off the request
    return answer


def settings(client, member, **fields):
    """The Settings > Profile forms, the JSON path."""
    answer = client.post("/api/vue/settings/profile", base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(client), **fields},
                         environ_base={"REMOTE_ADDR": address()})
    gevent.sleep(0.05)
    return answer


def send_picture(client, member, path, field):
    """A picture sent through one of Settings' own forms (a navigated multipart POST, as the page's file input makes it)."""
    answer = client.post(path, base_url=BASE, headers={**HEADERS, "Sec-Fetch-Mode": "navigate"}, content_type="multipart/form-data",
                         data={"formkey": member.formkey(client), field: (io.BytesIO(png()), "me.png")}, environ_base={"REMOTE_ADDR": address()})
    gevent.sleep(0.05)
    return answer


def recent_with_a_bio(member, bio="my first bio"):
    """An account a minute old with no post or comment (the SEO-spam rule's account) and a bio of its own."""
    update(member, created_utc=int(time.time()) - 60, bio=bio, bio_html=f"<p>{bio}</p>")


# --- Settings: the picture forms --------------------------------------------------------------------------------------------------------

def test_a_held_accounts_picture_and_banner_forms_in_settings_refuse_it_in_the_words_of_a_hold(real_app, make_member, storage):
    from ruqqus.__main__ import r
    from ruqqus.helpers import signup_signals
    member = held_member(real_app, make_member)
    here = member.browser()
    for path, field in (("/settings/images/profile", "profile"), ("/settings/images/banner", "banner")):
        answer = send_picture(here, member, path, field)
        assert answer.status_code == 403, path
        assert HELD_WORDS.encode() in answer.data and b'href="/appeal/quarantine"' in answer.data and b"Ask for a review" in answer.data, path
        assert b"upload images via Tor" not in answer.data, "a hold is not a Tor refusal"
    assert storage.calls == []
    now = state(real_app, member)
    assert not now["has_profile"] and not now["has_banner"] and now["quarantine_kind"] == 1 and not now["is_banned"]
    user = member.user()
    assert not user.can_upload_avatar and not user.can_upload_banner
    assert signup_signals.attempts(r, member.id).get("other") == 2, "what it tried is counted, as for any held account"


def test_a_held_account_still_takes_its_pictures_down_in_settings(real_app, make_member, storage):
    member = held_member(real_app, make_member)
    update(member, has_profile=True, profile_nonce=3, profile_set_utc=int(time.time()), has_banner=True, banner_nonce=2, banner_set_utc=int(time.time()))
    here = member.browser()
    for path in ("/settings/delete/profile", "/settings/delete/banner"):
        answer = here.post(path, base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(here)}, environ_base={"REMOTE_ADDR": address()})
        assert answer.status_code == 200, path
    now = state(real_app, member)
    assert not now["has_profile"] and not now["has_banner"] and len(storage.keys("delete")) == 2



def bot_of(real_app, owner, account):
    """A member bot (the owner runs it) waiting for approval: its page is the owner's to change."""
    from ruqqus.helpers import bot_store, bots
    db = owner.db_session()
    db.execute(text("UPDATE users SET is_bot = true, bot_owner_id = :o WHERE id = :u"), {"o": owner.id, "u": account.id})
    bot_store.add_bot(db, account.id, owner.id, account.username, "A feed of things.", [], int(time.time()), kind=bots.MEMBER, proposed_by=owner.id)
    db.commit()


def test_a_held_owner_cant_store_a_picture_on_their_bots_account_either_but_can_take_one_down(real_app, make_member, storage):
    """A bot account's picture is public and the owner's upload: the hold stops it as it stops their own (the fence skips everything under /settings)."""
    owner, account = held_member(real_app, make_member), make_member()
    bot_of(real_app, owner, account)
    update(account, has_profile=True, profile_nonce=1, profile_set_utc=int(time.time()))
    here = owner.browser()
    for which, field in (("picture", "profile"), ("banner", "banner")):
        answer = here.post(f"/settings/bots/{account.username}/image/{which}", base_url=BASE, headers={**HEADERS, "Sec-Fetch-Mode": "navigate"},
                           content_type="multipart/form-data", data={"formkey": owner.formkey(here), field: (io.BytesIO(png()), "me.png")},
                           environ_base={"REMOTE_ADDR": address()})
        assert answer.status_code == 302 and "error=" in answer.headers["Location"] and "on%20hold" in answer.headers["Location"], (which, answer.headers["Location"])
    assert storage.keys("upload") == []
    gone = here.post(f"/settings/bots/{account.username}/image/picture/remove", base_url=BASE, headers=HEADERS, data={"formkey": owner.formkey(here)},
                     environ_base={"REMOTE_ADDR": address()})
    assert gone.status_code == 302 and "msg=" in gone.headers["Location"], "taking a picture down stays open"


def test_a_held_member_cant_propose_a_bot_with_a_picture(real_app, make_member, storage):
    owner = held_member(real_app, make_member)
    update(owner, created_utc=int(time.time()) - 40 * 86400)            # old enough to run a bot: the picture is what is refused
    here = owner.browser()
    answer = here.post("/settings/bots/apply", base_url=BASE, headers={**HEADERS, "Sec-Fetch-Mode": "navigate"}, content_type="multipart/form-data",
                       data={"formkey": owner.formkey(here), "kind": "member", "username": f"held{int(time.time()) % 100000}bot", "purpose": "posts things",
                             "sources": "https://example.org/feed.xml", "profile": (io.BytesIO(png()), "me.png")},
                       environ_base={"REMOTE_ADDR": address()})
    location = answer.headers.get("Location", "")
    assert answer.status_code == 302 and location.startswith("/settings/bots?error=") and "on%20hold" in location, location
    assert storage.keys("upload") == []

# --- the pop-up -------------------------------------------------------------------------------------------------------------------------

def test_the_pop_up_refuses_a_held_accounts_pictures_in_the_words_of_a_hold_and_changes_nothing(real_app, make_member, storage):
    member = held_member(real_app, make_member)
    here = member.browser()
    for kind, files in (("profile", {"profile": (png(), "me.png")}), ("banner", {"banner": (png(), "bg.png")})):
        answer = edit(here, member, files=files, profile_job="not saved either")
        body = answer.get_json()
        assert answer.status_code == 403 and not body["ok"], kind
        assert body["errors"][kind].startswith(HELD_WORDS) and "Tor" not in body["errors"][kind], kind
    both = edit(here, member, files={"profile": (png(), "me.png"), "banner": (png(), "bg.png")})
    assert both.status_code == 403 and set(both.get_json()["errors"]) == {"profile", "banner"}
    assert storage.calls == []
    now = state(real_app, member)
    assert not now["has_profile"] and not now["has_banner"] and not now["profile_job"]


def test_the_pop_up_still_saves_a_held_accounts_text_and_takes_its_pictures_down(real_app, make_member, storage):
    member = held_member(real_app, make_member)
    here = member.browser()
    text_only = edit(here, member, profile_job="Still writing", bio="a plain bio")
    body = text_only.get_json()
    assert text_only.status_code == 200 and body["ok"] and sorted(body["saved"]) == ["bio", "profile_job"], body
    now = state(real_app, member)
    assert now["profile_job"] == "Still writing" and now["bio"] == "a plain bio" and now["quarantine_kind"] == 1

    update(member, has_profile=True, profile_nonce=3, profile_set_utc=int(time.time()), has_banner=True, banner_nonce=2, banner_set_utc=int(time.time()))
    removed = edit(here, member, remove_profile="1", remove_banner="1")
    assert removed.status_code == 200 and sorted(removed.get_json()["saved"]) == ["banner", "profile"]
    now = state(real_app, member)
    assert not now["has_profile"] and not now["has_banner"] and len(storage.keys("delete")) == 2 and storage.keys("upload") == []


def test_the_signup_guards_hold_keeps_its_pictures_in_settings_and_in_the_pop_up(real_app, make_member, storage):
    """That hold waits for a person to look at the account (helpers/quarantine.py): only the checks' own hold closes the pictures."""
    member = make_member()
    hold_by_the_signup_guard(member)
    here = member.browser()
    answer = edit(here, member, files={"profile": (png(), "me.png")})
    assert answer.status_code == 200 and answer.get_json()["saved"] == ["profile"], answer.get_json()
    page = send_picture(here, member, "/settings/images/banner", "banner")
    assert page.status_code == 200 and b"Banner successfully updated." in page.data
    now = state(real_app, member)
    assert now["has_profile"] and now["has_banner"] and now["quarantine_kind"] == 0
    assert len(storage.keys("upload")) == 2


# --- the SEO-spam hold keeps no bio that tripped it ----------------------------------------------------------------------------------------

def test_the_seo_spam_hold_in_the_pop_up_puts_the_old_bio_back_and_keeps_the_rest_of_the_save(real_app, make_member):
    member = make_member()
    recent_with_a_bio(member)
    answer = edit(member.browser(), member, bio=SPAM_BIO, profile_job="Writer")
    body = answer.get_json()
    said = str(body).lower()
    assert answer.status_code == 200 and body["ok"] and "hold" not in said and "quarantin" not in said, body
    now = state(real_app, member)
    assert now["quarantine_kind"] == 1 and now["quarantined_utc"] > 0 and not now["is_banned"]
    assert now["bio"] == "my first bio" and now["bio_html"] == "<p>my first bio</p>" and "spam-ci.example" not in (now["bio_html"] or "")
    assert now["profile_job"] == "Writer", "a field of the same save that tripped nothing is kept"
    assert rows(real_app, "SELECT count(*) AS n FROM log_entries WHERE action = 'quarantine_user' AND target_user_id = :u", u=member.id)[0]["n"] == 1


def test_the_seo_spam_hold_in_settings_puts_the_old_bio_back_and_the_answer_is_as_for_any_save(real_app, make_member):
    member = make_member()
    recent_with_a_bio(member)
    answer = settings(member.browser(), member, bio=SPAM_BIO)
    assert answer.status_code == 200 and answer.get_json() == {"message": "Your bio has been updated."}
    now = state(real_app, member)
    assert now["quarantine_kind"] == 1 and now["quarantined_utc"] > 0 and not now["is_banned"]
    assert now["bio"] == "my first bio" and now["bio_html"] == "<p>my first bio</p>"


def test_a_new_account_with_no_bio_before_has_none_after_the_seo_spam_hold(real_app, make_member):
    member = make_member()
    update(member, created_utc=int(time.time()) - 60)
    assert edit(member.browser(), member, bio=SPAM_BIO).status_code == 200
    now = state(real_app, member)
    assert now["quarantine_kind"] == 1 and not now["bio"] and "spam-ci.example" not in (now["bio_html"] or "")


@pytest.mark.parametrize("through", ["pop-up", "settings"])
def test_an_account_already_held_by_a_check_cannot_publish_a_link_bio_either(real_app, make_member, through):
    member = make_member()
    recent_with_a_bio(member)
    hold_by_a_check(real_app, member)
    before = state(real_app, member)
    if through == "pop-up":
        assert edit(member.browser(), member, bio=SPAM_BIO).status_code == 200
    else:
        assert settings(member.browser(), member, bio=SPAM_BIO).status_code == 200
    now = state(real_app, member)
    assert now["bio"] == "my first bio" and now["bio_html"] == "<p>my first bio</p>"
    assert now["quarantined_utc"] == before["quarantined_utc"], "the second check changed nothing about the hold"
    assert rows(real_app, "SELECT count(*) AS n FROM log_entries WHERE action = 'quarantine_user' AND target_user_id = :u", u=member.id)[0]["n"] == 1


def test_an_account_no_hold_applies_to_keeps_the_bio_it_wrote(real_app, make_member):
    """An admin is never held (helpers/auto_quarantine.py), so nothing takes its bio back."""
    admin = admin_of(make_member)
    update(admin, created_utc=int(time.time()) - 60)
    answer = edit(admin.browser(), admin, bio=SPAM_BIO)
    assert answer.status_code == 200 and answer.get_json()["ok"]
    now = state(real_app, admin)
    assert now["quarantined_utc"] == 0 and "spam-ci.example" in now["bio_html"]
