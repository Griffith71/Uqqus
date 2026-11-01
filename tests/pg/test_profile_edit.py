"""The Edit profile pop-up's one save (POST /settings/profile/edit, routes/profile_edit_pages.py) on the real app, and the
Settings > Profile forms that now call the same checks (helpers/profile_edit.py). What they keep: everything is checked
before anything is assigned (a refusal changes nothing), a field that did not change is never checked again, a picture
is stored before the old one goes (and a failure leaves the old one), and a malicious-domain bio still earns its ban.
The old picture's file is taken away AFTER the commit, in a greenlet (`g.after_commit`, run by __main__.after_request), so
the helpers below yield to it before a test looks at what the bucket was asked.
The bucket and the CSAM scan are faked: nothing here talks to S3 or Cloudflare."""
import io
import secrets
import time

import gevent
import pytest

BASE = "http://localhost"
HEADERS = {"User-Agent": "ci-profile-edit"}
OLD_ACCOUNT = 1500000000            # an old account; pictures no longer depend on age or Rep (User.can_upload_avatar)


def address():
    """A client address no earlier run used: the default limit per address and the 429 counts live in Redis."""
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def edit(client, member, files=None, formkey=True, headers=None, **fields):
    """A multipart POST to the pop-up's route, with the member's form key (formkey=False sends none)."""
    data = dict(fields)
    if formkey is True:
        data["formkey"] = member.formkey(client)
    elif formkey:
        data["formkey"] = formkey
    for name, (payload, filename) in (files or {}).items():
        data[name] = (io.BytesIO(payload), filename)
    answer = client.post("/settings/profile/edit", base_url=BASE, headers={**HEADERS, **(headers or {})}, data=data,
                         content_type="multipart/form-data", environ_base={"REMOTE_ADDR": address()})
    gevent.sleep(0.05)              # the after-commit work (an old picture's file) runs off the request
    return answer


def settings(client, member, path="/api/vue/settings/profile", **fields):
    """The Settings > Profile forms: the api path answers JSON, /settings/profile the page."""
    answer = client.post(path, base_url=BASE, headers=HEADERS, data={"formkey": member.formkey(client), **fields},
                         environ_base={"REMOTE_ADDR": address()})
    gevent.sleep(0.05)
    return answer


def png(size=(24, 12), colour="red"):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def update(member, **columns):
    from ruqqus.classes import User
    db = member.db_session()
    db.query(User).filter_by(id=member.id).update(columns, synchronize_session=False)
    db.commit()


def uploader(make_member):
    """A member old enough to upload both pictures."""
    member = make_member()
    update(member, created_utc=OLD_ACCOUNT)
    return member


def people_state(member):
    user = member.user()
    return {key: getattr(user, key) for key in ("display_name", "bio", "bio_html", "profile_job", "profile_location",
                                                 "profile_website", "has_profile", "has_banner", "profile_nonce",
                                                 "banner_nonce")}


class Storage:
    """What the bucket was asked, in order: ("upload", key) and ("delete", key)."""

    def __init__(self):
        self.calls = []
        self.fail = False               # every upload raises
        self.fail_on = None             # ... or only a key holding this text
        self.upload_delay = 0           # seconds an upload takes (to make two requests overlap)
        self.on_delete = None           # called with the key when the bucket is asked to delete it
        self.scanned = []

    def keys(self, what):
        return [key for kind, key in self.calls if kind == what]


@pytest.fixture
def storage(real_app, monkeypatch):
    from ruqqus.helpers import aws
    fake = Storage()

    def upload_file(name, file, resize=None):
        if fake.upload_delay:
            gevent.sleep(fake.upload_delay)
        if fake.fail or (fake.fail_on and fake.fail_on in name):
            raise RuntimeError("the bucket is down")
        fake.calls.append(("upload", name))

    def delete_file(name):
        fake.calls.append(("delete", name))
        if fake.on_delete:
            fake.on_delete(name)

    monkeypatch.setattr(aws, "upload_file", upload_file)
    monkeypatch.setattr(aws, "delete_file", delete_file)
    monkeypatch.setattr("ruqqus.routes.profile_edit_pages.check_csam_url", lambda url, v, remove: fake.scanned.append(url))
    return fake


