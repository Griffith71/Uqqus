"""What an image check does to its uploader, on the real app and Postgres (helpers/scan_bans.py -> helpers/auto_quarantine.py; the rules and the SQL are tested on SQLite
in tests/test_scan_bans.py, what each caller hands over in tests/test_csam_ban.py and tests/test_media_safety.py). The owner, 2026-11-17 and 18: an image check only
QUARANTINES; an admin decides what follows. So, end to end:

- a match (the CDN's 451, a picture on the site's own banned list) puts the uploader and their alts on hold and bans, suspends and closes nobody; the staff's entry names
  the account, the public line says only "Automatic image check" and names no account, no picture and no guild, what was matched is a separate linked entry (level 4 for the
  CDN's list, staff for the site's own), the alts are staff-only with no notice, and only the uploader is told, with the review link, once the scan has committed;
- the removal of a post or comment the picture was in is a PUBLIC entry in the same generic words, an anonymous item's too, and nothing pairs the account with the item;
- a second check, an admin, a bot and an already banned account write nothing and tell nobody, EXCEPT that a match of the CDN's child-abuse list is always a level-4 record naming
  the account (beside the hold the account is on, or linked to nothing for an exempt one), and makes its hold and its alts' entries level 4's: a level 3 admin is shown nothing of
  them in the queue (no row, no name, not in the count), is refused a release and can't decide the appeal; a level 4 admin sees it all and can lift it;
- the checks run for real: `check_csam_url` (the CDN's 451 holds whoever uploaded, even a limited QR sign-in; the site's own list holds nobody on a limited sign-in's word, and
  the picture goes either way), `_scan_public` with real media rows, and the upload route's own check, whose notice follows the request's commit."""
import io
import secrets
import time
from types import SimpleNamespace

import gevent
import pytest
from sqlalchemy import text

from pg.test_log_trail_routes import add_comment, add_post, admin_of, entries, get, guild, notices_of, post, rows

HELD = "put your account on hold"
APPEAL = "/appeal/quarantine"
SITE_LIST = "matched a picture on the site's banned list: Spam picture"
CDN = "the CDN answered 451 for an uploaded picture"


@pytest.fixture(autouse=True)
def no_stale_notices(real_app):
    """A worker (and so a test's thread) may reuse one session, and its waiting notices live on it: what an earlier test queued is not this one's to send."""
    real_app[1]().info.pop("auto_quarantine_notices", None)
    yield
    real_app[1]().info.pop("auto_quarantine_notices", None)


def row_of(html, member):
    """The queue's card for this account: from its anchor to the next card (other tests leave other held accounts in the page)."""
    start = html.index(f'id="quarantine-{member.id}"')
    end = html.find('class="card mb-2 quarantine-row"', start)
    return html[start:end if end != -1 else len(html)]


def link_alt(member, alt):
    db = member.db_session()
    db.rollback()
    db.execute(text("INSERT INTO alts (user1, user2) VALUES (:a, :b)"), {"a": member.id, "b": alt.id})
    db.commit()


def state(real_app, member):
    return rows(real_app, "SELECT quarantined_utc, quarantine_kind, is_banned, unban_utc FROM users WHERE id = :u", u=member.id)[0]


def held(real_app, member):
    now = state(real_app, member)
    return now["quarantined_utc"] > 0 and now["quarantine_kind"] == 1 and not now["is_banned"] and not now["unban_utc"]


def clean(real_app, member):
    now = state(real_app, member)
    return now["quarantined_utc"] == 0 and now["quarantine_kind"] == 0


def told(real_app, member):
    return [body for body in notices_of(real_app, member.id) if HELD in body]


def wait_told(real_app, member):
    for _ in range(50):
        if told(real_app, member):
            return True
        gevent.sleep(0.1)
    return False


def scanned(real_app, member, **kwargs):
    """What a scan's greenlet does: its own session, the hold, the commit, then the notice. Returns the staff entry's id (None: nothing changed) and what `tell` sent."""
    from ruqqus.classes import User
    from ruqqus.helpers import scan_bans
    scan = real_app[1]()
    scan.rollback()
    scan.expire_all()
    entry = scan_bans.quarantine_uploader(scan, scan.get(User, member.id), **kwargs)
    scan.commit()
    return entry, scan_bans.tell(scan)


