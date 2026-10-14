"""Tips: the boost counts in the ranked sorts only, the routes commit the tip before telling anyone, the post shows the
coins and never who tipped, and the schema agrees with the migration (helpers/tips.py, helpers/tips_store.py,
routes/paypal.py, partials/post_actions.html)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def func(source, name):
    body = source.split(f"\ndef {name}(")[1]
    return re.split(r"\n(?=@|def |class )", body, maxsplit=1)[0]


def sql_function(source, name):
    return re.search(rf"FUNCTION public\.{name}\(public\.submissions\)[\s\S]*?\$_\$;", source).group(0)


def test_the_boost_is_in_hot_and_best_only():
    for source in (read("schema.sql"), read("scripts/migrations/2026-10-13_tips.sql")):
        assert "$1.upvotes - $1.downvotes + $1.tip_boost" in sql_function(source, "rank_hot")
        assert "$1.upvotes - $1.downvotes + $1.tip_boost + 1" in sql_function(source, "rank_best")
    schema = read("schema.sql")
    for name in ("score", "rank_fiery", "rank_activity"):
        assert "tip_boost" not in sql_function(schema, name), name
    # the score shown and the Top sort stay pure votes
    assert "post.score_top = post.score" in read("ruqqus/routes/votes.py")
    assert "tip_boost" not in read("ruqqus/classes/mix_ins.py")
    assert "+ func.coalesce(Submission.tip_boost, 0), 0)" in read("ruqqus/helpers/curation_feed.py")


def test_the_routes_record_the_tip_and_commit_before_the_notification():
    src = read("ruqqus/routes/paypal.py")
    for name, record in (("gift_post_pid", "tips_store.record(g.db, v.id, coins, post_id=post.id)"),
                         ("gift_comment_pid", "tips_store.record(g.db, v.id, coins, comment_id=comment.id)")):
        body = func(src, name)
        assert 'tips.parse_coins(request.args.get("coins"))' in body and "int(request.args" not in body, name
        assert "in_private_guild_for(" in body and "is_anonymous" in body, name
        assert body.index(record) < body.index("g.db.commit()") < body.index("send_notification("), name
        assert "AwardRelationship(" not in body, name
    post = func(src, "gift_post_pid")
    assert post.index("tips_store.recompute_post(") < post.index("post.score_hot = post.rank_hot") < post.index("g.db.commit()")
    assert "post.score_top" not in post
    note = func(src, "_tip_notification")
    assert "Someone" in note and "v.username" not in note


def test_the_post_shows_coins_never_who_tipped():
    # the coins tipped are one of the counted actions under a post (partials/post_actions.html), on the card and the
    # post page, desktop and phone; the meta line no longer repeats them
    actions = read("ruqqus/templates/partials/post_actions.html")
    assert "{% set coins = p.tip_coins or 0 %}" in actions and "_n(coins)" in actions
    assert "| safe" not in actions and "username }}'" not in actions.split("post-action-tip")[0]
    for page in ("submission_listing.html", "submission.html"):
        src = read(f"ruqqus/templates/{page}")
        assert '{% import "partials/post_actions.html" as post_actions %}' in src and src.count("counted_actions(") == 2, page
        assert "p.award_count" not in src and "tip_badge(" not in src, page
    comments = read("ruqqus/templates/comments.html")
    assert 'class="tip-badge"' in comments and "ruqqus-coin.png\" style" not in comments
    # Tip is in the row (no longer in the "..." menu or the phone sheet), and never on one's own, an anonymous, a
    # removed or a deleted post, or one whose author's account is deleted
    assert actions.count("tipModal2(") == 1
    gate = "{% if v and v.id != p.author_id and not p.is_anonymous and not author_of(p, v).is_deleted and not anon_hidden(p, v) and p.deleted_utc == 0 and not p.is_banned %}"
    assert actions.count(gate) == 2
    for page in ("submission_listing.html", "submission.html"):
        assert "tipModal2(" not in read(f"ruqqus/templates/{page}"), page
    for sheet in ("main.scss", "main_dark.scss"):
        css = read(f"ruqqus/assets/style/{sheet}")
        assert ".tip-badge {" in css and ".tip-badge-count {" in css, sheet


def test_schema_and_migration_agree():
    migration, schema = read("scripts/migrations/2026-10-13_tips.sql"), read("schema.sql")
    for constraint in ("award_post_constraint", "award_comment_constraint", "award_constraint"):
        assert f"DROP CONSTRAINT IF EXISTS {constraint};" in migration
        assert f"ADD CONSTRAINT {constraint}" not in schema
    assert "ADD COLUMN IF NOT EXISTS coins integer NOT NULL DEFAULT 1;" in migration
    assert "    coins integer DEFAULT 1 NOT NULL,\n    created_utc integer DEFAULT 0 NOT NULL\n);" in schema
    assert "ADD COLUMN IF NOT EXISTS tip_boost double precision NOT NULL DEFAULT 0;" in migration
    assert "    tip_coins integer DEFAULT 0 NOT NULL,\n    tip_boost double precision DEFAULT 0 NOT NULL\n);" in schema
    assert "tip_coins = Column(Integer, default=0)" in read("ruqqus/classes/submission.py")
    assert "coins=Column(Integer, default=1)" in read("ruqqus/classes/paypal.py")
    assert "scripts/recompute_tip_boost.py --once" in migration
