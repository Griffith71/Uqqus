"""The Log's wiring (helpers/action_log.py): every writer of a decision reaches it, nothing private leaves it, and the
places that used to lose a decision (a ban overwrote the last one's reason) now keep it. The behaviour is in
tests/test_action_log.py; this is the source, read the way the project's other asset tests read it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


ACTION_LOG = read("ruqqus", "helpers", "action_log.py")
MOD_LOGS = read("ruqqus", "classes", "mod_logs.py")
USER = read("ruqqus", "classes", "user.py")
ADMIN_API = read("ruqqus", "routes", "admin_api.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
WIKI = read("ruqqus", "routes", "guild_wiki.py")
LOG_PAGES = read("ruqqus", "routes", "log_pages.py")


def body(source, name):
    return source.split(f"def {name}(", 1)[1].split("\n@app.", 1)[0]


# --- every ModAction reaches the Log -------------------------------------------------------------------------------------------

def test_every_mod_action_is_copied_into_the_log_as_it_is_made():
    assert '@event.listens_for(ModAction, "after_insert")' in MOD_LOGS
    listener = MOD_LOGS.split('@event.listens_for(ModAction, "after_insert")', 1)[1].split("\nACTIONTYPES", 1)[0]
    assert "action_log.mirror_modaction(connection, target.id" in listener
    assert "connection.begin_nested()" in listener                     # a failed copy never loses the mod action itself
    assert "logging.getLogger(__name__).exception" in listener         # and it is never silent


def test_the_log_is_a_helper_no_model_and_no_route_module_has_its_name():
    assert not (ROOT / "ruqqus" / "classes" / "action_log.py").exists() and not (ROOT / "ruqqus" / "routes" / "action_log.py").exists()
    assert not (ROOT / "ruqqus" / "routes" / "log.py").exists()           # the star import of the routes would cover a helper's name
    assert "from .log_pages import *" in read("ruqqus", "routes", "__init__.py")


# --- bans and unbans are kept ------------------------------------------------------------------------------------------------------

def test_every_ban_and_unban_is_written_where_it_happens():
    ban = body(USER, "ban")
    assert "action_log.record_ban(g.db, self.id" in ban and "automatic=acting is None" in ban
    unban = body(USER, "unban")
    assert "action_log.record_unban(g.db, self.id" in unban
    assert "self.ban_reason" not in unban.split("action_log.record_unban")[1]          # the stale reason is not what is recorded


def test_an_admins_ban_needs_a_public_reason_names_the_rule_and_hides_the_alts():
    route = body(ADMIN_API, "ban_user")
    assert 'rule = action_log.parse_rule(request.form.get("rule", ""))' in route
    assert "if not action_log.clean_reason(reason):" in route and "), 400" in route.split("clean_reason(reason)")[1].split("notice")[0]
    assert route.count("rule=rule, staff_note=message, notice_sent=True") == 2
    assert 'staff_note=f"alt of @{user.username}", floor=action_log.STAFF' in route         # the public never learns who is linked
    assert "user.unban(admin=v" in body(ADMIN_API, "unban_user")
    form = read("ruqqus", "templates", "userpage.html")
    assert 'name="reason" placeholder="Ban Reason (public, required)" required' in form and 'name="rule"' in form


def test_every_reason_the_site_gives_for_its_own_ban_is_classified():
    reasons = set()
    for path in (ROOT / "ruqqus").rglob("*.py"):
        if path.name in ("admin_api.py", "signup_guard_pages.py"):
            continue                                                      # an admin's own words, not the site's
        for call in re.findall(r"\.ban\(([^)]*)\)", path.read_text(encoding="utf-8")):
            m = re.search(r'reason=(?:N_\()?"([^"]+)"', call)
            if m:
                reasons.add(m.group(1))
    assert reasons, "no automatic bans found: the pattern is wrong"
    namespace = {}
    exec(ACTION_LOG.split("AUTOMATIC_REASONS = ", 1)[1].split("\n}\n", 1)[0].join(["AUTOMATIC_REASONS = ", "\n}"]).replace("PUBLIC", "0").replace("STAFF", "2").replace("LEGAL", "3"), namespace)
    assert reasons <= set(namespace["AUTOMATIC_REASONS"]), reasons - set(namespace["AUTOMATIC_REASONS"])
    assert namespace["AUTOMATIC_REASONS"]["Evading a site-wide ban"][1] == 2          # detection is never public
    assert namespace["AUTOMATIC_REASONS"]["Sexualizing minors"][1] == 3


def test_the_spam_path_no_longer_passes_an_argument_user_ban_does_not_take():
    for name in ("comments.py", "posts.py"):
        assert not re.search(r"^\s+include_alts\s*=", read("ruqqus", "routes", name), re.M), name        # a call's argument, not a comment
    assert "def ban(self, admin=None, reason=None,  days=0, rule=None, staff_note=\"\", floor=0, notice_sent=False, cascade=True)" in USER


# --- what an admin decides is kept with the entry ------------------------------------------------------------------------------------

def test_the_removal_routes_put_the_rule_and_reason_on_the_entry():
    for name in ("ban_post", "unban_post", "purge_post", "api_ban_comment", "api_unban_comment", "purge_comment"):
        assert '_decided(ma, rule=request.form.get("rule"), reason=request.form.get("reason"))' in body(ADMIN_API, name), name
    helper = body(ADMIN_API, "_decided")
    assert "g.db.flush()" in helper and "action_log.annotate(g.db, ma.id, rule=rule, reason=reason)" in helper


def test_a_purge_or_an_obliteration_erases_the_private_copies_too():
    assert "action_log.scrub_for(g.db, post_ids=[post.id])" in body(ADMIN_API, "purge_post")
    assert "action_log.scrub_for(g.db, comment_ids=[comment.id])" in body(ADMIN_API, "purge_comment")
    assert "action_log.scrub_for(g.db, target_ids, comment_ids)" in body(ADMIN_API, "admin_obliterate_post")
    assert "action_log.scrub_for(g.db, comment_ids=[comment.id])" in body(ADMIN_API, "admin_obliterate_comment")


def test_an_obliteration_is_a_public_stub_with_the_rule_only_and_the_detail_for_level_four():
    helper = body(ADMIN_API, "_obliterated")
    assert "_decided(ma, rule=_OBLITERATE_RULE" in helper                  # no reason on the public stub
    assert '"suppress_content", v.id' in helper and "reason=reason" in helper
    assert 'type_of("suppress_content")' not in helper and '"suppress_content"' in ACTION_LOG
    assert "LEGAL_ACTIONS = {\"suppress_content\", \"legal_removal\"}" in ACTION_LOG


def test_the_csam_route_keeps_who_decided_a_year_of_text_and_the_bans_for_level_four_only():
    route = body(ADMIN_API, "admin_csam_nuke")
    assert "legal_hold=True" in route and 'staff_note=f"decided by @{v.username}"' in route
    assert route.count("floor=action_log.LEGAL") == 2                       # the author and every alt


def test_the_actions_that_wrote_nothing_now_write_an_entry():
    assert '"ban_guild"' in body(ADMIN_API, "api_ban_guild") and '"unban_guild"' in body(ADMIN_API, "api_unban_guild")
    assert '"clear_display_name"' in body(SETTINGS, "admin_clear_display_name")
    assert "was: {target.display_name}" in body(SETTINGS, "admin_clear_display_name")          # recorded before it is erased
    assert body(SETTINGS, "admin_clear_display_name").index("record_quietly") < body(SETTINGS, "admin_clear_display_name").index('target.display_name = ""')
    assert '"force_sensitive_user"' in body(SETTINGS, "admin_sensitive_user")
    assert '"remove_wiki_revision"' in body(WIKI, "admin_wiki_remove_revision")


# --- nothing private leaves ----------------------------------------------------------------------------------------------------------

def test_the_public_columns_never_include_the_private_ones():
    public = re.search(r'PUBLIC_COLUMNS = \((.*?)\)\n', ACTION_LOG, re.S).group(1)
    for private in ("staff_note", "snapshot_id", "redacted_by", "legacy_modaction_id", "notice_sent"):
        assert private not in public, private
    fetch = body(ACTION_LOG, "fetch")
    assert "columns = STAFF_COLUMNS if level >= 3 else PUBLIC_COLUMNS" in fetch
    assert 'row["actor_id"] = None' in fetch                                # an admin is never named below staff


def test_a_mod_log_answer_never_names_an_admin():
    assert '"mod": app.config[\'SITE_NAME\'] if self.by_admin else self.user.username' in MOD_LOGS
    # the note's words say "admin" only for an account that really is one (a guildmaster's reason is the same free text)
    assert 'and any(mark in (self._note or "").lower() for mark in action_log.ADMIN_MARKERS))' in MOD_LOGS
    assert "(user.admin_level or 0) >= 3" in MOD_LOGS


def test_the_admin_page_is_staff_only_and_reads_through_the_one_visibility_rule():
    route = body(LOG_PAGES, "admin_log")
    assert "@admin_level_required(3)" in LOG_PAGES.split("def admin_log(")[0].rsplit("@app.route", 1)[1]
    assert "level = action_log.staff_level(v.admin_level)" in route and "action_log.fetch(" in route
    assert "SELECT" not in route                                            # no SQL of its own: who may see what is decided once
    assert "/admin/log" in read("ruqqus", "templates", "admin", "admin_home.html")


def test_a_reason_is_never_drawn_as_html():
    template = read("ruqqus", "templates", "admin", "log.html")
    assert "| safe" not in template and "|safe" not in template


def test_a_notice_can_carry_a_value_named_message():
    alerts = read("ruqqus", "helpers", "alerts.py")
    assert "def notify(user, message, /, **params):" in alerts and "def notify_many(user_ids, message, /, **params):" in alerts
    assert '{"message": message}' in body(ADMIN_API, "ban_user")             # the value that used to collide with the parameter
