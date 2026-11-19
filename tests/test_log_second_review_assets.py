"""What the second review of the Log found (2026-11-17: twelve independent lenses over the combined result of slices D1 to H, each
finding challenged by skeptics) and what was done about it. Tripwires on the source, in the project's usual way; the behaviour is in
the unit tests beside each fix and in tests/pg/test_log_second_review.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def body(source, name, stop="\n    @property"):
    return source.split(f"def {name}(", 1)[1].split(stop, 1)[0]


SUBMISSION = read("ruqqus", "classes", "submission.py")
ACTION_LOG = read("ruqqus", "helpers", "action_log.py")


def test_the_json_api_gives_no_title_for_a_removed_or_deleted_post():
    """C00: GET /api/v2/submissions/<id> and every comment's JSON served the title of a post that was erased, deleted by a child account or
    removed, and no purge ever blanked it."""
    core = body(SUBMISSION, "json_core")
    removed, deleted = core.split("elif self.is_deleted:")[0], core.split("elif self.is_deleted:")[1].split("elif self.hidden_by_guild")[0]
    assert "'title'" not in removed and "'title'" not in deleted
    assert "'guild_name'" not in deleted                      # a deleted copy's guild may be one the reader may not open


def test_a_member_of_one_guild_does_not_read_the_entries_of_another_that_went_private():
    """C02."""
    fetch = body(ACTION_LOG, "fetch", stop="\n\ndef ")
    assert "open_guilds_only = level < 3" in fetch and "not member_boards" not in fetch
    assert "log_entries.board_id IN (" in fetch               # a member still reads their own guild's entries, and only theirs


ALIAS_ROUTES = read("ruqqus", "routes", "alias_actions_pages.py")
ANONYMISE = read("ruqqus", "helpers", "anonymise.py")
POSTS = read("ruqqus", "routes", "posts.py")
COMMENTS = read("ruqqus", "routes", "comments.py")
LOG_PAGES = read("ruqqus", "routes", "log_pages.py")


def test_an_alias_action_has_no_side_effect_that_names_the_account():
    """C19 and C22."""
    assert "remove_from_rooms" not in ALIAS_ROUTES.split("def mod_unexile_alias")[0].split("def mod_exile_alias")[1].split("# NOT")[0]
    assert "_leave_encrypted_rooms" not in ALIAS_ROUTES and "kick_as_bot" not in ALIAS_ROUTES
    target = ALIAS_ROUTES.split("def _target(")[1].split("def _logged(")[0]
    assert "sanction_evidence.usable(item, board.id, user.id, user.id, allow_anonymous=True)" in target


def test_anonymising_ends_a_comments_shield_and_takes_the_posts_it_was_forwarded_as():
    """C17 and C18."""
    comment = ANONYMISE.split("def anonymise_comment(")[1].split("def anonymise_replies(")[0]
    assert "comment.distinguish_level = 0" in comment and "comment.gm_distinguish = 0" in comment
    replies = ANONYMISE.split("def anonymise_replies(")[1]
    assert "distinguish_level = 0, gm_distinguish = 0" in replies and "comment_forwardrels" in replies
    assert "distinguish_level = 0, gm_distinguish = 0" in read("ruqqus", "helpers", "account_anonymise.py")


def test_a_deleted_posts_thread_is_a_record_and_the_deletes_run_one_at_a_time_per_account():
    """C07, C08, C09."""
    edit = POSTS.split("def edit_post(")[1].split("COLLAB_EDIT_REFUSAL")[0]
    assert "p.is_banned or p.is_deleted" in edit and "primary.is_banned or primary.is_deleted" in edit
    assert "primary.is_deleted or p.is_deleted" in POSTS.split("def apply_post_edit(")[1].split("old_url")[0]
    assert "post.is_banned or post.deleted_utc > 0" in COMMENTS.split("def api_comment(")[1].split("the author may limit who can comment")[0]
    for source, fn, item in ((POSTS, "delete_post_as", "post"), (COMMENTS, "delete_comment_as", "c")):
        part = source.split(f"def {fn}(")[1].split("now = int(time.time())")[0]
        assert "deletion_store.lock_deletes(" in part and f"g.db.refresh({item})" in part, fn
    assert "pg_advisory_xact_lock" in read("ruqqus", "helpers", "deletion_store.py")
    assert "pg_advisory_xact_lock" in read("ruqqus", "helpers", "log_appeals.py").split("def file_appeal(")[1].split("entry = _entry")[0]
    assert "except IntegrityError" in read("ruqqus", "routes", "log_appeal_pages.py").split("def log_appeal_post(")[1].split("def _quote(")[0]


def test_the_log_thread_has_the_post_pages_gates_and_reads_a_retention_purge_as_a_delete():
    """C01 and C03."""
    thread = LOG_PAGES.split("def log_thread(")[1].split("def _")[0]
    gates, redirect = thread.index("post.in_private_guild_for(v)"), thread.index("return redirect(post.permalink)")
    assert gates < redirect and "post.board.is_banned and not post.is_profile_post" in thread and "post_hidden(post, v)" in thread
    assert "(c.purged_utc and not (c.deleted_utc or 0) > 0)" in thread and 'scrubbed_copy.get("scrubbed")' in thread


def test_every_notice_that_links_an_item_or_the_log_is_held_from_a_limited_qr_sign_in():
    """C14: a notice with `%(item)s` or `%(log)s` names the member's item by id (an anonymous one included): a relayed code's limited session reads the
    notifications, and must not."""
    from ruqqus.helpers import log_notices
    linking = {name: value for name, value in vars(log_notices).items()
               if name.isupper() and isinstance(value, str) and ("%(item)s" in value or "%(log)s" in value or "%(link)s" in value)}
    assert len(linking) >= 14
    missing = sorted(name for name, value in linking.items() if value not in log_notices.HELD_FROM_LIMITED)
    assert not missing, f"these notices link an item or an entry and a limited sign-in would read them: {missing}"
    user = read("ruqqus", "classes", "user.py")
    assert "log_notices.HELD_FROM_LIMITED" in user.split("def _without_own_anonymous(")[1].split("def _notification_post_query")[0]
    assert "log_notices.ALIAS_EXILED" in ALIAS_ROUTES
