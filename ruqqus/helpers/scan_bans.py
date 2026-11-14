"""The ban an image check makes, through `User.ban` like every other ban (the Log, slice D2: the audit's H5 and S1).

The checks (helpers/aws.py `check_csam_url`, helpers/media/safety.py `_scan_public`, routes/media.py) run in a greenlet
with a session of their own, after the request that uploaded the picture is over, and used to set `is_banned` on the uploader and their
alts by hand: the Log never heard of it, none of a permanent ban's other duties ran (the profile picture and banner, the guilds nobody
leads any more, the sub-accounts, the roles) and the reason stored and shown on the banned profile, "Sexualizing Minors", accused the
account of something a hash match can get wrong. They now call `ban_uploader`, which runs `User.ban` inside an application context of
its own whose `g.db` is that session:
  * the account's reason is the generic "Automatic image check", on the profile as in the Log (rule: criminal activity);
  * what was matched (the CDN's 451, a picture on the site's own banned list and its reason) is a separate entry linked to the public
    one, level 4 for the child-abuse list and staff for the site's own list (`action_log.record_detail`);
  * the uploader's alts are banned too, as before, in staff-only entries (a public one would say which accounts are linked);
  * **no cascade to a parent's sub-accounts** (`cascade=False`): a law-enforcement flow must not close an account the staff may need, as the
    CSAM route's hand ban never did (CLAUDE.md "Parent and child accounts").
`tests/test_log_coverage_assets.py` fails on any other code that assigns `is_banned` on an account."""
from ruqqus.helpers import action_log


def ban_uploader(db, user, *, days=0, detail="", legal=False):
    """Suspend (`days` > 0) or permanently ban `user` (a `User` loaded in `db`) and their alts as an image check's match, writing the
    Log's entries, and return the public entry's id. The caller commits `db`. `legal` says the match was the child-abuse list's
    (the detail is for level-4 admins) rather than the site's own banned pictures (staff)."""
    from flask import g

    from ruqqus.__main__ import app

    with app.app_context():
        g.db = db
        g.v = None
        alts = list(user.alts_threaded(db))
        entry = user.ban(reason=action_log.AUTOMATIC_IMAGE_REASON, days=days, rule="illegal", cascade=False)
        action_log.record_detail(db, entry, detail, target_user_id=user.id, legal=legal)
        for alt in alts:
            alt.ban(reason=action_log.AUTOMATIC_IMAGE_REASON, days=days, rule="illegal",
                    staff_note=f"alt of @{user.username}", floor=action_log.STAFF, cascade=False)
    return entry
