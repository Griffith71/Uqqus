"""The cleanup after the Log's slices E to H (2026-11-19), on the real app and Postgres: a held guildmaster is still a guildmaster for whoever acts on them, a lift told to an
account on hold says what is still so, a guild appeal nobody else could answer is the admins', and the admin guild powers need a reason."""
import time

from sqlalchemy import text

from pg.test_log_d3c import add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_d3f_aliases import anonymous_comment, ban_of, exile_alias, guildmaster
from pg.test_log_evidence import exile, named_post
from pg.test_log_lifted import REASON, mute, sayings
from pg.test_log_trail_routes import notices_of


def hold(real_app, member):
    db = member.db_session()
    db.execute(text("UPDATE users SET quarantined_utc = :n WHERE id = :u"), {"n": int(time.time()), "u": member.id})
    db.commit()


def test_a_held_guildmaster_can_still_not_be_exiled_or_muted_and_can_still_be_removed(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    founder, mod, held = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(founder, board_id)
    guildmaster(mod, board_id)
    guildmaster(held, board_id)
    _, thing_alias, _ = anonymous_comment(real_app, held, board_id)      # written before the hold: a held account can't write
    hold(real_app, held)
    _, _, thing = named_post(held, board_id)
    refused = exile(mod, board_name, username=held.username, reason="Rude", thing=thing)
    assert refused.status_code == 409 and "can't exile other guildmasters" in refused.get_json()["error"], "the guard is the target's standing, not their sign-in"
    assert ban_of(real_app, board_id, held) is None
    muted = mute(mod, board_name, held)
    assert "can't mute a guildmaster" in muted.get_data(as_text=True).replace("&#39;", "'") and rows(real_app, "SELECT 1 FROM chatbans WHERE user_id = :u", u=held.id) == []
    assert exile_alias(mod, board_name, thing_alias).status_code == 200
    assert ban_of(real_app, board_id, held) is None, "an alias exile of a held guildmaster acts on nobody, and answers as ever"
    # a senior guildmaster can still remove a held colleague (the route read their record through `has_mod`, which says no for a held account)
    removed = post(founder.browser(), founder, f"/mod/{base36encode(board_id)}/remove/{held.username}")
    assert removed.status_code == 204, removed.get_data(as_text=True)[:200]
    assert rows(real_app, "SELECT 1 FROM mods WHERE user_id = :u AND board_id = :b", u=held.id, b=board_id) == []


def test_a_lift_told_to_an_account_on_hold_says_what_is_still_so(real_app, make_member):
    mod, held, free = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    for who in (held, free):
        _, _, thing = named_post(who, board_id)
        assert exile(mod, board_name, username=who.username, reason="Rude", thing=thing).status_code in (200, 204)
    hold(real_app, held)
    for who in (held, free):
        assert post(mod.browser(), mod, f"/mod/unexile/{board_name}/{who.username}").status_code == 204
    (still,) = sayings(real_app, held, "no longer exiled")
    assert "still on hold" in still and "can't forward posts or comment" in still
    (plain,) = sayings(real_app, free, "no longer exiled")
    assert "on hold" not in plain and "can forward posts to it and comment in it again" in plain
    from ruqqus.helpers import log_notices
    assert log_notices.EXILE_LIFTED in log_notices.HELD_FROM_LIMITED and log_notices.MUTE_LIFTED in log_notices.HELD_FROM_LIMITED, "a relayed code is not shown them"


def sole_guildmaster_appeal(real_app, make_member):
    """An exile by the guild's only guildmaster, and the exiled account's appeal of it."""
    mod, member = make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    _, _, thing = named_post(member, board_id)
    assert exile(mod, board_name, username=member.username, reason="Rude to other members", thing=thing).status_code in (200, 204)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_user' AND board_id = :b", b=board_id)["id"]
    assert post(member.browser(), member, f"/log/{entry}/appeal", reason="That wasn't me being rude").status_code == 303
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry)["id"]
    return mod, member, board_id, board_name, entry, appeal


