"""Muting a guild crosses the helper, the feeds, the caches, the routes and every place that draws the button. These
checks keep the layers agreeing (the rule is tests/test_muting.py)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


MUTING = read("ruqqus", "helpers", "muting.py")
USER = read("ruqqus", "classes", "user.py")
SETTINGS = read("ruqqus", "routes", "settings.py")
POSTS = read("ruqqus", "routes", "posts.py")
CARD = read("ruqqus", "templates", "submission_listing.html")
PAGE = read("ruqqus", "templates", "submission.html")
BOARD = read("ruqqus", "templates", "board.html")
BOARD_COMMENTS = read("ruqqus", "templates", "board_comments.html")
FILTERS = read("ruqqus", "templates", "settings_guildfilter.html")
SETTINGS_NAV = read("ruqqus", "templates", "settings.html")
JS = read("ruqqus", "assets", "js", "all_js.js")


def func(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def test_the_table_stays_boardblocks_so_every_hidden_guild_became_a_mute():
    assert "from ruqqus.classes.board_relationships import BoardBlock" in func(MUTING, "muted_guild_ids")
    assert "BoardBlock.user_id == v.id" in func(MUTING, "muted_guild_ids")
    assert "class BoardBlock(" in read("ruqqus", "classes", "board_relationships.py")


def test_following_and_the_for_you_weights_leave_a_muted_guild_out():
    idlist = func(USER, "idlist", "\n    def ")
    assert "hide_muted_guilds(posts, self, Submission)" in idlist
    assert "import hide_muted, hide_muted_guilds" in USER
    interests = func(USER, "interest_subcats", "\n    @cache.memoize")
    assert interests.count("Board.id.notin_(muted_boards)") == 3          # subscriptions, followed accounts' ones, votes
    # For You itself already filtered the guild (the older inline subquery): the pairing must stay
    assert func(USER, "for_you_idlist", "\n    def ").count("BoardBlock.board_id") == 2


def test_a_member_who_muted_the_guild_is_not_rung_for_its_posts():
    assert "g.db.query(BoardBlock.user_id).filter_by(board_id=new_post.board_id).subquery()" in POSTS


def test_both_mute_urls_work_and_every_cache_is_cleared():
    block = SETTINGS.split("def settings_block_guild(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/settings/mute_guild", methods=["POST"])' in block
    assert '@app.route("/settings/block_guild", methods=["POST"])' in block
    unblock = SETTINGS.split("def settings_unblock_guild(")[0].rsplit("\n\n\n", 1)[1]
    assert '@app.route("/settings/unmute_guild", methods=["POST"])' in unblock
    assert '@app.route("/settings/unblock_guild", methods=["POST"])' in unblock
    for head in (block, unblock):
        assert "@auth_required" in head and "@validate_formkey" in head
    for body in (func(SETTINGS, "settings_block_guild"), func(SETTINGS, "settings_unblock_guild")):
        assert "muting_rules.clear_guild_mute_caches(v)" in body
    assert "You have already muted +" in SETTINGS
    clear = func(MUTING, "clear_guild_mute_caches")
    assert "clear_cached_feeds(v)" in clear and "v.interest_subcats" in clear and "comment_idlist" in clear
    feeds = func(MUTING, "clear_cached_feeds")
    for name in ("v.idlist", "v.for_you_idlist", "frontlist", "curation_idlist", "searchlisting"):
        assert name in feeds


def test_nothing_says_hide_from_all_any_more_and_every_caller_uses_the_new_urls():
    for name, source in (("card", CARD), ("page", PAGE), ("guild", BOARD), ("guild comments", BOARD_COMMENTS), ("filters", FILTERS)):
        assert "Hide from /all" not in source and "Show in /all" not in source and "Hide guild" not in source, name
        assert "/settings/block_guild" not in source and "/settings/unblock_guild" not in source, name
    assert 'xhr.open("post", "/settings/mute_guild");' in JS
    assert "Muted Guilds</a>" in SETTINGS_NAV and "Hide Guilds" not in SETTINGS_NAV
    assert "Guilds you have muted" in FILTERS and "15 minutes" not in FILTERS


def test_the_card_offers_mute_guild_on_every_feed_not_only_all():
    assert 'request.path=="/all"' not in CARD.split("hide-guild-")[0].rsplit("{% if", 1)[1]
    assert CARD.count('{% if v and not p.is_profile_post and not request.path.startswith("/+") %}') == 2     # dropdown + phone sheet
    assert "Mute guild</a>" in CARD and "Unmute guild</a>" in CARD
    assert "Mute guild</button>" in CARD and "Unmute guild</button>" in CARD


def test_a_phone_can_mute_from_the_guild_banner():
    mobile = BOARD.split("{% block mobileBanner %}")[1]
    assert "'unmute_guild' if v.has_blocked_guild(b) else 'mute_guild'" in mobile


def test_the_muting_helper_documents_the_difference_from_a_curations_leave_out_list():
    assert "MUTING A GUILD" in MUTING and "exclude_authors" in MUTING
    assert "def has_blocked_guild(self, board):" in USER           # test_muting_assets splits user.py on it


def test_claude_md_describes_guild_mutes():
    claude = read("CLAUDE.md")
    assert "Muting a guild" in claude and "hide_muted_guilds" in claude and "/settings/mute_guild" in claude
    section = claude.split("## Muting (legacy app)")[1].split("## Bookmark folders")[0]
    assert "different from a guild's `all_opt_out`" in section.replace("**", "")
