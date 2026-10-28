"""The places that filter, tally or name guilds by category, with a guild that is filed under a category alone (boards.cat_id,
subcat_id 0) among them (helpers/category_filter.py is tested in test_category_filter.py).

The real functions are compiled out of their files and run on SQLite with small stand-in models, as test_category_limits.py
does for the curation route: the modules themselves import the whole app (database models, gevent), which the suite never
does."""
import ast
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import Boolean, Column, Integer, String, create_engine, func, select, text
from sqlalchemy.orm import Session, declarative_base

from ruqqus.helpers import category_filter, search_comments, topics
from ruqqus.helpers import post_feedback

RUQQUS = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return RUQQUS.joinpath(*parts).read_text(encoding="utf-8")


def _function(parts, name, **names):
    """`name` from a source file under ruqqus/, compiled alone (decorators dropped) in a namespace holding `names`."""
    path = RUQQUS.joinpath(*parts)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(path), "exec"), names)
    return names[name]


def sub(id_, name, visible=None):
    return NS(id=id_, name=name, _visible=visible, category=None)


def cat(id_, name, subs, visible=True, nsfw=False):
    category = NS(id=id_, name=name, visible=visible, is_nsfw=nsfw, subcats=list(subs))
    for each in subs:
        each.category = category
    return category


ARTS = cat(1, "Arts", [sub(8, "Film & TV"), sub(10, "Music")])
NEWS_CATEGORY = cat(11, "News", [sub(45, "Local"), sub(47, "World")])
ENTERTAINMENT = cat(5, "Entertainment", [sub(23, "News"), sub(24, "Film & TV")])
MEMES = cat(10, "Memes", [sub(44, "Offensive"), sub(50, "Dank")])
POLITICS = cat(12, "Politics", [sub(108, "Identity Politics"), sub(60, "Debate")])
FOOD = cat(18, "Food", [sub(120, "Baking"), sub(121, "Cooking")])
EMPTY = cat(20, "Empty", [])
SECRET = cat(30, "Secret", [sub(90, "Quiet")], visible=False)
CATEGORIES = [ARTS, NEWS_CATEGORY, ENTERTAINMENT, MEMES, POLITICS, FOOD, EMPTY, SECRET]

# every choice the filters take: each subcategory, Uncategorized and -N for each category with subcategories
CHOICES = ({x.id for c in CATEGORIES for x in c.subcats} | {0}) | {-c.id for c in CATEGORIES if c.subcats}


@pytest.fixture(autouse=True)
def site_categories():
    topics.set_categories(CATEGORIES)
    yield
    topics.set_categories(None)


Base = declarative_base()


class Board(Base):
    __tablename__ = "boards"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    subcat_id = Column(Integer)
    cat_id = Column(Integer)
    is_banned = Column(Boolean, default=False)
    is_circle = Column(Boolean, default=False)
    stored_subscriber_count = Column(Integer, default=0)


class Submission(Base):
    __tablename__ = "submissions"
    id = Column(Integer, primary_key=True)
    board_id = Column(Integer)


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    board_id = Column(Integer)
    is_active = Column(Boolean, default=True)


class Follow(Base):
    __tablename__ = "follows"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    target_id = Column(Integer)


class Vote(Base):
    __tablename__ = "votes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    submission_id = Column(Integer)
    vote_type = Column(Integer)
    created_utc = Column(Integer, default=0)


class BoardBlock(Base):
    __tablename__ = "boardblocks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    board_id = Column(Integer)


# board id: (subcat_id, cat_id)
GUILDS = {
    1: (8, None),        # Arts, Film & TV
    2: (0, 1),           # Arts alone
    3: (0, None),        # Uncategorized
    4: (120, None),      # Food, Baking
    5: (0, 18),          # Food alone
    6: (108, None),      # Politics, Identity Politics: left out of every guild list
    7: (44, None),       # Memes, Offensive
    8: (0, 10),          # Memes alone
    9: (45, None),       # News (the category), Local
}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.execute(text("CREATE TABLE post_feedback (id integer PRIMARY KEY AUTOINCREMENT, user_id integer NOT NULL, "
                         "post_id integer NOT NULL, board_id integer, author_id integer, created_utc integer DEFAULT 0)"))
    for bid, (subcat, whole) in GUILDS.items():
        session.add(Board(id=bid, name=f"g{bid}", subcat_id=subcat, cat_id=whole, stored_subscriber_count=100 - bid))
        session.add(Submission(id=bid, board_id=bid))
    session.commit()
    yield session
    session.close()


