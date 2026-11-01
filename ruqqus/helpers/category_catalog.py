"""The Categorical filter's list (sidebar, phone sheet, curation form): the site's categories as headings over their
subcategories, in the reader's language. A subcategory's name used to repeat ("News" sat under Entertainment, Politics, Science
and Technology; the site's names are unique now, 2026-10-31), and the headings are still what a reader scans by, so the rows are
told apart under their parent, and a name that still reads alike in one group (a translation) gets its English one. Pure: `build` works on any
objects shaped like classes/categories.py and is given the translating function, so the tests need no database;
routes/front.py hands it the site's CATEGORIES and `msg`.

Everything here is sorted by the translated name (case-folded, accents set aside): the order of CATEGORIES depends on the
database's collation and a category's subcategories sort case-sensitively (classes/categories.py), and neither is right
once the names are in another language. The names arrive already translated, so a template draws them as they are.

A category's heading is a row of its own: `-N` (helpers/category_filter.py), only the guilds filed under the category
alone. The list ticks every subcategory row with it (all_js.js), so the whole category is the heading and all its rows
together. It is kept out of `options`, which are the subcategories.
"""
import unicodedata
from collections import Counter

from ruqqus.helpers.i18n_runtime import N_, tr

UNCATEGORIZED_ID = 0
# Translators: the category of guilds that have none
UNCATEGORIZED = N_("Uncategorized")

_built = {}


def _fold(text):
    """The sort key of a name: no case, no accents ("Éducation" sits among the E's, as a reader of French expects)."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return plain.casefold(), text.casefold()


def _rows(subcats, translate, parent_label, parent):
    """A group's rows, sorted. Two that still read alike in this language (machine-translated catalogues map different
    names to one string) get the English name in brackets, except the one that already is the English name."""
    named = sorted(((translate(sub.name), sub.name, sub.id) for sub in subcats),
                   key=lambda row: (_fold(row[0]), row[1], row[2]))
    alike = Counter(label.casefold() for label, _name, _id in named)
    return [{"value": id_,
             "label": f"{label} [{name}]" if alike[label.casefold()] > 1 and name.casefold() != label.casefold() else label,
             # typing the English name or the parent's finds the row in every language
             "search": f"{label} {name} {parent_label} {parent}"}
            for label, name, id_ in named]


def build(categories, translate, uncategorized=UNCATEGORIZED):
    """[{"label", "heading": {"value", "label", "search"}, "options": [{"value", "label", "search"}]}], one group per
    category that has subcategories, by name. "Uncategorized" is a lone row first, in a group with no label and no
    heading (`{"label": "", "options": [...]}`): at the end it would read as part of the last group."""
    keyed = []
    for category in categories:
        if category.subcats:
            label = translate(category.name)
            heading = {"value": -category.id, "label": label, "search": f"{label} {category.name}"}
            keyed.append(((_fold(label), category.id),
                          {"label": label, "heading": heading,
                           "options": _rows(category.subcats, translate, label, category.name)}))
    label = translate(uncategorized)
    lone = {"value": UNCATEGORIZED_ID, "label": label, "search": f"{label} {uncategorized}"}
    return [{"label": "", "options": [lone]}] + [group for _key, group in sorted(keyed, key=lambda item: item[0])]


def cached(locale, version, categories, translate):
    """`build`, once per language: the context processor runs for every rendered page and fragment, and the phone sheet
    is in all of them. `version` is whatever changes when the language's catalogue is read again (i18n_runtime.translations
    gives a new object then), so a catalogue finished while the worker runs is used."""
    found = _built.get(locale)
    if found is None or found[0] is not version:
        found = _built[locale] = (version, build(categories, translate))
    return found[1]


def notice_names(rows, ids, translate, categories=()):
    """The names the "Showing posts in" notice lists for these ids, in the reader's language, in the order given. `rows`
    are (name, id, category name), routes/front.py ALL_SUBCATS. A name under more than one category carries its category
    ("News (Technology)"), or the notice would read "News, News, News". A negative id is the guilds filed under a category
    alone (helpers/category_filter.py), named by `categories`, objects with an `id`, a `name` and `subcats`: by its name
    alone when every one of its subcategories is in the list too (the whole category, which then does not name them one
    by one), else "Arts (category only)" followed by the subcategories in the list."""
    parents = {}
    for name, _id, parent in rows:
        parents.setdefault(str(name), set()).add(parent)
    found = {id_: (name, parent) for name, id_, parent in rows}
    wholes = {-category.id: category for category in categories}
    listed = set(ids)
    complete = {token for token, category in wholes.items()
                if token in listed and all(sub.id in listed for sub in category.subcats)}
    inside = {sub.id for token in complete for sub in wholes[token].subcats}
    names = []
    for id_ in ids:
        if id_ in inside:
            continue
        if id_ in found:
            name, parent = found[id_]
            label = str(translate(name))
            names.append(f"{label} ({translate(parent)})" if len(parents[str(name)]) > 1 else label)
        elif id_ in complete:
            names.append(str(translate(wholes[id_].name)))
        elif id_ in wholes:
            # Translators: a category named in the "Showing posts in" notice without its subcategories
            names.append(tr("%(category)s (category only)", category=translate(wholes[id_].name)))
    return names
