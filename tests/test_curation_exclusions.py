"""A curation's own leave-out list (helpers/curation_feed.py `leave_out`): the real SQL on SQLite with stand-in tables,
because the unit tests cannot import the app's models. The rules: an excluded account's posts go, in every guild
(a forwarded copy keeps the original author's id) and when co-authored; an anonymous post of an excluded account
STAYS, because dropping only those would tell the curation's owner which ones the account wrote."""
import sys
from types import ModuleType

import pytest
from sqlalchemy import Boolean, Column, Integer, String, create_engine, select
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import curation_feed as cf
from ruqqus.helpers import feed_algorithm as fa

Base = declarative_base()


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    board_id = Column(Integer)
    author_id = Column(Integer)
    is_anonymous = Column(Boolean)
    repost_id = Column(Integer, default=0)


class PostCoauthor(Base):
    __tablename__ = "post_coauthors"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer)
    user_id = Column(Integer)
    status = Column(String)


@pytest.fixture
def db(monkeypatch):
    classes = ModuleType("ruqqus.classes")
    classes.Submission = Submission
    coauthors = ModuleType("ruqqus.classes.coauthors")
    coauthors.PostCoauthor = PostCoauthor
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes)
    monkeypatch.setitem(sys.modules, "ruqqus.classes.coauthors", coauthors)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Submission(id=1, board_id=10, author_id=5, is_anonymous=False),               # by the excluded account
            Submission(id=2, board_id=10, author_id=5, is_anonymous=True),                # the same account, anonymous
            Submission(id=3, board_id=11, author_id=6, is_anonymous=False),               # an ordinary post in guild 11
            Submission(id=4, board_id=12, author_id=7, is_anonymous=False),               # co-authored with the account
            Submission(id=5, board_id=13, author_id=5, is_anonymous=False, repost_id=1),  # its forward in another guild
            Submission(id=6, board_id=14, author_id=8, is_anonymous=False),               # untouched
            PostCoauthor(post_id=4, user_id=5, status="accepted"),
            PostCoauthor(post_id=6, user_id=5, status="pending"),                         # an invitation not yet accepted
        ])
        session.commit()
        yield session


def left(db, **parts):
    return {i for (i,) in db.execute(cf.leave_out(select(Submission.id), fa.clean(parts)))}


def test_with_nothing_left_out_the_query_is_untouched():
    q = select(Submission.id)
    assert cf.leave_out(q, fa.DEFAULT) is q


def test_an_excluded_account_goes_everywhere_but_its_anonymous_posts_stay(db):
    assert left(db, exclude_users=[5]) == {2, 3, 6}
    # 1 (its post), 4 (co-authored) and 5 (its forward in another guild) are gone; 2 stays; a pending invitation counts for nothing


def test_an_excluded_guild_goes_whoever_wrote_there(db):
    assert left(db, exclude_guilds=[11]) == {1, 2, 4, 5, 6}


def test_both_lists_work_together(db):
    assert left(db, exclude_users=[5], exclude_guilds=[11]) == {2, 6}


def test_a_list_for_an_account_that_wrote_nothing_changes_nothing(db):
    assert left(db, exclude_users=[999], exclude_guilds=[999]) == {1, 2, 3, 4, 5, 6}


def test_what_an_outside_feed_server_named_loses_the_same_posts_and_keeps_its_order(db):
    spec = fa.clean({"exclude_users": [5], "exclude_guilds": [14]})
    assert cf.drop_left_out(db, [6, 5, 3, 2, 1], spec) == [3, 2]
    assert cf.drop_left_out(db, [6, 5, 3], fa.DEFAULT) == [6, 5, 3]          # no list: nothing is looked up
    assert cf.drop_left_out(db, [], spec) == []


def test_every_spec_key_the_query_reads_is_one_the_cleaner_builds():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "ruqqus" / "helpers" / "curation_feed.py").read_text(encoding="utf-8")
    for key in re.findall(r'spec\["(\w+)"\]', source):
        assert key in fa.DEFAULT, key


# --- the layers agree (source checks) -----------------------------------------------------------------

def _read(*parts):
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.joinpath(*parts).read_text(encoding="utf-8")


ROUTES = _read("ruqqus", "routes", "curations.py")
FEED = _read("ruqqus", "helpers", "curation_feed.py")
FORM = _read("ruqqus", "templates", "curations", "algorithm_form.html")


def _body(source, name, nxt="\n\n\n"):
    return source.split(f"def {name}(")[1].split(nxt)[0]


def test_typed_names_become_ids_the_way_a_search_finds_them():
    ids = _body(ROUTES, "_left_out_ids")
    assert "search_filters.find_guild(name, v)" in ids and "_account_to_leave_out(name, v)" in ids
    assert "No account called @" in ids and "No guild called +" in ids          # one notice for everything the owner may not see
    assert "search_filters.find_account(name, v)" in _body(ROUTES, "_account_to_leave_out")
    posted = _body(ROUTES, "_posted_algorithm", "\n@app.route(")
    assert "_Resolved(request.form, **_left_out_ids(request.form, g.v))" in posted
    # from_form is the only reader of the form, and it gets ids, never the names that were typed
    assert posted.index("_left_out_ids(") < posted.index("feed_algorithm.from_form(") or "from_form(_Resolved(" in posted


def test_the_query_the_server_path_and_the_preview_all_apply_the_list():
    assert "posts = leave_out(posts, spec)" in _body(FEED, "apply", "\n\n\n")
    assert "muting.exclude_authors(posts, spec[\"exclude_users\"], Submission)" in _body(FEED, "leave_out")
    assert "curation_feed.drop_left_out(g.db, ordered, spec)" in _body(ROUTES, "_server_ids")
    assert "curation_feed.drop_left_out(g.db, ids, spec)" in ROUTES.split("def curation_preview_algorithm(")[1].split("\n@app.route")[0]
    # who may see what is still decided first, for every curation
    ids = _body(ROUTES, "_curation_ids", "\n@cache.memoize")
    assert ids.index("posts = _viewable(posts, v)") < ids.index("posts = curation_feed.apply(posts, spec, now)")


def test_the_owners_form_shows_names_and_everyone_else_only_a_count():
    assert 'name="exclude_users"' in FORM and 'name="exclude_guilds"' in FORM
    assert "left_out.users | join(', ')" in FORM and "left_out.guilds | join(', ')" in FORM
    assert "left_out=_left_out_names(curation.algorithm_spec)" in _body(ROUTES, "curation_edit_get", "\n\n\n@app.route")
    # the public page shows describe(), which only counts
    assert 'f"{accounts} account' in _read("ruqqus", "helpers", "feed_algorithm.py")
    assert "A fork gets the rules but not this list." in FORM


def test_the_list_is_the_curations_own_and_never_the_owners_mute_list():
    for source in (FEED, ROUTES):
        assert "usermutes" not in source and "UserMute" not in source
    assert "muted_ids(" not in _body(FEED, "leave_out")
    assert "exclude_authors(" in _read("ruqqus", "helpers", "muting.py")


def test_claude_md_describes_the_leave_out_list():
    claude = _read("CLAUDE.md")
    section = claude.split("## Curation algorithms (legacy app)")[1].split("## Private guilds")[0]
    for part in ("exclude_users", "exclude_guilds", "leave_out", "counts only", "fork"):
        assert part in section, part