def publish_lines(real_app):
    """The cycle's step that writes the public lines, run as if just past its delay."""
    from ruqqus.helpers import auto_quarantine
    db = real_app[1]()
    db.rollback()
    written = auto_quarantine.publish_due(db, now=int(time.time()) + auto_quarantine.PUBLISH_DELAY + 1)
    db.commit()
    return written


# --- a match holds the uploader and the alts, says so generically in public, and tells only the uploader ----------------------------------------

def test_a_cdn_451_holds_the_uploader_and_their_alt_names_nobody_in_public_and_tells_only_the_uploader(make_member, real_app):
    from ruqqus.helpers import action_log
    uploader, alt, bystander = make_member(), make_member(), make_member()
    link_alt(uploader, alt)
    public_before = len(entries(real_app, action="automatic_quarantine"))

    entry, sent = scanned(real_app, uploader, detail=CDN, legal=True)
    assert entry and sent == 1

    assert held(real_app, uploader) and held(real_app, alt) and clean(real_app, bystander)           # held, never banned, suspended or closed
    assert not entries(real_app, action="ban_user", target_user_id=uploader.id) and not entries(real_app, action="suspend_user", target_user_id=uploader.id)

    (staff,) = entries(real_app, id=entry)
    assert (staff["action"], staff["actor_role"], staff["target_user_id"], staff["audience"], staff["staff_note"]) == (
        "quarantine_user", "system", uploader.id, action_log.LEGAL, "image")          # the CDN's match: level 4's, as its detail is
    (detail,) = entries(real_app, linked_entry_id=entry, action="suppress_content")
    assert (detail["audience"], detail["staff_note"], detail["target_user_id"]) == (action_log.LEGAL, CDN, uploader.id)
    assert len(entries(real_app, action="automatic_quarantine")) == public_before               # the public line is written by the cycle, later
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    publish_lines(real_app)
    public = entries(real_app, action="automatic_quarantine", linked_entry_id=entry)
    assert len(public) == 1
    assert (public[-1]["audience"], public[-1]["rule_key"], public[-1]["reason"], public[-1]["actor_role"], public[-1]["linked_entry_id"]) == (
        action_log.PUBLIC, "illegal", "Automatic image check", "system", entry)
    assert (public[-1]["target_user_id"], public[-1]["target_submission_id"], public[-1]["target_comment_id"], public[-1]["board_id"]) == (None,) * 4
    alt_entry = entries(real_app, action="quarantine_user", target_user_id=alt.id)[-1]                 # the alt's current hold: the form names the hold it was drawn for
    assert alt_entry["audience"] == action_log.LEGAL and f"alt of @{uploader.username}" in alt_entry["staff_note"] and alt_entry["linked_entry_id"] == entry

    visitor = real_app[0].test_client()
    line = get(visitor, f"/log/{public[-1]['id']}").get_data(as_text=True)                  # by id: a line dated to the hour sorts below the hour's other entries
    assert "Automatic image check" in line
    html = line + get(visitor, "/log?type=sanction").get_data(as_text=True)
    assert "answered 451" not in html and "the CDN" not in html
    assert uploader.username not in html and alt.username not in html
    profile = get(visitor, f"/@{uploader.username}").get_data(as_text=True)
    assert "Sexualizing" not in profile and "Automatic image check" not in profile and 'id="held-notice"' not in profile
    assert 'id="held-notice"' in get(uploader.browser(), "/").get_data(as_text=True)                  # only the account itself is told on its own pages

    (notice,) = told(real_app, uploader)
    assert APPEAL in notice and "Automatic image check" in notice and "451" not in notice and "CDN" not in notice
    assert not told(real_app, alt) and not told(real_app, bystander)