@pytest.fixture
def banned_domains(real_app):
    """Two linked-site rules for a test (reason 4 earns a ban, reason 1 only a refusal), taken away again."""
    from ruqqus.classes import Domain
    app, db_session = real_app
    names = ("worse-ci.example", "short-ci.example")
    db = db_session()
    db.query(Domain).filter(Domain.domain.in_(names)).delete(synchronize_session=False)
    db.add(Domain(domain=names[0], can_comment=False, can_submit=False, reason=4))
    db.add(Domain(domain=names[1], can_comment=False, can_submit=False, reason=1))
    db.commit()
    yield names
    db = db_session()
    db.query(Domain).filter(Domain.domain.in_(names)).delete(synchronize_session=False)
    db.commit()


# --- the text: one save, atomic, only what changed ---------------------------------------------------------------------

def test_one_save_changes_the_name_the_bio_and_the_details_together(make_member):
    member = make_member()
    here = member.browser()
    answer = edit(here, member, display_name="Ada L", bio="I write **notes**", profile_job="Engineer",
                  profile_location="Berlin", profile_website="example.com")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and body["changed"], body
    assert set(body["saved"]) == {"display_name", "bio", "profile_job", "profile_location", "profile_website"}
    assert body["values"]["profile_website"] == "https://example.com"        # as it is stored, not as typed
    user = member.user()
    assert (user.display_name, user.profile_job, user.profile_location, user.profile_website) == \
        ("Ada L", "Engineer", "Berlin", "https://example.com")
    assert user.bio == "I write **notes**" and "<strong>notes</strong>" in user.bio_html
    assert user.name_severity == 0 and user.bio_severity == 0            # re-stamped by the word filter, and clean


def test_the_word_filter_rates_the_new_name_once_after_the_assignments(make_member):
    member = make_member()
    answer = edit(member.browser(), member, display_name="fuck off", profile_job="shit")
    assert answer.status_code == 200 and answer.get_json()["ok"]
    user = member.user()
    assert user.name_severity > 0 and user.bio_severity > 0              # the name with the username, the job with the bio


def test_a_refusal_changes_nothing_not_even_the_fields_that_were_fine(make_member):
    member = make_member()
    before = people_state(member)
    here = member.browser()
    bad_bio = edit(here, member, display_name="Fine Name", bio="x" * 257)
    assert bad_bio.status_code == 400 and list(bad_bio.get_json()["errors"]) == ["bio"]
    assert people_state(member) == before

    # a bio that is short as typed but would not fit its column once rendered (users.bio_html is varchar(1000))
    heavy = edit(here, member, display_name="Fine Name", bio="<https://a.io/b> " * 14)
    heavy_body = heavy.get_json()
    assert heavy.status_code == 400 and "formatting" in heavy_body["errors"]["bio"], heavy_body
    assert people_state(member) == before

    wrong_site = edit(here, member, display_name="Fine Name", bio="fine", profile_website="javascript:alert(1)")
    errors = wrong_site.get_json()["errors"]
    assert wrong_site.status_code == 400 and list(errors) == ["profile_website"]      # the good bio is not in the list: it is just not saved
    assert people_state(member) == before


def test_every_refused_field_is_named_in_one_answer(make_member):
    member = make_member()
    answer = edit(member.browser(), member, display_name="a@b", bio="y" * 300, profile_job="<b>", profile_location="z" * 61,
                  profile_website="javascript:x")
    body = answer.get_json()
    assert answer.status_code == 400 and not body["ok"]
    assert set(body["errors"]) == {"display_name", "bio", "profile_job", "profile_location", "profile_website"}
    assert body["error"] in body["errors"].values()


def test_what_did_not_change_is_not_a_change_and_is_not_checked_again(make_member):
    member = make_member()
    here = member.browser()
    fields = {"display_name": "Ada L", "bio": "same bio", "profile_job": "Engineer", "profile_location": "Berlin",
              "profile_website": "https://example.com"}
    assert edit(here, member, **fields).get_json()["changed"]
    again = edit(here, member, **fields)
    assert again.status_code == 200 and again.get_json()["ok"] is True and again.get_json()["changed"] is False
    assert again.get_json()["saved"] == []
    # a form sends a textarea's line breaks as CRLF: the same bio is still the same bio
    edit(here, member, bio="one\ntwo")
    crlf = edit(here, member, bio="one\r\ntwo")
    assert crlf.get_json()["changed"] is False and member.user().bio == "one\ntwo"