# --- Discover guilds (routes/front.py guild_ids) ---------------------------------------------------------------------

@pytest.fixture
def guild_ids(db):
    return _function(("routes", "front.py"), "guild_ids", g=NS(db=db), Board=Board, func=func, category_filter=category_filter,
                     filter_boards=lambda query, v, level: query, topic_rules=topics, PROFILE_BOARD_NAME="systemprofile",
                     ALL_CATEGORY_CHOICES=frozenset(CHOICES), abort=lambda status: pytest.fail(f"abort({status})"))


def test_discover_guilds_lists_every_guild_but_the_unlisted_subcategory(guild_ids):
    assert guild_ids(cats=None) == [1, 2, 3, 4, 5, 7, 8, 9]


def test_a_category_token_in_a_typed_list_is_only_the_guilds_filed_under_it_alone(guild_ids):
    assert guild_ids(cats=[-1]) == [2]
    assert guild_ids(cats=[-18]) == [5]
    assert guild_ids(cats=[-10]) == [8]                    # not the Offensive guild, which needs its own row ticked
    assert guild_ids(cats=[-12]) == []
    assert guild_ids(cats=[-1, 120]) == [2, 4]


def test_the_whole_category_is_its_token_and_all_of_its_rows(guild_ids):
    assert guild_ids(cats=[-1, 8, 10]) == [1, 2]
    assert guild_ids(cats=[-18, 120, 121]) == [4, 5]
    # Memes: the Offensive row is part of it, as in the Categorical list; Politics' unlisted row stays out of every list
    assert guild_ids(cats=[-10, 44, 50]) == [7, 8]
    assert guild_ids(cats=[-12, 108, 60]) == []


def test_rows_ticked_without_their_heading_leave_the_guilds_filed_under_the_category_alone_out(guild_ids):
    assert guild_ids(cats=[8, 10]) == [1] and guild_ids(cats=[120, 121]) == [4]


def test_uncategorized_is_the_guilds_with_neither_a_subcategory_nor_a_category(guild_ids):
    assert guild_ids(cats=[0]) == [3]


def test_a_category_that_does_not_exist_matches_nothing_rather_than_everything(guild_ids):
    assert guild_ids(cats=[-999]) == [] and guild_ids(cats=[-20]) == []         # 20 has no subcategory to hold a guild
    assert guild_ids(cats=[999]) == []


def test_a_topics_own_ids_are_applied_as_they_are(guild_ids):
    # #food is Baking, Cooking and the guilds filed under Food alone; #news holds the category and the subcategory
    assert topics.find("food").ids == (120, 121, -18)
    assert guild_ids(cats=list(topics.find("food").ids)) == [4, 5]
    news = topics.find("news")
    assert news.ids == (23, 45, 47, -11)
    assert guild_ids(cats=list(news.ids)) == [9]
    # a topic's -N is the category alone, as a typed one is: its own rows are what it lists
    assert guild_ids(cats=[-10]) == [8] and guild_ids(cats=[-10, 44]) == [7, 8]


def test_the_page_passes_a_topics_ids_as_they_are_and_reads_a_typed_list_as_choices():
    front = read("routes", "front.py")
    browse = front.split("def browse_guilds(", 1)[1].split("\n@app", 1)[0]
    assert "cats = list(topic.ids)" in browse and "expand" not in browse
    assert 'cats = _category_choices(request.args.get("cats", ""))' in browse
    assert "def guild_ids(sort=\"subs\", page=1, cats=[], filter_level=1):" in front
    assert "expand" not in front.split("def guild_ids(", 1)[1].split("\n@app", 1)[0]


# --- a curation's own category filter (routes/curations.py) -----------------------------------------------------------

def _curation_posts(db, category_ids):
    own_filters = _function(("routes", "curations.py"), "_own_filters", select=select, Board=Board, Submission=Submission,
                            category_filter=category_filter, ALL_CATEGORY_CHOICES=frozenset(CHOICES),
                            region_filter_condition=None, language_condition=None)
    curation = NS(region_filter_list=[], language_filter_list=[], category_filter_list=category_ids)
    return sorted(x[0] for x in own_filters(db.query(Submission.id), curation).all())


def test_a_curations_whole_category_is_its_token_and_its_rows_and_the_token_alone_is_the_category_alone(db):
    assert _curation_posts(db, []) == list(GUILDS)                       # no filter: every guild
    assert _curation_posts(db, [-1]) == [2]
    assert _curation_posts(db, [-1, 8, 10]) == [1, 2]
    assert _curation_posts(db, [-1, 120, 0]) == [2, 3, 4]                # a row beside a category, and Uncategorized
    assert _curation_posts(db, [8]) == [1]                               # the row alone leaves the category-alone guild out