@pytest.mark.parametrize("anonymous", [False, True])
def test_the_site_lists_match_holds_and_the_removal_of_the_post_is_public_an_anonymous_ones_too(make_member, real_app, anonymous):
    from ruqqus.classes import User
    from ruqqus.helpers import action_log, scan_bans
    uploader = make_member()
    board_id, name = guild(real_app)
    post_id, _ = add_post(uploader, board_id, anonymous=anonymous)
    scan = real_app[1]()
    scan.rollback()
    scan.expire_all()
    entry = scan_bans.quarantine_uploader(scan, scan.get(User, uploader.id), detail=SITE_LIST)
    removal = action_log.record_image_check_removal(scan, post_id=post_id, board_id=board_id, detail=SITE_LIST)
    scan.commit()
    assert entry and removal and scan_bans.tell(scan) == 1
    assert held(real_app, uploader)

    (removed,) = entries(real_app, id=removal)
    assert (removed["action"], removed["audience"], removed["reason"], removed["actor_role"], removed["rule_key"]) == (
        "ban_post", action_log.PUBLIC, "Automatic image check", "system", "illegal")          # public again, an anonymous post's too (decision 4)
    assert removed["target_user_id"] is None and removed["target_submission_id"] == post_id
    assert not rows(real_app, "SELECT 1 FROM log_entries WHERE target_user_id = :u AND (target_submission_id IS NOT NULL OR target_comment_id IS NOT NULL)", u=uploader.id)
    (quarantine_detail,) = entries(real_app, linked_entry_id=entry, action="admin_image_ban")
    assert entries(real_app, id=entry)[0]["audience"] == action_log.STAFF                  # the site's own list: a staff hold, not level 4's
    (removal_detail,) = entries(real_app, linked_entry_id=removal)
    assert quarantine_detail["audience"] == action_log.STAFF and quarantine_detail["staff_note"] == SITE_LIST      # the site's own list: staff, not level 4
    assert removal_detail["audience"] == action_log.STAFF and removal_detail["target_user_id"] is None
    html = get(real_app[0].test_client(), f"/log?guild={name}").get_data(as_text=True)
    assert "Automatic image check" in html and "Spam picture" not in html and uploader.username not in html


# --- quiet and idempotent ---------------------------------------------------------------------------------------------------------------------

def test_a_second_check_of_the_site_list_writes_nothing_and_tells_nobody(make_member, real_app):
    uploader, alt = make_member(), make_member()
    first, sent = scanned(real_app, uploader, detail=SITE_LIST)
    assert first and sent == 1
    link_alt(uploader, alt)
    about = len(entries(real_app, target_user_id=uploader.id))
    public = len(entries(real_app, action="automatic_quarantine"))
    linked = len(entries(real_app, linked_entry_id=first))

    again, sent = scanned(real_app, uploader, detail=SITE_LIST)

    assert again is None and sent == 0
    assert len(entries(real_app, target_user_id=uploader.id)) == about and len(entries(real_app, action="automatic_quarantine")) == public
    assert len(entries(real_app, linked_entry_id=first)) == linked and len(told(real_app, uploader)) == 1
    assert clean(real_app, alt)                                      # a check that changed nothing sweeps up nobody new


def test_a_second_cdn_match_holds_nothing_new_but_keeps_one_level_4_record_of_its_picture(make_member, real_app):
    """A held account may still change its own profile, pictures included: the child-abuse list's second match is a record of its own, linked to the hold."""
    from ruqqus.helpers import action_log
    uploader, alt = make_member(), make_member()
    first, sent = scanned(real_app, uploader, detail=CDN, legal=True)
    assert first and sent == 1
    link_alt(uploader, alt)
    about = len(entries(real_app, action="quarantine_user", target_user_id=uploader.id))
    public = len(entries(real_app, action="automatic_quarantine"))

    again, sent = scanned(real_app, uploader, detail=CDN + " again", legal=True)

    assert again is None and sent == 0                               # no new hold and no notice
    assert len(entries(real_app, action="quarantine_user", target_user_id=uploader.id)) == about
    assert len(entries(real_app, action="automatic_quarantine")) == public and len(told(real_app, uploader)) == 1
    assert entries(real_app, id=first)[0]["audience"] == action_log.LEGAL
    details = entries(real_app, linked_entry_id=first, action="suppress_content")
    assert [(d["audience"], d["staff_note"], d["target_user_id"]) for d in details] == [
        (action_log.LEGAL, CDN, uploader.id), (action_log.LEGAL, CDN + " again", uploader.id)]
    assert clean(real_app, alt)                                      # and it sweeps up nobody new