def test_a_value_stored_before_a_rule_changed_does_not_stop_the_next_save(make_member):
    member = make_member()
    # stored directly: what the rules of an earlier day let through and today's would refuse
    update(member, profile_website="javascript:alert(1)", display_name="a@b", bio="x" * 290)       # users.bio holds 300; the limit is now 256
    here = member.browser()
    answer = edit(here, member, display_name="a@b", profile_website="javascript:alert(1)", bio="x" * 290,
                  profile_job="Engineer")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and body["saved"] == ["profile_job"], body
    user = member.user()
    assert (user.profile_website, user.display_name, user.bio) == ("javascript:alert(1)", "a@b", "x" * 290)
    assert user.profile_job == "Engineer"


def test_a_field_not_sent_is_left_as_it_is(make_member):
    member = make_member()
    update(member, bio="kept", display_name="Kept")
    answer = edit(member.browser(), member, profile_job="Only this")
    assert answer.get_json()["saved"] == ["profile_job"]
    user = member.user()
    assert (user.bio, user.display_name) == ("kept", "Kept")


def test_a_new_account_with_a_link_in_its_bio_is_banned_and_the_answer_says_nothing_of_it(make_member):
    member = make_member()
    update(member, created_utc=int(time.time()) - 60)
    answer = edit(member.browser(), member, bio="[my page](https://spam-ci.example/)")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and body["changed"] and "ban" not in str(body).lower()
    user = member.user()
    assert user.is_banned and "spam-ci.example" in user.bio_html


# --- the banned-domain list ------------------------------------------------------------------------------------------------

def test_the_worst_kind_of_linked_site_earns_the_thirty_day_ban_and_the_refusal(make_member, banned_domains):
    member = make_member()
    answer = edit(member.browser(), member, bio="see [here](https://worse-ci.example/x)")
    body = answer.get_json()
    assert answer.status_code == 400 and "worse-ci.example" in body["errors"]["bio"]
    user = member.user()
    assert user.is_banned and user.unban_utc > time.time() + 29 * 86400 and user.bio in (None, "")


def test_another_banned_site_is_refused_without_a_ban(make_member, banned_domains):
    member = make_member()
    answer = edit(member.browser(), member, bio="see [here](https://short-ci.example/x)")
    assert answer.status_code == 400 and "short-ci.example" in answer.get_json()["errors"]["bio"]
    assert not member.user().is_banned


def test_a_banned_site_as_the_website_is_refused(make_member, banned_domains):
    member = make_member()
    answer = edit(member.browser(), member, profile_website="https://short-ci.example/")
    assert answer.status_code == 400 and "short-ci.example" in answer.get_json()["errors"]["profile_website"]
    assert member.user().profile_website in (None, "")


# --- who may send a picture -------------------------------------------------------------------------------------------------------

def test_a_new_account_with_no_reputation_can_send_both_pictures(make_member, storage):
    # no Rep is asked for a profile picture or a banner (the owner, 2026-10-30)
    member = make_member()
    answer = edit(member.browser(), member, files={"profile": (png(), "me.png"), "banner": (png((60, 10)), "bg.png")})
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and set(body["saved"]) == {"profile", "banner"}, body
    assert member.user().has_profile and member.user().has_banner


def test_through_tor_without_a_verified_email_no_picture_can_be_sent(make_member, storage):
    member = make_member()
    before = people_state(member)
    tor = {"cf-ipcountry": "T1"}
    sentence = "You must have a verified email address to upload images via Tor."
    answer = edit(member.browser(), member, files={"profile": (png(), "me.png")}, headers=tor, bio="not saved either")
    assert answer.status_code == 403 and answer.get_json()["errors"]["profile"] == sentence
    answer = edit(member.browser(), member, files={"banner": (png(), "bg.png")}, headers=tor)
    assert answer.status_code == 403 and answer.get_json()["errors"]["banner"] == sentence
    assert people_state(member) == before and storage.calls == []


def test_a_suspended_account_may_change_its_text_and_remove_a_picture_but_not_send_one(make_member, storage):
    member = uploader(make_member)
    update(member, is_banned=1, unban_utc=0, has_profile=True, profile_nonce=3, profile_set_utc=int(time.time()))
    here = member.browser()
    refused = edit(here, member, files={"profile": (png(), "me.png")})
    assert refused.status_code == 403 and refused.get_json()["errors"]["profile"] == "Your account is suspended."
    assert storage.calls == [] and member.user().profile_nonce == 3

    text = edit(here, member, profile_job="Still writing")
    assert text.status_code == 200 and text.get_json()["saved"] == ["profile_job"]

    removed = edit(here, member, remove_profile="1")
    assert removed.status_code == 200 and removed.get_json()["saved"] == ["profile"]
    assert not member.user().has_profile and len(storage.keys("delete")) == 1