def test_a_curation_filter_naming_a_category_that_is_gone_shows_nothing_for_it(db):
    assert _curation_posts(db, [-999]) == []
    assert _curation_posts(db, [-999, 120]) == [4]


# --- For You and the suggestions tally categories (classes/user.py interest_subcats) ---------------------------------

NOW = int(time.time())
ME, FRIEND = 1, 2


def interests(db, user_id=ME):
    tally = _function(("classes", "user.py"), "interest_subcats", g=NS(db=db), select=select, Board=Board,
                      BoardBlock=BoardBlock, Subscription=Subscription, Follow=Follow, Vote=Vote, Submission=Submission,
                      category_filter=category_filter, post_feedback=post_feedback)
    return tally(NS(id=user_id))


def join(db, user, board):
    db.add(Subscription(user_id=user, board_id=board, is_active=True))
    db.commit()


def test_a_joined_guild_filed_under_a_category_alone_counts_for_that_category_not_for_uncategorized(db):
    join(db, ME, 2)
    assert interests(db) == [-1]


def test_joins_follows_and_votes_keep_their_weights_for_subcategories_and_categories_alike(db):
    join(db, ME, 2)                                      # Arts alone: 3 for -1
    db.add(Follow(user_id=ME, target_id=FRIEND))
    join(db, FRIEND, 4)                                  # a followed account's guild, Baking: 2
    join(db, FRIEND, 5)                                  # and Food alone: 2 for -18
    db.add(Vote(user_id=ME, submission_id=1, vote_type=1, created_utc=NOW))      # an upvote in Film & TV: 1
    db.add(Vote(user_id=ME, submission_id=3, vote_type=1, created_utc=NOW))      # one in an Uncategorized guild: 1
    db.add(Vote(user_id=ME, submission_id=7, vote_type=-1, created_utc=NOW))     # a downvote teaches nothing
    db.commit()
    ranked = interests(db)
    assert ranked[0] == -1 and set(ranked[1:3]) == {120, -18} and set(ranked[3:]) == {8, 0}
    assert len(ranked) == 5


def test_a_guild_the_member_muted_teaches_nothing_whatever_its_category(db):
    join(db, ME, 2)
    db.add(BoardBlock(user_id=ME, board_id=2))
    db.commit()
    assert interests(db) == []


def test_show_fewer_lowers_the_category_alone_token_of_a_guild_filed_under_it_alone(db):
    join(db, ME, 2)                                      # 3 for -1
    db.execute(text("INSERT INTO post_feedback (user_id, post_id, board_id, created_utc) VALUES (:u, 300, 2, :t)"),
               {"u": ME, "t": NOW})
    db.commit()
    assert interests(db) == [-1]                         # 3 - 2 = 1, still positive
    db.execute(text("INSERT INTO post_feedback (user_id, post_id, board_id, created_utc) VALUES (:u, 301, 2, :t)"),
               {"u": ME, "t": NOW})
    db.commit()
    assert interests(db) == []                           # 3 - 4 < 0


def test_the_interest_ids_filter_for_you_and_the_suggestions_as_categories():
    source = read("classes", "user.py")
    for_you = source.split("def for_you_idlist(", 1)[1]
    assert "category_filter.board_clause(subcats, Board.subcat_id, Board.cat_id)" in for_you
    assert "category_filter.board_clause(explicit_categories, Board.subcat_id, Board.cat_id)" in for_you
    assert "Board.subcat_id.in_(" not in source
    idlist = source.split("def idlist(", 1)[1].split("def interest_subcats(", 1)[0]
    assert "category_filter.board_clause(categories, Board.subcat_id, Board.cat_id)" in idlist


# --- a guild's topic (helpers/jinja2.py guild_topic) ------------------------------------------------------------------

guild_topic = _function(("helpers", "jinja2.py"), "guild_topic")


def board(subcat=None, whole=None):
    """A guild as the template sees it: its category (from cat_id) or its subcategory's category."""
    category = next((c for c in CATEGORIES if c.id == whole), None) if whole else None
    if subcat is not None and category is None:
        category = subcat.category
    return NS(subcat=subcat, subcat_id=subcat.id if subcat is not None else 0, cat_id=whole, category=category)