def test_a_cdn_match_on_an_account_the_spam_guard_already_holds_is_recorded_beside_that_hold_and_makes_it_level_4s(make_member, real_app):
    """The queue row must stop reading 'spam' to a level 3 admin, who could otherwise lift it without learning of the match."""
    from ruqqus.classes import User
    from ruqqus.helpers import action_log, auto_quarantine
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    db = real_app[1]()
    db.rollback()
    held_entry = auto_quarantine.hold(db, db.get(User, uploader.id), "spam", detail="six similar posts in a minute", alts=[db.get(User, alt.id)], notify=False)
    db.commit()
    assert held_entry and entries(real_app, id=held_entry)[0]["audience"] == action_log.STAFF
    public = len(entries(real_app, action="automatic_quarantine"))

    again, sent = scanned(real_app, uploader, detail=CDN, legal=True)

    assert again is None and sent == 0
    assert entries(real_app, id=held_entry)[0]["audience"] == action_log.LEGAL
    assert {e["audience"] for e in entries(real_app, action="quarantine_user", target_user_id=alt.id)} == {action_log.LEGAL}
    (detail,) = entries(real_app, linked_entry_id=held_entry, action="suppress_content")
    assert (detail["audience"], detail["staff_note"], detail["target_user_id"]) == (action_log.LEGAL, CDN, uploader.id)
    assert len(entries(real_app, action="automatic_quarantine")) == public and not told(real_app, uploader)


def test_a_451_hold_is_a_level_4_admins_in_the_queue_the_release_and_the_appeal(make_member, real_app):
    """The review's finding: a level 3 admin saw 'Image check' in the queue, could press Release and could decide the appeal, without ever seeing the match."""
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    level3, level4 = admin_of(make_member, 3), admin_of(make_member, 4)
    entry, sent = scanned(real_app, uploader, detail=CDN, legal=True)
    assert entry and sent == 1
    filed = post(uploader.browser(), uploader, "/appeal/quarantine", reason="That was an ordinary picture.")
    assert filed.status_code == 303
    (appeal,) = rows(real_app, "SELECT id FROM log_appeals WHERE user_id = :u", u=uploader.id)

    hidden = get(level3.browser(), "/admin/quarantine").get_data(as_text=True)
    for member in (uploader, alt):
        assert f'id="quarantine-{member.id}"' not in hidden and member.username not in hidden             # not in level 3's queue at all, no masked row
        assert f'action="/admin/quarantine/{member.id}/release"' not in hidden
    for member in (uploader, alt):
        assert post(level3.browser(), level3, f"/admin/quarantine/{member.id}/release", note="Trying.").status_code == 404
        assert held(real_app, member)
    assert f'action="/admin/appeals/{appeal["id"]}"' not in get(level3.browser(), "/admin/appeals").get_data(as_text=True)
    assert post(level3.browser(), level3, f"/admin/appeals/{appeal['id']}", outcome="reversed", note="Trying.").status_code == 404
    assert held(real_app, uploader)

    full = get(level4.browser(), "/admin/quarantine").get_data(as_text=True)
    assert "Image check" in row_of(full, uploader) and f'action="/admin/quarantine/{uploader.id}/release"' in full
    alt_entry = entries(real_app, action="quarantine_user", target_user_id=alt.id)[-1]                 # the alt's current hold: the form names the hold it was drawn for
    lifted = post(level4.browser(), level4, f"/admin/quarantine/{alt.id}/release", entry=alt_entry["id"], note="An alt of a hold I have read.")
    assert lifted.status_code == 303 and clean(real_app, alt) and held(real_app, uploader)
    (lift,) = entries(real_app, action="unquarantine_user", target_user_id=alt.id)
    assert lift["audience"] == 3                                      # as private as the hold it lifts


@pytest.mark.parametrize("change", ["admin_level = 3", "is_bot = true", "is_banned = 1"])
def test_an_admin_a_bot_and_a_banned_account_are_never_held_by_a_check_and_the_site_lists_match_writes_nothing(make_member, real_app, change):
    uploader = make_member()
    db = uploader.db_session()
    db.rollback()
    db.execute(text(f"UPDATE users SET {change} WHERE id = :u"), {"u": uploader.id})
    db.commit()
    entry, sent = scanned(real_app, uploader, detail=SITE_LIST)
    assert entry is None and sent == 0
    assert state(real_app, uploader)["quarantined_utc"] == 0 and not entries(real_app, target_user_id=uploader.id) and not told(real_app, uploader)