# --- pictures ---------------------------------------------------------------------------------------------------------------------------

def test_a_picture_is_stored_under_its_new_key_before_the_old_one_is_taken_away(make_member, storage):
    import gevent
    member = uploader(make_member)
    here = member.browser()
    first = edit(here, member, files={"profile": (png(), "me.png"), "banner": (png((60, 10)), "bg.png")}, bio="with pictures")
    body = first.get_json()
    assert first.status_code == 200 and body["ok"] and set(body["saved"]) == {"bio", "profile", "banner"}, body
    user = member.user()
    assert user.has_profile and user.has_banner and user.profile_nonce == 1 and user.banner_nonce == 1
    key = f"uid/{user.base36id}/"
    assert storage.calls == [("upload", key + "profile-1.png"), ("upload", key + "banner-1.png")]   # nothing to take away yet
    assert body["profile_url"].endswith(key + "profile-1.png") and body["banner_url"].endswith(key + "banner-1.png")
    assert body["has_profile"] and body["has_banner"]
    gevent.sleep(0.1)
    assert storage.scanned == [body["profile_url"], body["banner_url"]]      # each new picture, by its new address, goes to the CSAM check

    storage.calls.clear()
    pointed_at = []
    storage.on_delete = lambda name: pointed_at.append(member.user().profile_nonce)
    second = edit(here, member, files={"profile": (png(colour="blue"), "me2.png")})
    assert second.status_code == 200 and second.get_json()["saved"] == ["profile"]
    assert storage.calls == [("upload", key + "profile-2.png"), ("delete", key + "profile-1.png")]
    # the old file goes only once the commit is done: the account already pointed at the new one when the bucket was asked
    assert pointed_at == [2]
    user = member.user()
    assert user.profile_nonce == 2 and user.banner_nonce == 1 and user.has_profile


def test_a_picture_that_cannot_be_stored_leaves_the_old_one_and_the_text_is_saved_anyway(make_member, storage):
    member = uploader(make_member)
    here = member.browser()
    assert edit(here, member, files={"profile": (png(), "me.png")}).status_code == 200
    storage.calls.clear()
    storage.scanned.clear()
    storage.fail = True
    answer = edit(here, member, files={"profile": (png(colour="blue"), "me2.png")}, bio="the text goes through")
    body = answer.get_json()
    # a 409, not a 5xx: a proxy may replace an origin's 5xx body with its own page and the saved part would be lost
    assert answer.status_code == 409 and not body["ok"] and "profile" in body["errors"], body
    assert body["saved"] == ["bio"]                                   # the text; the picture is not in what was saved
    assert set(body["values"]) == {"display_name", "bio", "profile_job", "profile_location", "profile_website"}
    user = member.user()
    assert user.has_profile and user.profile_nonce == 1               # still pointing at the picture that is in the bucket
    assert storage.keys("delete") == []                               # and it was not taken away
    assert storage.scanned == []                                      # nothing new was stored, so nothing new is scanned
    assert user.bio == "the text goes through"


