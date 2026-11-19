"""What an image check does to the account that uploaded the picture: it QUARANTINES it, and an admin decides what follows (the owner,
2026-11-17 and 18; helpers/auto_quarantine.py is the only place that sets or clears an automatic hold).

The checks (helpers/aws.py `check_csam_url`, helpers/media/safety.py `_scan_public`, routes/media.py) run in a greenlet with a session of
their own, after the request that uploaded the picture is over (the one in routes/media.py runs inside the request). They used to ban the
uploader and their alts, permanently or for a few days, on a hash match that can be wrong. They now call `quarantine_uploader`:
  * the uploader is put on hold with the trigger `image` (`auto_quarantine.hold`): the staff's entry names the account, the public line (written by the
    cycle an hour or more later, `auto_quarantine.publish_due`) says only "Automatic image check" (rule: criminal activity) and names no account, no picture and no guild, and the person is told, with the
    link to ask for a review;
  * what was matched (the CDN's 451, a picture on the site's own banned list and its reason) is a separate entry linked to the staff's,
    level 4 for the child-abuse list and staff for the site's own list (`action_log.record_detail`);
  * the uploader's alts are held too, in staff-only entries with no public line and no notice (the hold's own rule);
  * **no cascade to a parent's sub-accounts is needed any more**: nothing here bans, closes or suspends anybody, which is the point.
A second check on an account already held by an automatic check, an admin, a bot, a deleted or an already banned account is a no-op: no new hold, no
public line, nobody is told, no alt is touched, and the picture (and the post or comment that showed it) is taken down by the caller as before.
**A match of the CDN's child-abuse list is level 4's, whatever follows it** (`legal=True`): (1) the hold's staff entry, and the entries of the alts held beside it, are
written at the legal audience (a level 3 admin sees a masked row in the queue, is refused a release as it answers a hold that isn't there, and can't decide its appeal:
`quarantine_review.release_refusal`, `log_appeals`), so the staff cannot lift what they were never shown the reason for; (2) a match on an account that is ALREADY held by
an automatic check (a held account may still change its own profile, pictures included) is its own level-4 detail entry linked to the hold the account is on, and that hold,
its alts' entries included, becomes legal-level too (`action_log.floor_entry` only ever narrows); (3) a match on an account that is not held because it is exempt (an admin, a
bot, deleted, banned) or because the hold could not be written is still a level-4 entry that names the account, linked to nothing. All of these are level-4 only: no public
line, no notice, nothing the person reads, so the quiet of a second check holds for everyone but the admins who must know.

**A failure to put the account on hold never stops the picture coming down**: it is logged and the caller goes on to remove the file (a
removal that waited on a bookkeeping error would leave an illegal picture up). `tell` sends the notice after the caller's own commit: a scan
runs in a greenlet, outside any request, so no `g.after_commit` is there to do it. `tests/test_log_coverage_assets.py` fails on any other
code that assigns `is_banned` on an account."""
import logging

from sqlalchemy import text

from ruqqus.helpers import action_log, auto_quarantine

_log = logging.getLogger(__name__)

NOT_HELD = "the account was not put on hold: it is an admin, a bot, deleted or already banned"
NOT_WRITTEN = "the hold's own entry could not be written"
NOT_PLACED = "the hold could not be placed"


def quarantine_uploader(db, user, *, detail="", legal=False):
    """Put `user` (a `User` loaded in `db`) and their alts on hold as an image check's match, writing the Log's entries, and return the
    STAFF entry's id, or None when nothing changed (no account, an admin, a bot, a deleted or a banned one, one already held by an
    automatic check) or when the hold could not be written. (None can still come with a level-4 entry written: see the module's docstring.) The caller
    commits `db`, then calls `tell`. `legal` says the match was the child-abuse list's (its detail and the hold's entry are for level-4 admins) rather than the
    site's own banned pictures (staff)."""
    if user is None:
        return None
    entry, why = None, NOT_HELD
    try:
        with db.begin_nested():
            alts = list(user.alts_threaded(db))
            entry = auto_quarantine.hold(db, user, "image", alts=alts)
            if entry is not None and not entry:
                why = NOT_WRITTEN
            elif entry and not legal:
                action_log.record_detail(db, entry, detail, target_user_id=user.id, legal=False)
    except Exception:
        _log.exception("an image check could not put its uploader on hold")
        entry, why = None, NOT_PLACED
    if legal:
        _keep_legal(db, user, entry, detail, why)
    return entry


def _keep_legal(db, user, entry, detail, why):
    """The child-abuse list's match, kept at level 4 whatever became of the hold (the module's docstring). `entry` is `hold`'s answer (the staff entry's id; None when
    nothing changed; 0 when the entry could not be written) and `why` says which. Its own savepoint and `try`: a failure here is logged and never undoes the hold, and
    the picture comes down either way."""
    try:
        with db.begin_nested():
            held_by = entry or None
            if held_by is None and why == NOT_HELD:
                current = auto_quarantine.current_entry(db, user.id)           # already held by an automatic check: the match goes beside that hold
                held_by = current["id"] if current is not None else None
            if held_by:
                _make_legal(db, held_by)
                action_log.record_detail(db, held_by, detail, target_user_id=user.id, legal=True)
            else:
                action_log.record_quietly(db, "suppress_content", action_log.SITE_ACCOUNT_ID, role=action_log.SYSTEM, target_user_id=user.id,
                                          staff_note=f"{detail} ({why})", floor=action_log.LEGAL)
    except Exception:
        _log.exception("an image check could not keep the child-abuse match's own record")


def _make_legal(db, entry_id):
    """Narrow the hold's staff entry, and the staff entries of the alts held beside it, to level 4 (never wider: `action_log.floor_entry`). The public line is not touched."""
    ids = [int(entry_id)] + [row[0] for row in db.execute(text(
        "SELECT id FROM log_entries WHERE linked_entry_id = :e AND action = 'quarantine_user'"), {"e": int(entry_id)})]
    for entry in ids:
        action_log.floor_entry(db, entry, action_log.LEGAL)


def tell(db):
    """Send the notices `quarantine_uploader` queued on `db`, once the caller has committed it. Best effort; returns how many went. Inside a
    request this is not needed (the notice rides `g.after_commit`) and does no harm: a notice goes once."""
    try:
        return auto_quarantine.send_pending(db)
    except Exception:
        _log.exception("an image check's notice was not sent")
        return 0