@pytest.mark.parametrize("change", ["admin_level = 3", "is_bot = true", "is_banned = 1"])
def test_a_cdn_match_on_an_admin_a_bot_or_a_banned_account_holds_nobody_but_is_one_level_4_record_naming_it(make_member, real_app, change):
    from ruqqus.helpers import action_log
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    db = uploader.db_session()
    db.rollback()
    db.execute(text(f"UPDATE users SET {change} WHERE id = :u"), {"u": uploader.id})
    db.commit()

    entry, sent = scanned(real_app, uploader, detail=CDN, legal=True)

    assert entry is None and sent == 0
    assert state(real_app, uploader)["quarantined_utc"] == 0 and clean(real_app, alt) and not told(real_app, uploader)
    (record,) = entries(real_app, target_user_id=uploader.id)
    assert (record["action"], record["audience"], record["actor_role"], record["linked_entry_id"]) == ("suppress_content", action_log.LEGAL, "system", None)
    assert record["staff_note"].startswith(CDN)
    assert not entries(real_app, action="automatic_quarantine", linked_entry_id=record["id"])


# --- the checks themselves, end to end --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("limited", [False, True])
@pytest.mark.parametrize("match", ["cdn", "list"])
def test_check_csam_url_holds_on_the_cdns_word_whoever_uploaded_and_on_the_lists_word_unless_the_sign_in_was_limited(make_member, real_app, monkeypatch, tmp_path, match, limited):
    from ruqqus.helpers import action_log, aws
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(aws, "BUCKET", "bucket.example")
    monkeypatch.setattr(aws.requests, "get", lambda *a, **k: SimpleNamespace(status_code=451 if match == "cdn" else 200, iter_content=lambda n: [b"x"]))
    monkeypatch.setattr(aws, "check_phash", lambda db, name: SimpleNamespace(ban_time=2, ban_reason="Spam picture"))
    monkeypatch.setattr(aws, "delete_file", lambda name: None)
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    user = uploader.user()
    user.qr_limited = limited
    removed = []

    aws.check_csam_url(f"https://bucket.example/{secrets.token_hex(4)}.png", user, lambda: removed.append(1))

    assert removed == [1]                                             # the picture goes either way
    holds = match == "cdn" or not limited
    assert (held(real_app, uploader) and held(real_app, alt)) is holds and (clean(real_app, uploader) and clean(real_app, alt)) is not holds
    assert not state(real_app, uploader)["is_banned"] and not state(real_app, uploader)["unban_utc"]       # nothing is banned or suspended, ever
    assert bool(told(real_app, uploader)) is holds                    # the notice went inline, after the scan's own commit
    if holds:
        (entry,) = entries(real_app, action="quarantine_user", target_user_id=uploader.id)
        if match == "cdn":
            (detail,) = entries(real_app, linked_entry_id=entry["id"], action="suppress_content")
            assert detail["audience"] == action_log.LEGAL and "451" in detail["staff_note"]
            assert entry["audience"] == action_log.LEGAL                                  # the child-abuse list's match makes the hold level 4's
            assert {e["audience"] for e in entries(real_app, action="quarantine_user", target_user_id=alt.id)} == {action_log.LEGAL}
        else:
            (detail,) = entries(real_app, linked_entry_id=entry["id"], action="admin_image_ban")
            assert detail["audience"] == action_log.STAFF and "Spam picture" in detail["staff_note"]
            assert entry["audience"] == action_log.STAFF
        assert "Spam picture" not in told(real_app, uploader)[0] and "451" not in told(real_app, uploader)[0]
    else:
        assert not entries(real_app, target_user_id=uploader.id) and not entries(real_app, target_user_id=alt.id)