def test_a_file_that_is_not_a_picture_changes_nothing_and_never_reaches_the_bucket(make_member, storage):
    member = uploader(make_member)
    before = people_state(member)
    here = member.browser()
    text = edit(here, member, files={"profile": (b"this is only text", "me.png")}, bio="not saved")
    assert text.status_code == 400 and text.get_json()["errors"]["profile"] == "That file is not a picture."

    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (400, 400), "red").save(out, "JPEG")
    cut = edit(here, member, files={"banner": (out.getvalue()[: len(out.getvalue()) // 2], "bg.jpg")})
    assert cut.status_code == 400 and "banner" in cut.get_json()["errors"]
    assert people_state(member) == before and storage.calls == []


def test_removing_a_picture_takes_it_away_once_and_only_when_there_is_one(make_member, storage):
    member = uploader(make_member)
    update(member, has_profile=True, profile_nonce=5, profile_set_utc=int(time.time()))
    here = member.browser()
    first = edit(here, member, remove_profile="1")
    assert first.status_code == 200 and first.get_json()["saved"] == ["profile"]
    assert not member.user().has_profile
    assert storage.keys("delete") == [f"uid/{member.user().base36id}/profile-5.png"]

    storage.calls.clear()
    again = edit(here, member, remove_profile="1")
    assert again.status_code == 200 and again.get_json()["changed"] is False
    assert storage.calls == []                                        # nothing there: no bucket call, no purge


def test_a_banner_that_fails_after_a_good_avatar_is_reported_alone_and_the_old_banner_stays(make_member, storage):
    member = uploader(make_member)
    update(member, has_banner=True, banner_nonce=2, banner_set_utc=int(time.time()))
    storage.fail_on = "banner"
    answer = edit(member.browser(), member, files={"profile": (png(), "me.png"), "banner": (png((60, 10)), "bg.png")})
    body = answer.get_json()
    assert answer.status_code == 409 and not body["ok"], body
    assert list(body["errors"]) == ["banner"]                          # the avatar is not in the list of what went wrong
    assert body["saved"] == ["profile"] and body["has_profile"] and body["has_banner"]
    user = member.user()
    assert user.has_profile and user.profile_nonce == 1                # the avatar went through
    assert user.has_banner and user.banner_nonce == 2                  # the banner is the one it was
    assert storage.keys("delete") == []                                # nothing was taken away: the avatar had no old file, the banner failed
    assert storage.keys("upload") == [f"uid/{user.base36id}/profile-1.png"]


def test_removing_a_banner_takes_it_away_once(make_member, storage):
    member = uploader(make_member)
    update(member, has_banner=True, banner_nonce=3, banner_set_utc=int(time.time()))
    here = member.browser()
    answer = edit(here, member, remove_banner="1")
    body = answer.get_json()
    assert answer.status_code == 200 and body["saved"] == ["banner"] and body["has_banner"] is False, body
    user = member.user()
    assert not user.has_banner
    assert storage.keys("delete") == [f"uid/{user.base36id}/banner-3.png"] and storage.keys("upload") == []
    storage.calls.clear()
    assert edit(here, member, remove_banner="1").get_json()["changed"] is False and storage.calls == []


def test_the_old_file_is_found_where_it_was_stored_for_an_old_account_and_a_new_one(make_member, storage):
    """Accounts older than 1616443200 keep their pictures under users/<name>/, newer ones under uid/<id>/: the file a
    replaced picture leaves is taken away from where it IS (the key is worked out before the new picture changes the
    date), while the new one is always stored under uid/."""
    legacy = uploader(make_member)
    update(legacy, has_profile=True, profile_nonce=4, profile_set_utc=1600000000,
           has_banner=True, banner_nonce=2, banner_set_utc=1600000000)
    user = legacy.user()
    base36id, username = user.base36id, user.username              # read now: the request closes the session this row is in
    answer = edit(legacy.browser(), legacy, files={"profile": (png(), "me.png"), "banner": (png((60, 10)), "bg.png")})
    assert answer.status_code == 200 and set(answer.get_json()["saved"]) == {"profile", "banner"}
    new = f"uid/{base36id}/"
    assert storage.calls == [("upload", new + "profile-5.png"), ("upload", new + "banner-3.png"),
                             ("delete", f"users/{username}/profile-4.png"),
                             ("delete", f"users/{username}/banner-2.png")], storage.calls

    storage.calls.clear()
    modern = uploader(make_member)
    update(modern, has_profile=True, profile_nonce=4, profile_set_utc=int(time.time()))
    base36id = modern.user().base36id
    assert edit(modern.browser(), modern, files={"profile": (png(), "me.png")}).status_code == 200
    new = f"uid/{base36id}/"
    assert storage.calls == [("upload", new + "profile-5.png"), ("delete", new + "profile-4.png")], storage.calls


def test_a_photo_holding_more_than_one_picture_is_a_picture(make_member, storage):
    """Most phones' JPEGs are what Pillow calls MPO."""
    from PIL import Image
    first, second = Image.new("RGB", (32, 32), "red"), Image.new("RGB", (16, 16), "blue")
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second])
    data = out.getvalue()
    if Image.open(io.BytesIO(data)).format != "MPO":
        pytest.skip("this Pillow writes no multi-picture JPEG that reads back as MPO")
    member = uploader(make_member)
    answer = edit(member.browser(), member, files={"profile": (data, "phone.jpg")})
    assert answer.status_code == 200 and answer.get_json()["saved"] == ["profile"], answer.get_json()
    assert member.user().has_profile and len(storage.keys("upload")) == 1


def test_a_request_past_the_cap_is_turned_away_with_a_sentence_and_changes_nothing(make_member, storage):
    from ruqqus.helpers import profile_edit
    member = uploader(make_member)
    before = people_state(member)
    padding = b"0" * (profile_edit.REQUEST_MAX_BYTES + 1024)               # a file part: a text field has a much smaller limit of its own
    answer = edit(member.browser(), member, files={"pad": (padding, "pad.bin")}, bio="not saved")
    assert answer.status_code == 413, answer.status_code
    body = answer.get_json()
    assert not body["ok"] and body["errors"]["profile"] == "That file is too big. The limit for image files is 16 MB."
    assert people_state(member) == before and storage.calls == []


def test_two_overlapping_saves_of_one_account_never_take_the_same_picture_number(make_member, storage):
    """The advisory lock (helpers/profile_edit.lock_account): without it both read nonce 0 and store profile-1."""
    member = uploader(make_member)
    storage.upload_delay = 0.3                                       # long enough for the second request to arrive while the first is in the bucket
    results = []

    def save(colour):
        results.append(edit(member.browser(), member, files={"profile": (png(colour=colour), "me.png")}).status_code)

    jobs = [gevent.spawn(save, "red"), gevent.spawn(save, "blue")]
    gevent.joinall(jobs, timeout=30)
    assert sorted(results) == [200, 200], results
    user = member.user()
    keys = storage.keys("upload")
    assert len(keys) == 2 and len(set(keys)) == 2, keys                 # two different keys: never the same file twice
    assert user.profile_nonce == 2 and keys == [f"uid/{user.base36id}/profile-1.png", f"uid/{user.base36id}/profile-2.png"]
    assert storage.keys("delete") == [f"uid/{user.base36id}/profile-1.png"]        # the second save replaced the first's picture


# --- what an earlier day's rules let through is never held against a later save ------------------------------------------------

def test_a_bio_on_a_now_banned_site_is_not_refused_or_punished_when_it_is_sent_back_unchanged(make_member, banned_domains):
    member = make_member()
    stored = "see [here](https://worse-ci.example/x)"
    update(member, bio=stored, bio_html='<p>see <a href="https://worse-ci.example/x">here</a></p>')
    answer = edit(member.browser(), member, bio=stored, profile_job="Engineer")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and body["saved"] == ["profile_job"], body
    user = member.user()
    assert not user.is_banned                                           # the reason-4 ban is for a bio being WRITTEN, not for one they already had
    assert user.bio == stored and "worse-ci.example" in user.bio_html and user.profile_job == "Engineer"


def test_a_website_on_a_now_banned_site_is_kept_when_only_something_else_changes(make_member, banned_domains):
    member = make_member()
    update(member, profile_website="https://worse-ci.example/")
    answer = edit(member.browser(), member, profile_website="https://worse-ci.example/", bio="a fresh bio")
    body = answer.get_json()
    assert answer.status_code == 200 and body["ok"] and body["saved"] == ["bio"], body
    user = member.user()
    assert not user.is_banned and user.profile_website == "https://worse-ci.example/" and user.bio == "a fresh bio"
    # changing it TO that site is refused as ever
    refused = edit(member.browser(), member, profile_website="https://short-ci.example/")
    assert refused.status_code == 400 and "short-ci.example" in refused.get_json()["errors"]["profile_website"]


def test_a_bio_over_todays_limit_does_not_stop_the_save_of_something_else(make_member):
    """users.bio holds 300; the limit is 256 as members count (a CJK character counts 2), so older bios are over it."""
    member = make_member()
    for stored in ("x" * 300, "漢" * 200):
        update(member, bio=stored, bio_html=f"<p>{stored}</p>", profile_job="")
        answer = edit(member.browser(), member, bio=stored, profile_job="Engineer")
        body = answer.get_json()
        assert answer.status_code == 200 and body["saved"] == ["profile_job"], body
        user = member.user()
        assert user.bio == stored and user.profile_job == "Engineer"
        # but writing a different over-long bio is refused
        refused = edit(member.browser(), member, bio=stored + "y")
        assert refused.status_code == 400 and list(refused.get_json()["errors"]) == ["bio"]


# --- who may call it ----------------------------------------------------------------------------------------------------------------------

def test_no_form_key_and_no_sign_in_change_nothing(make_member, real_app):
    member = make_member()
    before = people_state(member)
    without = edit(member.browser(), member, formkey=False, bio="nope")
    assert without.status_code in (302, 401, 403) and people_state(member) == before
    visitor = real_app[0].test_client()
    answer = visitor.post("/settings/profile/edit", base_url=BASE, headers=HEADERS, data={"bio": "nope", "formkey": "x"},
                          environ_base={"REMOTE_ADDR": address()})
    assert answer.status_code in (302, 401) and people_state(member) == before


def test_twenty_saves_an_hour_per_account_and_then_a_429(make_member):
    member = make_member()
    here = member.browser()
    for _ in range(20):
        assert edit(here, member, display_name="").status_code == 200
    assert edit(here, member, display_name="").status_code == 429


# --- Settings > Profile still answers as it did, now through the same checks ------------------------------------------------------

def test_settings_saves_a_display_name_and_refuses_the_same_ones(make_member):
    member = make_member()
    here = member.browser()
    saved = settings(here, member, display_name_form="1", display_name="Ada L")
    assert saved.status_code == 200 and saved.get_json() == {"message": "Your display name has been updated."}
    assert member.user().display_name == "Ada L"
    same = settings(here, member, display_name_form="1", display_name="Ada L")
    assert same.status_code == 400 and same.get_json() == {"error": "You didn't change anything"}
    assert settings(here, member, display_name_form="1", display_name="a@b").status_code == 400
    other = make_member()
    stolen = settings(here, member, display_name_form="1", display_name=other.username.replace("_", " "))
    assert stolen.status_code == 400 and "another account's username" in stolen.get_json()["error"]
    assert settings(here, member, display_name_form="1", display_name="Site Admin").status_code == 400     # kept for the staff
    assert settings(here, member, display_name_form="1", display_name="").get_json() == {"message": "Your display name has been removed."}


def test_settings_saves_the_details_and_refuses_a_bad_website(make_member, banned_domains):
    member = make_member()
    here = member.browser()
    saved = settings(here, member, details="1", profile_job="Engineer", profile_location="Berlin", profile_website="example.com")
    assert saved.status_code == 200 and saved.get_json() == {"message": "Your profile details have been updated."}
    assert member.user().profile_website == "https://example.com"
    bad = settings(here, member, details="1", profile_job="x", profile_location="y", profile_website="javascript:alert(1)")
    assert bad.status_code == 400 and bad.get_json()["error"]
    listed = settings(here, member, details="1", profile_job="x", profile_location="y", profile_website="https://short-ci.example")
    assert listed.status_code == 400 and "short-ci.example" in listed.get_json()["error"]
    assert member.user().profile_job == "Engineer"


def test_settings_saves_a_bio_and_keeps_its_answers(make_member, banned_domains):
    member = make_member()
    here = member.browser()
    saved = settings(here, member, bio="hello **there**")
    assert saved.status_code == 200 and saved.get_json() == {"message": "Your bio has been updated."}
    assert "<strong>there</strong>" in member.user().bio_html
    same = settings(here, member, bio="hello **there**")
    assert same.get_json() == {"error": "You didn't change anything"}
    assert settings(here, member, bio="x" * 257).status_code == 400
    heavy = settings(here, member, bio="<https://a.io/b> " * 14)
    assert heavy.status_code == 400 and "formatting" in heavy.get_json()["error"]          # was a 500 at the commit
    listed = settings(here, member, bio="[a](https://short-ci.example/)")
    assert listed.status_code == 401 and "short-ci.example" in listed.get_json()["error"] and not member.user().is_banned
    page = settings(here, member, path="/settings/profile", bio="a page answer")
    assert page.status_code == 200 and "Your bio has been updated." in page.get_data(as_text=True)


def test_settings_still_bans_for_the_worst_kind_of_linked_site(make_member, banned_domains):
    member = make_member()
    answer = settings(member.browser(), member, bio="[a](https://worse-ci.example/)")
    assert answer.status_code == 401 and "worse-ci.example" in answer.get_json()["error"]
    user = member.user()
    assert user.is_banned and user.unban_utc > time.time() + 29 * 86400
