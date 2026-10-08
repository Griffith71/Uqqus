"""A Circle guild crosses the model, the create form, the privacy rules, the composer, the guild page, the settings tab,
the renewal job and the guard. These checks keep the layers agreeing and keep the promises: a Circle guild is private for
good, only its members see what is in it, what members write stays off their profiles, and a removed member stops paying."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


BOARDS = read("ruqqus", "routes", "boards.py")
POSTS = read("ruqqus", "routes", "posts.py")
CIRCLES = read("ruqqus", "routes", "circles.py")
STORE = read("ruqqus", "helpers", "circle_store.py")
GUARD = read("ruqqus", "helpers", "circle_guard.py")
USER = read("ruqqus", "classes", "user.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
COMPOSER = read("ruqqus", "templates", "partials", "inline_composer.html")
COMPOSER_JS = read("ruqqus", "assets", "js", "inline_composer.js")


def func(source, name, nxt="\n@app."):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def test_the_column_is_in_the_model_the_schema_and_the_migration():
    assert "is_circle=Column(Boolean, nullable=False, default=False)" in read("ruqqus", "classes", "boards.py")
    table = read("schema.sql").split("CREATE TABLE public.boards (")[1].split("\n);")[0]
    assert "    is_circle boolean DEFAULT false NOT NULL" in table
    assert "ALTER TABLE boards ADD COLUMN IF NOT EXISTS is_circle boolean NOT NULL DEFAULT false;" in read("scripts", "migrations", "2026-10-10_circle_guilds.sql")


# --- private for good -----------------------------------------------------------------------------------

def test_a_circle_guild_is_made_private_and_a_circle_at_creation_only():
    body = func(BOARDS, "create_board_post", "\n@app.route")
    assert 'circle = request.form.get("circle", "") == "true"' in body
    assert "is_private=circle," in body and "is_circle=circle" in body
    form = read("ruqqus", "templates", "make_board.html")
    assert 'name="circle" value="true"' in form and "This can't be changed later." in form


def test_nothing_can_make_a_circle_guild_public():
    assert "if board.is_circle:\n        return jsonify({\"error\": \"A Circle guild is always private.\"}), 409" in func(BOARDS, "mod_bid_settings_private", "\n@app.route")
    # the three places that open a guild up when its last guildmaster goes: a Circle guild stays private
    assert "if board.mods_count == 0 and not board.is_circle:" in BOARDS
    assert "if b.mods_count == 0 and not b.is_circle:" in USER
    assert "if b.mods_count == 0 and not b.is_circle:" in SETTINGS
    # and no other writer of is_private exists outside the settings route
    writers = [path.name for path in (ROOT / "ruqqus").rglob("*.py")
               if re.search(r"\b(b|board)\.is_private = (False|bool)", path.read_text(encoding="utf-8"))]
    assert sorted(writers) == ["boards.py", "settings.py", "user.py"], writers


def test_removing_a_paying_member_stops_the_charges():
    body = func(BOARDS, "mod_unapprove_bid_user", "\n@app.route")
    assert body.index("x.is_active = False") < body.index("circle_store.end_guild_member(g.db, board.id, user.id)") < body.index("g.db.commit()")


# --- what is in it ----------------------------------------------------------------------------------------

def test_a_post_can_be_made_straight_in_a_circle_guild_by_a_member_only():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert 'circle_guild_name = request.form.get("circle_guild", "").strip().lstrip("+")' in body
    for refusal in ("not circle_board.is_circle or circle_board.is_banned", "circle_board.has_ban(v)",
                    "not (circle_board.has_contributor(v) or circle_board.has_mod(v)) and v.admin_level < 4", "elif forward_guild_names:"):
        assert refusal in body, refusal
    assert "board = circle_board or get_guild(PROFILE_BOARD_NAME)" in body
    assert "audience, audience_error = circle_rules.GUILD, None" in body
    # the same refusals as any Circle post (anonymous, co-authors, an own video)
    assert body.index("audience, audience_error = circle_rules.GUILD, None") < body.index("circle_rules.post_refusal(")
    assert "post_public=not audience and not board.is_private," in body


def test_a_post_in_a_circle_guild_tells_the_guilds_members_and_not_the_authors_followers():
    body = func(POSTS, "submit_post", "\n@app.route")
    assert "if new_post.audience == circle_rules.GUILD:\n        follower_ids = []" in body
    assert "circle_guard.eligible_ids(g.db, v.id, new_post.audience, board_id=new_post.board_id)" in body


def test_what_members_write_in_a_circle_guild_is_not_on_their_profile():
    assert "authored = authored.filter(Submission.audience != circles.GUILD)" in USER.split("def userpagelisting(")[1].split("def commentlisting(")[0]
    assert "circles," in USER.split("from ruqqus.helpers import anonymity,")[1].split("\n")[0]


def test_the_guard_asks_the_guilds_members_for_audience_three():
    assert "if audience == circles.GUILD:\n        return in_guild(db, board_id, viewer.id)" in GUARD
    assert GUARD.index("ADMIN_LEVEL") < GUARD.index("return in_guild(db, board_id, viewer.id)")
    for needle in ("is_active = :yes", "accepted = :yes AND COALESCE(invite_rescinded, :no) = :no", "FROM bans WHERE user_id = :u AND board_id = :b AND is_active = :yes"):
        assert needle in GUARD, needle
    assert "return may_see(db, post.audience, post.author_id, viewer, now, getattr(post, \"board_id\", None))" in GUARD
    assert "found is None or may_see(db, found[0], found[1], viewer, now, found[2])" in GUARD


# --- the composer and the guild page --------------------------------------------------------------------

def test_the_composer_on_a_circle_guild_posts_straight_in_and_hides_what_would_take_it_elsewhere():
    assert "{% set circle_guild = board and board.is_circle %}" in COMPOSER
    assert '{% if circle_guild %} data-circle-guild="{{ board.name }}"{% endif %}{% if board %} data-guild="{{ board.name }}"{% endif %}>' in COMPOSER
    assert COMPOSER.count("{% if circle_guild %} hidden{% endif %}") == 4          # forward, audience, anonymity, co-authors
    assert "var presetGuild = circleGuild ? null : form.getAttribute('data-guild');" in COMPOSER_JS
    assert "['audience', 'anonymous', 'coauthors', 'forward_guilds'].forEach(function (name) { data.delete(name); });" in COMPOSER_JS
    assert "data.set('circle_guild', circleGuild);" in COMPOSER_JS


def test_the_guild_page_offers_join_circle_and_the_settings_have_a_tab_for_it():
    page = read("ruqqus", "templates", "board.html")
    assert '{% from "partials/circle_guild_button.html" import circle_guild_button %}' in page
    assert "{{ circle_guild_button(b, v) }}" in page and "{{ circle_guild_button(b, v, true) }}" in page
    macro = read("ruqqus", "templates", "partials", "circle_guild_button.html")
    assert "/api/circle/guild/{{ b.name }}/subscribe" in macro and "| safe" not in macro
    tabs = read("ruqqus", "templates", "guild", "guild_settings.html")
    assert tabs.count("/mod/circle") >= 4 and tabs.count("{% if b.is_circle and mod and mod.perm_full %}") == 2


def test_only_the_founder_sets_the_price_and_anyone_but_a_guildmaster_is_turned_away_from_the_tab():
    head = lambda name: CIRCLES.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]
    assert "@is_guildmaster(\"full\")" in head("board_circle_settings") and "@auth_required" in head("board_circle_settings")
    assert "@is_guildmaster(\"full\")" in head("board_circle_price") and "@validate_formkey" in head("board_circle_price")
    assert "if board.creator_id != v.id:\n        return _fail(\"Only the guild's founder can set the price.\", 403)" in func(CIRCLES, "board_circle_price", "\n\n\n")
    assert "circle_store.set_board_price(g.db, board.id, price)" in func(CIRCLES, "board_circle_price", "\n\n\n")


def test_joining_and_leaving_need_a_login_the_form_key_and_a_balance_that_is_not_negative():
    for name, extra in (("circle_guild_subscribe", ['@no_negative_balance("toast")', "@is_not_banned"]), ("circle_guild_cancel", ["@auth_required"])):
        head = CIRCLES.split(f"def {name}(")[0].rsplit("\n\n\n", 1)[1]
        assert "@validate_formkey" in head, name
        for item in extra:
            assert item in head, (name, item)
    body = func(CIRCLES, "circle_guild_subscribe", "\n\n\n")
    assert body.index("g.db.flush()") < body.index("circle_store.subscribe_guild(g.db, board.id, v.id)")
    assert body.index("g.db.commit()") < body.index("g.db.refresh(v)")
    assert "board = get_guild(name or \"\", graceful=True)" in CIRCLES and "not board.is_circle or board.is_banned" in CIRCLES


# --- the money and the job --------------------------------------------------------------------------------

def test_a_guilds_coins_go_to_its_founder_and_a_lost_membership_takes_the_access_with_it():
    body = func(STORE, "subscribe_guild", "\n\ndef ")
    assert "founder = guild.creator_id" in body and "_pay(db, payer_id, founder, circle.id, price, \"subscribe\", now)" in body
    assert body.index("_pay(") < body.index("join_guild(db, board_id, payer_id, founder, now)")
    renew = func(STORE, "renew_due", "\n\n# ----")
    assert "leave_guild(db, row.board_id, row.user_id)" in renew and "exiled_from(db, row.board_id, row.user_id)" in renew
    assert "COALESCE(c.user_id, b.creator_id) AS owner_id" in STORE


def test_the_job_tells_a_member_whose_guild_membership_lapsed_and_names_the_guild():
    job = read("scripts", "renew_circles.py")
    assert "for event, payer, owner, coins, board in events if event == \"lapsed\"" in job
    assert "Your membership of [+{board.name}](/+{board.name}) ended" in job


def test_the_settings_page_lists_the_guilds_a_member_pays_to_be_in():
    assert "circle_store.guild_memberships(g.db, v.id)" in CIRCLES
    page = read("ruqqus", "templates", "settings_circles.html")
    assert 'id="circle-guilds-in"' in page and "/api/circle/guild/" in page


def test_claude_md_describes_circle_guilds_and_the_new_exception_to_where_a_post_lives():
    doc = read("CLAUDE.md")
    assert "### Circle guilds" in doc
    for needle in ("private for good", "straight in", "founder", "not on their profile", "Circle guild"):
        assert needle in doc, needle
    assert "except inside a Circle guild" in doc