def test_a_guild_appeal_only_the_one_who_decided_could_answer_is_decided_by_the_admins(real_app, make_member):
    mod, member, board_id, board_name, entry, appeal = sole_guildmaster_appeal(real_app, make_member)
    assert "That wasn't me being rude" not in get(mod.browser(), f"/+{board_name}/mod/appeals").get_data(as_text=True), "not on the guild's queue: it can't be answered there"
    assert post(mod.browser(), mod, f"/+{board_name}/mod/appeals/{appeal}", outcome="upheld", note="Mine").status_code == 404, "and the one who decided doesn't decide their own"
    admin = admin_of(make_member)
    queue = get(admin.browser(), "/admin/appeals").get_data(as_text=True)
    assert "That wasn&#39;t me being rude" in queue or "That wasn't me being rude" in queue
    assert "no other guildmaster of that guild could decide this" in queue
    decided = post(admin.browser(), admin, f"/admin/appeals/{appeal}", outcome="reversed", note="Not enough to exile for")
    assert decided.status_code == 303 and "Decided" in decided.headers["Location"], decided.headers.get("Location")
    assert ban_of(real_app, board_id, member)["is_active"] is False
    undone = one(real_app, "SELECT actor_role, actor_id, linked_entry_id FROM log_entries WHERE action = 'unexile_user' AND board_id = :b", b=board_id)
    assert undone["linked_entry_id"] == entry and undone["actor_role"] == "admin", "an admin's reversal is the site's in the Log, never a guildmaster by name"
    visitor = get(real_app[0].test_client(), "/log?type=sanction").get_data(as_text=True)
    assert admin.username not in visitor
    assert len(sayings(real_app, member, "was accepted")) == 1


def test_a_guild_appeal_the_guild_left_for_two_weeks_is_the_admins_too_and_whoever_decides_first_settles_it(real_app, make_member):
    mod, second, member = (make_member() for _ in range(3))
    board_id, board_name = guild(real_app)
    guildmaster(mod, board_id)
    guildmaster(second, board_id)
    _, _, thing = named_post(member, board_id)
    assert exile(mod, board_name, username=member.username, reason="Rude to other members", thing=thing).status_code in (200, 204)
    entry = one(real_app, "SELECT id FROM log_entries WHERE action = 'exile_user' AND board_id = :b", b=board_id)["id"]
    assert post(member.browser(), member, f"/log/{entry}/appeal", reason="Please look again").status_code == 303
    appeal = one(real_app, "SELECT id FROM log_appeals WHERE entry_id = :e", e=entry)["id"]
    admin = admin_of(make_member)
    assert "Please look again" not in get(admin.browser(), "/admin/appeals").get_data(as_text=True), "while a colleague could answer it, it is the guild's"
    assert post(admin.browser(), admin, f"/admin/appeals/{appeal}", outcome="upheld", note="x").status_code == 404
    db = member.db_session()
    db.execute(text("UPDATE log_appeals SET created_utc = created_utc - :d WHERE id = :a"), {"d": 15 * 86400, "a": appeal})
    db.commit()
    assert "Please look again" in get(admin.browser(), "/admin/appeals").get_data(as_text=True)
    assert "Please look again" in get(second.browser(), f"/+{board_name}/mod/appeals").get_data(as_text=True), "and still the guild's own"
    assert post(admin.browser(), admin, f"/admin/appeals/{appeal}", outcome="upheld", note="The exile stands").status_code == 303
    again = post(second.browser(), second, f"/+{board_name}/mod/appeals/{appeal}", outcome="reversed", note="Too late")
    assert "already decided" in again.headers["Location"].replace("%20", " ")
    assert ban_of(real_app, board_id, member)["is_active"] is True


def test_the_admin_count_includes_what_was_escalated(real_app, make_member):
    mod, member, board_id, board_name, entry, appeal = sole_guildmaster_appeal(real_app, make_member)
    admin = admin_of(make_member)
    from ruqqus.helpers import log_appeals
    assert log_appeals.waiting_count(real_app[1](), None, level=3, viewer_id=admin.id) >= 1
    assert "waiting" in get(admin.browser(), "/admin").get_data(as_text=True)


def test_an_admin_taking_guildmaster_powers_or_taking_them_away_says_why_and_the_people_are_told(real_app, make_member):
    from ruqqus.helpers.base36 import base36encode
    owner, head, target = admin_of(make_member, 4), make_member(), make_member()
    board_id, board_name = guild(real_app)
    guildmaster(head, board_id)
    guildmaster(target, board_id)
    refused = post(owner.browser(), owner, f"/api/mod_self/{base36encode(board_id)}")
    assert refused.status_code == 400 and one(real_app, "SELECT COUNT(*) AS n FROM mods WHERE user_id = :u AND board_id = :b", u=owner.id, b=board_id)["n"] == 0
    done = post(owner.browser(), owner, f"/api/mod_self/{base36encode(board_id)}", reason="Reports say the guild is unattended")
    assert done.status_code in (200, 302), done.get_data(as_text=True)[:200]
    assert one(real_app, "SELECT COUNT(*) AS n FROM mods WHERE user_id = :u AND board_id = :b", u=owner.id, b=board_id)["n"] == 1
    assert len(sayings(real_app, head, "made themselves a guildmaster")) == 1 and "unattended" in sayings(real_app, head, "made themselves")[0]
    bare = post(owner.browser(), owner, "/admin/demod_user", user=target.username)
    assert bare.status_code == 400 and one(real_app, "SELECT COUNT(*) AS n FROM mods WHERE user_id = :u", u=target.id)["n"] == 1, "nothing removed without a reason"
    done = post(owner.browser(), owner, "/admin/demod_user", user=target.username, reason="Abusing guildmaster powers")
    assert done.status_code in (200, 302)
    assert one(real_app, "SELECT COUNT(*) AS n FROM mods WHERE user_id = :u", u=target.id)["n"] == 0
    (told,) = sayings(real_app, target, "removed you as a guildmaster")
    assert "Abusing guildmaster powers" in told and f"+{board_name}" in told
    entry = one(real_app, "SELECT reason, actor_role FROM log_entries WHERE action = 'remove_mod' AND target_user_id = :u AND board_id = :b", u=target.id, b=board_id)
    assert entry["reason"] == "Abusing guildmaster powers" and entry["actor_role"] == "admin"