@pytest.mark.parametrize("on", ["post", "comment"])
def test_a_cdn_451_on_a_served_picture_removes_it_and_what_shows_it_and_holds_the_uploader(make_member, real_app, monkeypatch, on):
    import requests

    from ruqqus.classes import MediaAccount, MediaAsset
    from ruqqus.helpers import action_log
    from ruqqus.helpers.media import cdn, rules, safety
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    board_id, name = guild(real_app)
    post_id, _ = add_post(uploader, board_id)
    comment_id = add_comment(uploader, post_id, board_id)[0] if on == "comment" else None
    now = int(time.time())
    db = uploader.db_session()
    db.rollback()
    account = MediaAccount(user_id=uploader.id, provider="gdrive", status="active", created_utc=now, updated_utc=now)
    db.add(account)
    db.flush()
    asset = MediaAsset(user_id=uploader.id, account_id=account.id, provider="gdrive", kind="image", token=secrets.token_hex(8), ext="png", status="ready",
                       submission_id=post_id if on == "post" else None, comment_id=comment_id, created_utc=now, updated_utc=now)
    db.add(asset)
    db.commit()
    asset_id, path = asset.id, asset.path
    purged = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(status_code=451))
    monkeypatch.setattr(cdn, "purge", purged.append)

    safety._scan_public("https://cdn.example/picture.png", asset_id)

    assert purged == [[path]]
    assert rows(real_app, "SELECT status FROM media_assets WHERE id = :i", i=asset_id)[0]["status"] == rules.REMOVED
    if on == "post":
        assert rows(real_app, "SELECT is_banned FROM submissions WHERE id = :i", i=post_id)[0]["is_banned"]
        (removed,) = entries(real_app, action="ban_post", target_submission_id=post_id)
    else:
        assert rows(real_app, "SELECT is_banned FROM comments WHERE id = :i", i=comment_id)[0]["is_banned"]
        (removed,) = entries(real_app, action="ban_comment", target_comment_id=comment_id)
    assert (removed["audience"], removed["reason"], removed["actor_role"], removed["target_user_id"]) == (action_log.PUBLIC, "Automatic image check", "system", None)
    assert held(real_app, uploader) and held(real_app, alt)
    (entry,) = entries(real_app, action="quarantine_user", target_user_id=uploader.id)
    (detail,) = entries(real_app, linked_entry_id=entry["id"], action="suppress_content")
    assert detail["audience"] == action_log.LEGAL and "451" in detail["staff_note"] and entry["audience"] == action_log.LEGAL
    assert len(told(real_app, uploader)) == 1                         # sent by the scan itself: nothing else runs after a greenlet's commit
    assert not rows(real_app, "SELECT 1 FROM log_entries WHERE target_user_id = :u AND (target_submission_id IS NOT NULL OR target_comment_id IS NOT NULL)", u=uploader.id)


# --- the upload route's own check: inside the request, the notice follows its commit --------------------------------------------------------------

@pytest.mark.parametrize("limited", [False, True])
def test_a_banned_picture_at_upload_holds_the_uploader_inside_the_request_unless_the_sign_in_was_limited(make_member, real_app, monkeypatch, tmp_path, limited):
    from PIL import Image

    from ruqqus.helpers import action_log, qr_login
    from ruqqus.helpers.media import safety
    monkeypatch.setenv("MEDIA_DEV_PROVIDER", "1")
    monkeypatch.setenv("MEDIA_DEV_DIR", str(tmp_path))
    monkeypatch.setattr(safety, "banned_match", lambda db, path: SimpleNamespace(ban_reason="Spam picture", ban_time=0))
    uploader, alt = make_member(), make_member()
    link_alt(uploader, alt)
    raw = io.BytesIO()
    Image.new("RGB", (40, 30), (35, 169, 242)).save(raw, "PNG")
    data = raw.getvalue()
    here = uploader.browser()
    assert post(here, uploader, "/settings/media/dev/enable?json=1").status_code == 200
    if limited:
        with here.session_transaction() as session:
            qr_login.begin_limited(session, time.time())

    begun = post(here, uploader, "/api/media/uploads", kind="image", filename="a.png", size=str(len(data)))
    assert begun.status_code == 200, begun.get_data(as_text=True)[:300]
    assert here.put(begun.get_json()["upload"]["url"], base_url="http://localhost", data=data).status_code in (200, 201, 204)
    done = post(here, uploader, f"/api/media/uploads/{begun.get_json()['id']}/complete")

    assert done.status_code == 403 and "not allowed" in done.get_data(as_text=True)               # refused either way
    assert rows(real_app, "SELECT status FROM media_assets WHERE id = :i", i=int(begun.get_json()["id"], 36))[0]["status"] == "removed"
    if limited:
        gevent.sleep(0.5)
        assert clean(real_app, uploader) and clean(real_app, alt) and not told(real_app, uploader)
        assert not entries(real_app, target_user_id=uploader.id)                                 # a relayed code's word never holds the member
        return
    assert held(real_app, uploader) and held(real_app, alt) and not state(real_app, uploader)["is_banned"]
    assert wait_told(real_app, uploader), "the notice follows the request's commit (g.after_commit)"
    (entry,) = entries(real_app, action="quarantine_user", target_user_id=uploader.id)
    (detail,) = entries(real_app, linked_entry_id=entry["id"], action="admin_image_ban")
    assert detail["audience"] == action_log.STAFF and "Spam picture" in detail["staff_note"]
    assert len(told(real_app, uploader)) == 1 and not told(real_app, alt)