def test_a_subcategory_guild_is_in_the_topic_of_its_subcategory():
    assert guild_topic(board(ARTS.subcats[0])) == topics.find("filmtv")
    assert guild_topic(board(ENTERTAINMENT.subcats[1])) == topics.find("filmtv")
    assert guild_topic(board(ENTERTAINMENT.subcats[0])) == topics.find("news")        # News under Entertainment
    assert guild_topic(board(NEWS_CATEGORY.subcats[0])) == topics.find("local")


def test_a_guild_filed_under_a_category_alone_is_in_that_categorys_topic():
    assert guild_topic(board(whole=1)) == topics.find("arts") and guild_topic(board(whole=1)).url == "/topic/arts"
    # the category News and the subcategory News are one topic, which holds the category
    assert guild_topic(board(whole=11)) == topics.find("news")


def test_a_category_with_no_topic_gives_its_guilds_none():
    assert guild_topic(board(whole=30)) is None             # hidden
    assert guild_topic(board(whole=20)) is None             # nothing in it
    assert guild_topic(board(whole=999)) is None            # gone
    assert guild_topic(NS(subcat=None, subcat_id=0, cat_id=None, category=None)) is None      # Uncategorized
    assert guild_topic(None) is None


def test_a_topic_that_does_not_hold_the_guild_is_not_its_topic():
    stale = NS(subcat=ARTS.subcats[0], subcat_id=999, cat_id=None, category=ARTS)
    assert guild_topic(stale) is None
    # the category-alone link needs the category in the topic: a subcategory of the same name is not it
    assert guild_topic(NS(subcat=None, subcat_id=0, cat_id=24, category=cat(24, "Film & TV", [sub(301, "Cult")]))) is None


# --- search: in:#topic on a comment (helpers/search_comments.py) ------------------------------------------------------

def test_a_comment_search_in_a_topic_finds_the_guilds_filed_under_its_category_alone(db, monkeypatch):
    from dataclasses import replace
    from ruqqus.helpers import search_query as sq

    classes = types.ModuleType("ruqqus.classes")
    for model in (Board, Comment, Submission):
        setattr(classes, model.__name__, model)
    monkeypatch.setitem(sys.modules, "ruqqus.classes", classes)

    def found(text):
        part = sq.parse(text).parts[0]
        part = replace(part, refs=tuple(topics.find(part.value[1:]).ids))
        return sorted(x[0] for x in db.query(Submission.id).filter(search_comments._condition(part, None)).all())

    assert found("in:#food") == [4, 5]
    assert found("-in:#food") == [1, 2, 3, 6, 7, 8, 9]
    assert found("in:#news") == [9]


# --- the definitions in routes/front.py -------------------------------------------------------------------------------

def _front_choices():
    """CATEGORY_TOKENS and ALL_CATEGORY_CHOICES out of front.py, over the stand-in categories (the real ones need the database)."""
    tree = ast.parse(read("routes", "front.py"))
    wanted = [n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id in ("CATEGORY_TOKENS", "ALL_CATEGORY_CHOICES")]
    assert len(wanted) == 2
    names = {"CATEGORIES": CATEGORIES, "ALL_SUBCAT_IDS": {x.id for c in CATEGORIES for x in c.subcats} | {0}}
    exec(compile(ast.fix_missing_locations(ast.Module(body=wanted, type_ignores=[])), "front.py", "exec"), names)
    return names


def test_every_category_with_subcategories_is_a_choice_and_the_others_are_not():
    names = _front_choices()
    assert names["CATEGORY_TOKENS"] == {-1, -11, -5, -10, -12, -18, -30}          # not 20: it holds no subcategory
    assert names["ALL_CATEGORY_CHOICES"] == CHOICES == names["ALL_SUBCAT_IDS"] | names["CATEGORY_TOKENS"]


def test_the_notice_names_the_categories_and_the_session_heals_when_everything_is_ticked():
    front = read("routes", "front.py")
    assert "category_catalog.notice_names(ALL_SUBCATS, subcat_ids, msg, CATEGORIES)" in front
    assert "set(stored_catids) in (ALL_SUBCAT_IDS, ALL_CATEGORY_CHOICES)" in front
    assert "known_categories=ALL_CATEGORY_CHOICES" in front
    assert "category_filter.board_clause(categories, Board.subcat_id, Board.cat_id,\n" in front
    assert "Board.subcat_id.in_(" not in front


def test_no_topic_filter_stands_in_an_id_that_could_name_a_real_category():
    for parts in (("helpers", "search_filters.py"), ("helpers", "search_comments.py"), ("helpers", "feed_filter_sql.py")):
        assert "(-1,)" not in read(*parts), parts
