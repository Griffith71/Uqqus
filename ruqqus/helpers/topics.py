"""Topics: what `#name` means. A topic is one of the site's categories (Arts, Gaming, News...) or subcategories (PC,
Film & TV, Q&A...), which are groups of guilds. `#gaming` names the topic only when one exists, so an ordinary hashtag
stays plain text. Pure: `build` works on any objects shaped like the category classes, and the site's own list
(`classes/categories.CATEGORIES`, loaded once at start-up without a session) is read the first time a topic is asked for,
so the markdown tests need no database.

A topic's name is its `slug`: lowercase, letters and digits only (`Film & TV` is `filmtv`, `Q&A` is `qa`). Every category or
subcategory with the same slug is one topic (`#news` is every News topic, `#filmtv` is Arts and Entertainment), so a name
never has to be told apart.

**Never read `SubCategory.visible` here or anywhere that holds the start-up list**: it reads its category lazily and the
list is detached from its session. `shown` below is the same rule written with the category at hand.
"""
import re
from dataclasses import dataclass

# 108 is left out of every guild list (routes/front.py guild_ids) and 0 is "Uncategorized": neither is a topic
UNLISTED_SUBCAT = 108
EXCLUDED_SUBCATS = (0, UNLISTED_SUBCAT)


def slug(name):
    """A name as `#` writes it: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


@dataclass(frozen=True)
class Topic:
    slug: str
    label: str
    subcat_ids: tuple          # the subcategories whose guilds it holds

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
    labels, ids = {}, {}
    for category in categories:
        if category.is_nsfw or not category.visible:
            continue
        own = [x.id for x in shown(category)]
        if own:
            key = slug(category.name)
            labels.setdefault(key, category.name)
            ids.setdefault(key, []).extend(own)
    for category in categories:
        if category.is_nsfw or not category.visible:
            continue
        for sub in shown(category):
            key = slug(sub.name)
            labels.setdefault(key, sub.name)
            ids.setdefault(key, []).append(sub.id)
    return {key: Topic(key, labels[key], tuple(sorted(set(found)))) for key, found in ids.items() if key}


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


def matching(term):
    """Topics whose name contains the term, the exact one first (the search box)."""
    key = slug(str(term or "").lstrip("#"))
    found = [t for t in all_topics() if key in t.slug]
    return sorted(found, key=lambda t: (t.slug != key, t.label.lower()))
