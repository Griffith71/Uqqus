"""Who may share a post or a comment (ruqqus/helpers/sharing.py): anyone when it is public, only its author otherwise;
where the controls are drawn, which routes refuse, and what moving a shared public post to a Circle does."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import Undefined

from ruqqus.helpers import sharing

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def func(source, name):
    return source.split(f"def {name}(")[1].split("\n\n\n")[0]


AUTHOR, MEMBER = NS(id=1), NS(id=2)
PUBLIC = NS(is_public=True, author_id=1)
PRIVATE = NS(is_public=False, author_id=1)      # a Circle post, or one in a private guild that was never public there


# --- the rule ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("viewer", [None, Undefined(), MEMBER, AUTHOR])
def test_anyone_shares_a_public_post_visitors_included(viewer):
    assert sharing.may_share(PUBLIC, viewer) and sharing.post_refusal(PUBLIC, viewer) is None


def test_only_the_author_shares_a_post_that_is_not_public():
    assert sharing.may_share(PRIVATE, AUTHOR)
    for viewer in (None, Undefined(), MEMBER, NS(id=3, admin_level=6)):        # admins may read it, not spread it
        assert not sharing.may_share(PRIVATE, viewer)
        assert sharing.post_refusal(PRIVATE, viewer) == sharing.REFUSAL
    assert not sharing.may_share(None, AUTHOR)


def test_a_comment_follows_the_post_it_is_on_and_its_own_author():
    on_public = NS(post=PUBLIC, author_id=2)
    on_private = NS(post=PRIVATE, author_id=2)
    assert sharing.may_share_comment(on_public, None) and sharing.may_share_comment(on_public, AUTHOR)
    assert sharing.may_share_comment(on_private, MEMBER)                        # the comment's own author
    assert not sharing.may_share_comment(on_private, AUTHOR)                    # the post's author does not share others' words
    assert not sharing.may_share_comment(on_private, None)
    assert sharing.comment_refusal(on_private, NS(id=3)) == sharing.REFUSAL
    assert sharing.may_share_comment(NS(post=None, author_id=2), None)          # nothing private to keep in
    assert not sharing.may_share_comment(None, AUTHOR)


def test_public_means_the_posts_own_rule():
    """Submission.is_public: no audience, and public when made or in a guild that is not private. So a post forwarded
    while public stays shareable after its guild turns private: what was made public stays easy to share."""
    source = read("ruqqus", "classes", "submission.py")
    assert "return not self.audience and (self.post_public or not self.board.is_private)" in source.split("def is_public(self):")[1][:120]


# --- the author is told what moving a shared public post to a Circle does --------------------------------------

def test_the_note_names_the_authors_own_forwards_and_says_what_others_keep():
    assert sharing.narrowing_note([], 0, False) is None
    assert sharing.narrowing_note(["news"], 0, False) == "The copy you forwarded to +news will be taken down."
    assert sharing.narrowing_note(["a", "b", "c"], 0, False) == "The copies you forwarded to +a, +b and +c will be taken down."
    assert sharing.narrowing_note([], 1, False) == "The copy other people forwarded to guilds will stay there."
    assert sharing.narrowing_note([], 3, True) == ("The 3 copies other people forwarded to guilds will stay there. "
                                                   "Reposts of it will show it only to people who can see it.")


# --- where it is drawn -----------------------------------------------------------------------------------------

def test_the_post_share_menu_is_drawn_only_for_whoever_may_share():
    actions = read("ruqqus", "templates", "partials", "post_actions.html")
    shares = actions.split('<li class="list-inline-item post-action-item">')[2].split("</li>")[0]
    menu, rest = shares.split("{% if may_share(p, v) %}")[1].split("{% else %}")
    for item in ("Copy link</a>", "share-chat-item", "Forward</a>", "/repost'"):
        assert item in menu and item not in rest, item
    assert '<span class="post-action post-action-shares" title="Only the author can share this post"' in rest
    assert "_n(shares)" in rest                                                  # the count still shows: counts only
    # a post made for a Circle is still never forwarded or reposted, even by its author
    assert menu.index("Forward</a>") > menu.index("{% if v and not p.audience %}") > menu.index("share-chat-item")


def test_the_shortlink_box_follows_the_rule_and_an_empty_box_is_not_drawn():
    page = read("ruqqus", "templates", "submission.html").split("{% block sidebarblock %}")[1].split("{% endblock %}")[0]
    assert "{% set shortlink_shown = may_share(p, v) %}" in page
    assert "{% if shortlink_shown or p.board_id != p.original_board_id %}" in page
    assert page.index("{% if shortlink_shown %}") < page.index("Shortlink:")


def test_every_comment_share_item_follows_the_rule():
    comments = read("ruqqus", "templates", "comments.html")
    assert "{% set can_share = may_share_comment(c, v) %}" in comments
    assert "{% set can_forward = v and can_share and not (c.post and c.post.audience) %}" in comments
    for marker in ('id="shareDropdownLink-{{ c.base36id }}"', "<span>Copy Link</span>"):
        before = comments.split(marker)[0]
        assert before.rsplit("{% if ", 1)[1].startswith("can_share %}"), marker
    for marker in ('<a id="repost-{{ c.base36id }}"', '<a id="repost-modal-{{ c.base36id }}"', "<span>Forward</span>",
                   "Forward</a>"):
        before = comments.split(marker)[0]
        assert before.rsplit("{% if ", 1)[1].startswith("can_forward %}"), marker
    # Move to folder is in the "..." menu, so a comment that can't be shared can still be filed
    more = comments.split('aria-labelledby="dropdownMoreLink"')[1].split("</div>")[0]
    assert "bookmark-folder-item" in more


def test_the_template_globals_are_registered():
    jinja = read("ruqqus", "helpers", "jinja2.py")
    assert '@app.template_global("may_share")' in jinja and '@app.template_global("may_share_comment")' in jinja


# --- what the routes refuse ------------------------------------------------------------------------------------

@pytest.mark.parametrize("module, name, call", [
    ("posts.py", "forward_post", "sharing.post_refusal(post, v)"),
    ("posts.py", "repost_post", "sharing.post_refusal(post, v)"),
    ("comments.py", "forward_comment", "sharing.comment_refusal(comment, v)"),
    ("comments.py", "repost_comment", "sharing.comment_refusal(comment, v)"),
])
def test_every_way_of_sharing_through_the_server_asks_the_rule_before_anything_is_written(module, name, call):
    body = func(read("ruqqus", "routes", module), name)
    assert f"refusal = {call}" in body
    assert 'return {"error": refusal}, 403' in body
    assert body.index(call) < body.index("in_private_guild_for(v)") + 200                # right after the visibility check
    written = " = create_forward_post" if "forward" in name else "g.db.add("
    assert body.index(call) < body.index(written)


def test_the_helper_is_imported_after_the_star_import():
    for module in ("posts.py", "comments.py"):
        source = read("ruqqus", "routes", module)
        assert source.index("from ruqqus.helpers import sharing") > source.index("from ruqqus.classes import *"), module


# --- moving a shared public post to a Circle ---------------------------------------------------------------------

def test_a_shared_public_post_may_move_and_only_the_authors_own_forwards_go():
    routes = read("ruqqus", "routes", "circles.py")
    copies = func(routes, "_shared_copies")
    assert "Submission.repost_id == post.id, Submission.deleted_utc == 0" in copies
    assert "primary_submission_id=post.id, forwarded_by_id=author_id" in copies
    assert "RepostRelationship.user_id != author_id" in copies
    withdraw = func(routes, "_withdraw")
    assert "copy.deleted_utc = now" in withdraw and "ForwardRelationship.forwarded_by_id == author_id" in withdraw
    assert "RepostRelationship" not in withdraw                                    # reposts follow the post itself
    body = func(routes, "circle_post_audience")
    assert body.index("own, others, reposted = _shared_copies(post, v.id)") < body.index('return jsonify({"error": " ".join(notes), "confirm": True}), 409')
    assert "own = []" in body                                                      # making it public withdraws nothing
    assert "already been forwarded" not in read("ruqqus", "helpers", "circles.py")


def test_the_sheet_says_what_happens_to_copies():
    modal = read("ruqqus", "templates", "partials", "audience_modal.html")
    assert "takes down the copies you forwarded; copies other people forwarded stay in their guilds" in modal
    assert "has been shared" not in modal


def test_the_forwarded_to_lists_never_name_a_private_guild_or_a_copy_taken_down():
    for path, name in ((("ruqqus", "classes", "submission.py"), "forwards_shown_to"), (("ruqqus", "classes", "comment.py"), "forwards_shown_to")):
        body = read(*path).split(f"def {name}(self, v):")[1].split("\n\n")[0]
        for condition in ("not f.deleted_utc", "not f.is_banned", "not f.hidden_by_guild", "not f.board.is_banned",
                          "not f.in_private_guild_for(v)"):
            assert condition in body, (path, condition)
    page = read("ruqqus", "templates", "submission.html")
    assert page.count("{% set shown_forwards = p.forwards_shown_to(v) if p.is_profile_post else [] %}") == 2
    assert "{% for fp in p.forwards %}" not in page and "p.forwards | length" not in page
    comments = read("ruqqus", "templates", "comments.html")
    assert "{% set shown_forwards = c.forwards_shown_to(v) %}" in comments and "c.forwarded_posts" not in comments
