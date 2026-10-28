"""A guild's category as a filter names it. A guild is filed under a subcategory (`boards.subcat_id`) or, when its
guildmaster chose only the broad category, under that category alone: `subcat_id` is then 0 and `boards.cat_id` holds the
category. Every list of categories (the picker's values, the session, saved defaults, a curation's filter, `/categorical`,
a topic's ids) is one list of integers: a positive id is a subcategory, 0 is Uncategorized (no subcategory and no
category) and `-N` is only the guilds filed under category N alone. It does not stand for N's subcategories: the whole
category is `-N` and all of its subcategory ids, which the Categorical list ticks for the person when they tick the heading.

Pure: this imports SQLAlchemy only, never classes/categories (which reads the database when imported), so the tests run
it on SQLite stand-ins. The columns come from the caller, because some queries use the model's and
feed_filter_sql.py a hand-written table.

**Every condition here is true or false, never NULL**: a NULL `subcat_id` or `cat_id` just fails the terms that test it,
so a caller may negate the clause without those guilds dropping out of both the filter and its opposite.
"""
import re

from sqlalchemy import and_, false, or_

_TOKEN = re.compile(r"-?[0-9]{1,9}")


def parse_token(text):
    """The id a typed or posted category means, or None: ASCII digits (1 to 9 of them) with an optional minus in front.
    `-0`, `--5`, `-`, an empty or padded text and other scripts' digits ("²".isdigit() is True) are refused."""
    if not isinstance(text, str) or not _TOKEN.fullmatch(text):
        return None
    value = int(text)
    return None if value == 0 and text.startswith("-") else value


def _numbers(ids):
    for item in ids:
        try:
            number = int(item)
        except (TypeError, ValueError, OverflowError):
            continue
        yield number


def board_clause(ids, subcat_col, cat_col, known=None):
    """The condition for guilds in any of these ids. An id that is not a whole number is ignored, and a list with
    nothing left matches nothing (never a stand-in id: category 1 is Arts). `known` is the caller's valid set: a `-N` that
    is not in it is ignored.

    `-N` is only the guilds filed under category N alone, never N's subcategories: a filter for the whole category lists
    them as well."""
    subs, cats, uncategorized = set(), set(), False
    for number in _numbers(ids):
        if number > 0:
            subs.add(number)
        elif number == 0:
            uncategorized = True
        elif known is None or number in known:
            cats.add(-number)
    terms = []
    if uncategorized:
        terms.append(and_(subcat_col.is_not(None), subcat_col == 0, cat_col.is_(None)))
    if subs:
        terms.append(and_(subcat_col.is_not(None), subcat_col.in_(sorted(subs))))
    if cats:
        terms.append(and_(cat_col.is_not(None), cat_col.in_(sorted(cats))))
    return or_(*terms) if terms else false()


def token_of(subcat_id, cat_id):
    """The id a guild is counted under: `-N` for a guild filed under category N alone, else its subcategory."""
    return -cat_id if cat_id else subcat_id


def choice_of(token, categories):
    """(category, subcategory) a guild may be filed under for this id, the subcategory None for a whole category, or
    None when there is no such choice: a positive id must be a subcategory of some category, a negative one a category
    that has subcategories (the Categorical list's headings)."""
    if not isinstance(token, int) or isinstance(token, bool):
        return None
    for category in categories:
        if token < 0 and category.id == -token and category.subcats:
            return category, None
        if token > 0:
            for sub in category.subcats:
                if sub.id == token:
                    return category, sub
    return None


def valid_choice(token, categories):
    """Whether `choice_of` finds the id among these categories (the site's, `classes/categories.CATEGORIES`)."""
    return choice_of(token, categories) is not None


def file_under(board, token):
    """File a guild under a checked id (`valid_choice`): a category alone has subcat_id 0 and cat_id N, a subcategory
    has cat_id NULL, so the two columns never disagree. Nothing is saved."""
    board.subcat_id = 0 if token < 0 else token
    board.cat_id = -token if token < 0 else None