def test_an_admin_removal_or_ban_names_the_rule_it_breaks_and_a_ban_keeps_a_copy_of_what_it_cites(real_app, make_member):
    """The owner, 2026-11-19: an admin decides by citing the account's activity and outlining how it breaks the rules: a reason AND a rule, and the cited items kept as they were."""
    from ruqqus.helpers.base36 import base36encode
    admin, author = admin_of(make_member), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, "Cited words")
    here = admin.browser()
    for url, fields in ((f"/api/ban_post/{b36}", {}), (f"/api/ban_user/{author.id}", {"days": "3"})):
        bare = post(here, admin, url, reason="Spamming", **fields)
        assert bare.status_code == 400, url
        assert "rule" in bare.get_data(as_text=True).lower(), url
    assert one(real_app, "SELECT is_banned FROM submissions WHERE id = :p", p=post_id)["is_banned"] in (False, None, 0)
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE target_user_id = :u AND action = 'ban_user'", u=author.id) == []
    bad = post(here, admin, f"/api/ban_post/{b36}", reason="Spamming", rule="not_a_rule")
    assert bad.status_code == 400
    done = post(here, admin, f"/api/ban_user/{author.id}", reason="Spamming", rule="evasion", days="3", evidence=[f"p{b36}"])
    assert done.status_code in (200, 302), done.get_data(as_text=True)[:300]
    copies = rows(real_app, "SELECT kind, target_submission_id, board_id, author_id FROM log_snapshots WHERE kind = 'evidence' AND target_submission_id = :p", p=post_id)
    assert len(copies) == 1 and copies[0]["author_id"] == author.id and not copies[0]["board_id"], "a private copy for the account and the admins: no guild's guildmasters"


def test_the_dialogs_remove_answers_204_and_a_purge_needs_the_rule_too(real_app, make_member):
    """The review of 2026-11-19: the removal dialog posts by XHR and a followed redirect read as a failure; a purge must name the rule like a removal."""
    admin, author = admin_of(make_member), make_member()
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, "Removed by dialog")
    here = admin.browser()
    answer = post(here, admin, f"/api/ban_post/{b36}", reason="Spam links", rule="evasion", ajax="1")
    assert answer.status_code == 204, answer.status_code
    from ruqqus.helpers.base36 import base36encode
    from pg.test_log_d3c import add_comment
    cid, c36 = add_comment(author, post_id, board_id, body="to be erased")        # (id, base 36 id)
    bare = post(here, admin, f"/api/purge_comment/{c36}", reason="Doxxing")
    assert bare.status_code == 400 and "rule" in bare.get_data(as_text=True).lower()
    assert post(here, admin, f"/api/purge_comment/{c36}", reason="Doxxing", rule="illegal").status_code == 204


def test_an_escalated_appeal_is_decided_against_the_admin_conflict_rule_the_page_shows(real_app, make_member):
    """An admin who is also the only guildmaster of the guild can't settle the appeal of their own decision while another admin exists, from the page or by a direct POST."""
    from ruqqus.helpers import log_appeals
    mod, member, board_id, board_name, entry, appeal = sole_guildmaster_appeal(real_app, make_member)
    admin = admin_of(make_member)
    db = real_app[1]()
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": mod.id})
    db.commit()
    db.close()
    answer = post(mod.browser(), mod, f"/admin/appeals/{appeal}", outcome="upheld", note="Still stands")
    assert one(real_app, "SELECT status FROM log_appeals WHERE id = :i", i=appeal)["status"] == "open", answer.get_data(as_text=True)[:200]
