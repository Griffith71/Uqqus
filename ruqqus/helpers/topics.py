"""Topics: what `#name` means. A topic is one of the site's categories (Arts, Gaming, News...) or subcategories (PC,
Film & TV, Q&A...), which are groups of guilds. `#gaming` names the topic only when one exists, so an ordinary hashtag
stays plain text. Pure: `build` works on any objects shaped like the category classes, and the site's own list
(`classes/categories.CATEGORIES`, loaded once at start-up without a session) is read the first time a topic is asked for,
so the markdown tests need no database.

A topic's name is its `slug`: lowercase, letters and digits only (`Film & TV` is `filmtv`, `Q&A` is `qa`). Every category or
subcategory with the same slug is one topic (`#filmtv` is the Film & TV category and its subcategory, `#music` the Music category
and the Arts subcategory), so a name never has to be told apart. Among subcategories every name is its own (the owner, 2026-10-31:
"each sub category must have its own unique name"): the four that were all "News" are Entertainment, Political, Science and Tech News.

**Never read `SubCategory.visible` here or anywhere that holds the start-up list**: it reads its category lazily and the
list is detached from its session. `shown` below is the same rule written with the category at hand.
"""
import re
from dataclasses import dataclass

# 0 is "Uncategorized": not a topic. (Subcategory 108 used to be left out of every guild list and of the topics, as the "unlisted" bucket;
# it is "Social Issues" now, listed like any other: the owner, 2026-10-30.)
EXCLUDED_SUBCATS = (0,)


def slug(name):
    """A name as `#` writes it: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


@dataclass(frozen=True)
class Topic:
    slug: str
    label: str
    subcat_ids: tuple          # the subcategories whose guilds it holds
    cat_ids: tuple = ()        # the categories it is named after: a guild filed under one alone (cat_id) belongs too

    @property
    def ids(self):
        """The topic as a guild filter lists it (helpers/category_filter.py): its subcategories, and `-N` (the guilds filed
        under that category alone) for each category."""
        return self.subcat_ids + tuple(-c for c in self.cat_ids)

    @property
    def url(self):
        return f"/topic/{self.slug}"


def shown(category):
    """The subcategories of a category a visitor may see, by the rule `SubCategory.visible` has but with the category at hand."""
    return [x for x in category.subcats
            if x.id not in EXCLUDED_SUBCATS and (x._visible if x._visible in (True, False) else bool(category.visible))]


def build(categories):
    """{slug: Topic} from categories (visible, not NSFW) and their visible subcategories. A category's own name comes
    first, so it is the label when a subcategory carries the same name."""
    labels, ids, cat_ids = {}, {}, {}
    for category in categories:
        if category.is_nsfw or not category.visible:
            continue
        own = [x.id for x in shown(category)]
        if own:
            key = slug(category.name)
            labels.setdefault(key, category.name)
            ids.setdefault(key, []).extend(own)
            cat_ids.setdefault(key, set()).add(category.id)       # kept when a same-named subcategory merges below
    for category in categories:
        if category.is_nsfw or not category.visible:
            continue
        for sub in shown(category):
            key = slug(sub.name)
            labels.setdefault(key, sub.name)
            ids.setdefault(key, []).append(sub.id)
    return {key: Topic(key, labels[key], tuple(sorted(set(found))), tuple(sorted(cat_ids.get(key, ()))))
            for key, found in ids.items() if key}


_topics = None


def set_categories(categories):
    """Use these categories (the tests' way in); None forgets them, so the site's list is read again."""
    global _topics
    _topics = None if categories is None else build(categories)


def all_topics():
    """Every topic, by label."""
    global _topics
    if _topics is None:
        from ruqqus.classes.categories import CATEGORIES
        _topics = build(CATEGORIES)
    return sorted(_topics.values(), key=lambda t: t.label.lower())


def find(name):
    """The topic `name` writes (with or without its #), or None."""
    all_topics()
    return _topics.get(slug(str(name or "").lstrip("#")))


# Topics that went in the clean-up of 2026-12-08 (scripts/migrations/2026-12-08_taxonomy_cleanup.sql) and the topic that holds their guilds
# now. Only the `/topic/<slug>` address uses it, so a link already written into a post still lands somewhere; a typed `#left` is not a link.
RETIRED = {
    "lgbtq": "modernculture",
    "left": "politicalideasdebate", "right": "politicalideasdebate", "authoritarian": "politicalideasdebate",
    "libertarian": "politicalideasdebate", "centrist": "politicalideasdebate",
    "uspolitics": "governmentpolicy", "elections": "governmentpolicy", "worldpolitics": "internationalaffairs",
    "identitypolitics": "socialissues",
    "combat": "combatsports", "boxingmma": "combatsports",
    "team": "othersports", "individual": "othersports", "partner": "othersports",
    "hardsciences": "generalscience", "naturalsciences": "generalscience", "softsciences": "socialsciences",
    "casual": "generalmemes", "dank": "generalmemes", "interesting": "generalmemes",
    "mechanics": "engineering", "help": "techsupport",
    "local": "news", "northamerica": "news",       # where the news is about is the region filter's job (2026-10-31)
}


# The subcategory ids the same clean-up merged away and the id each went into (the migration's `taxonomy_cleanup_merge`): a session's
# `catids` cookie, a saved link's `?cats=` and anything else that still holds one reads as the successor, so it matches the guilds that
# moved instead of nothing. Stored data (guilds, curations, saved defaults) is moved by the migration itself.
# A negative id is a category (helpers/category_filter.py): Local and North America became "News, filed under the category alone" (-11).
RETIRED_SUBCATS = {50: 49, 51: 49, 52: 49, 176: 49, 171: 174, 173: 174, 194: 67, 90: 91, 93: 91, 100: 98, 42: 41, 165: 41, 96: 97,
                   8: 24, 45: -11, 46: -11}


def current_ids(ids):
    """The ids with every retired subcategory replaced by the one it went into, each once, in order."""
    return list(dict.fromkeys(RETIRED_SUBCATS.get(i, i) for i in ids))


def find_retired(name):
    """The topic that took over from a retired one (`#lgbtq`), or None; never for a name that is a topic today."""
    if find(name) is not None:
        return None
    return find(RETIRED.get(slug(str(name or "").lstrip("#")), ""))


def matching(term):
    """Topics whose name contains the term, the exact one first (the search box)."""
    key = slug(str(term or "").lstrip("#"))
    found = [t for t in all_topics() if key in t.slug]
    return sorted(found, key=lambda t: (t.slug != key, t.label.lower()))
