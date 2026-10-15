"""Sensitive accounts and All cross the schema, the models, All / For You / trending / curations, Settings > Content,
the guild settings, the admins' force / unlock, new posts, the Child filter, ads and the profile. The rules and their
SQL are tests/test_sensitive.py."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def head(source, name):
    return source.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]


MIGRATION = read("scripts", "migrations", "2026-10-18_sensitive_accounts.sql")
SCHEMA = read("schema.sql")
FRONT = read("ruqqus", "routes", "front.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
USER = read("ruqqus", "classes", "user.py")


def table(name):
    return SCHEMA.split(f"CREATE TABLE public.{name} (")[1].split("\n);")[0]


def test_the_migration_the_schema_and_the_models_agree():
    for column, where in (("is_sensitive", "users"), ("sensitive_forced", "users"), ("all_shows_sensitive", "users"),
                          ("sensitive_forced", "boards")):
        line = f"{column} boolean DEFAULT false NOT NULL"
        assert f"ALTER TABLE {where} ADD COLUMN IF NOT EXISTS {line};" in MIGRATION, (where, column)
        assert line in table(where), (where, column)
    for column in ("is_sensitive", "sensitive_forced", "all_shows_sensitive"):
        assert f"    {column} = Column(Boolean, default=False)" in USER, column
    assert "sensitive_forced=Column(Boolean, default=False)" in read("ruqqus", "classes", "boards.py")
    assert "users_sensitive_idx" in MIGRATION and "users_sensitive_idx" in SCHEMA


def test_all_is_everything_but_sensitive_content_and_keeps_the_guild_opt_out():
    front = func(FRONT, "frontlist", "\n@app.")
    assert "posts = hide_sensitive(posts, v, Submission)" in front
    assert front.index("hide_fewer(") < front.index("hide_sensitive(")
    assert "board_id!=1" not in front and "board_id != 1" not in front           # +general is part of All
    assert "subcat_id.notin_" not in front                                          # no categories kept off
    assert "Board.all_opt_out == False" in front and "all_opt_out=False" in front  # the guild's own choice stays


def test_for_you_trending_and_a_curations_whole_site_pool_follow_all():
    for_you = func(USER, "for_you_idlist", "\n    @")
    assert "posts = hide_sensitive(posts, self, Submission)" in for_you
    assert "board_id != 1" not in for_you and "subcat_id.notin_" not in for_you
    trending = func(read("ruqqus", "helpers", "trending_store.py"), "_load")
    assert "return hide_sensitive(db.query(" in trending and "), None, Submission).order_by(" in trending
    assert "board_id != 1" not in trending and "Board.all_opt_out == False" in trending
    pool = func(read("ruqqus", "helpers", "curation_feed.py"), "site_pool")
    assert "posts = hide_sensitive(posts, v, Submission)" in pool and "board_id != 1" not in pool


def test_the_rule_never_judges_an_anonymous_post_by_whose_it_is():
    rule = func(read("ruqqus", "helpers", "sensitive.py"), "hide_sensitive")
    assert "or_(model.is_anonymous.is_(True), model.author_id.notin_(sensitive_user_ids()))" in rule
    vis = read("ruqqus", "helpers", "visibility.py")
    assert "return or_(model.is_anonymous.is_(True), model.author_id.notin_(sensitive_user_ids()))" in vis
    assert vis.count("account_flag=not getattr(") == 2                          # post_hidden and comment_hidden


def test_the_viewers_choice_is_saved_and_shows_at_once():
    route = func(SETTINGS, "settings_all_shows_sensitive")
    assert 'v.all_shows_sensitive = request.values.get("on") == "true"' in route
    assert "muting_rules.clear_cached_feeds(v)" in route
    assert "@validate_formkey" in head(SETTINGS, "settings_all_shows_sensitive")
    shows = func(read("ruqqus", "helpers", "sensitive.py"), "shows_sensitive")
    assert "return viewer_level(v) < CHILD" in shows                            # the Child filter wins


def test_a_member_marks_their_account_unless_an_admin_forced_it():
    route = func(SETTINGS, "settings_sensitive_account")
    assert "sensitive_rules.owner_refusal(v.sensitive_forced, wanted)" in route and ", 403" in route
    assert "@validate_formkey" in head(SETTINGS, "settings_sensitive_account")
    guild = func(read("ruqqus", "routes", "boards.py"), "mod_bid_settings_sensitive")
    assert "sensitive_rules.owner_refusal(board.sensitive_forced, wanted)" in guild and ", 403" in guild


def test_admins_force_and_unlock_and_the_owners_are_told():
    for name in ("admin_sensitive_user", "admin_sensitive_guild"):
        assert "@admin_level_required(3)" in head(SETTINGS, name) and "@validate_formkey" in head(SETTINGS, name)
        body = func(SETTINGS, name)
        assert "send_notification(" in body and "if force:" in body, name
    assert "v.admin_level <= (target.admin_level or 0)" in func(SETTINGS, "admin_sensitive_user")
    guild = func(SETTINGS, "admin_sensitive_guild")
    assert "g.db.add(ModAction(" in guild and "for guildmaster in board.mods:" in guild


def test_a_sensitive_accounts_posts_are_marked_when_written_and_an_edit_keeps_the_mark():
    posts = read("ruqqus", "routes", "posts.py")
    assert 'is_sensitive=sensitive_rules.marks_post(request.form.get("sensitive", ""), v)' in posts
    assert "sensitive_rules.marks_post(sensitive, primary.author)" in func(posts, "edit_post", "\n@app.")


def test_pages_say_so():
    filters = read("ruqqus", "templates", "settings_filters.html")
    assert 'id="all-shows-sensitive"' in filters and "/settings/all_shows_sensitive" in filters
    assert 'id="sensitive-account"' in filters and "{% if v.sensitive_forced %} disabled{% endif %}" in filters
    guild = read("ruqqus", "templates", "guild", "settings.html")
    assert "{% if b.sensitive_forced %} disabled{% endif %}" in guild
    assert 'action="/admin/sensitive/guild"' in read("ruqqus", "templates", "board.html")
    assert 'action="/admin/sensitive/user"' in read("ruqqus", "templates", "userpage.html")
    header = read("ruqqus", "templates", "partials", "profile_header.html")
    assert "{% if u.is_sensitive %}" in header and "profile-x-sensitive" in header
    for style in ("main.scss", "main_dark.scss"):
        assert ".profile-x-chip.profile-x-sensitive" in read("ruqqus", "assets", "style", style)


def test_ads_leave_sensitive_accounts_alone():
    ads = read("ruqqus", "helpers", "ads.py")
    assert "and account_allows(account)" in func(ads, "allows")
    assert 'not getattr(post, "is_anonymous", False) and not account_allows(getattr(post, "author", None))' in ads
    assert 'account=context.get("profile_user")' in read("ruqqus", "routes", "ads.py")


def test_claude_md_describes_it():
    claude = read("CLAUDE.md")
    assert "## Sensitive accounts and All (legacy app)" in claude
    section = claude.split("## Sensitive accounts and All (legacy app)")[1].split("\n## ")[0]
    for part in ("helpers/sensitive.py", "hide_sensitive", "all_shows_sensitive", "sensitive_forced", "all_opt_out",
                 "anonymous", "/admin/sensitive/user", "2026-10-18_sensitive_accounts.sql", "Child"):
        assert part in section, part
    assert not re.search(r"FILTERED_SUBCATS", claude)
