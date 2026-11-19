"""Makes helpers/anonymity.py available to every template.

Templates ask `author_of(item, v)` for the author to draw, `anon_hidden(item, v)`
whether to hide something that would single the author out, and `op_badge(...)`
for the "Submitter" mark. `v` is the viewer; a few pages (the embeds) have none,
and then nobody is allowed to see an anonymous author."""
from jinja2 import Undefined

from ruqqus.__main__ import app
from ruqqus.helpers import anonymity


def _viewer(v):
    return None if isinstance(v, Undefined) else v


def _anon_hidden(item, v=None):
    return anonymity.identity_hidden(item, _viewer(v))


def _is_author(item, v=None):
    """Is the viewer the item's author, as far as this request may be told (a limited QR sign-in is a stranger to its own anonymous items)."""
    viewer = _viewer(v)
    return viewer is not None and anonymity.is_author(item, viewer)


def _author_of(item, v=None):
    return anonymity.author_of(item, _viewer(v))


def _author_label(item, v=None):
    return anonymity.label_of(item, _viewer(v))


def _op_badge(comment, post, v=None):
    return anonymity.op_badge(comment, post, _viewer(v))


def _alias_resolver(item):
    """The number the item's author has in its thread, from one query a thread a request (the numbers of every person who has one there)."""
    from flask import g, has_request_context
    from ruqqus.helpers import anon_aliases
    if not has_request_context() or getattr(g, "db", None) is None:
        return None
    scope = anon_aliases.scope_of(item)
    author = getattr(item, "author_id", None)
    if scope is None or author is None:
        return None
    return anon_aliases.numbers_cached(g.db, scope).get(author)


anonymity.alias_resolver = _alias_resolver

app.jinja_env.globals.update(
    anon_hidden=_anon_hidden,
    is_author=_is_author,
    author_of=_author_of,
    author_label=_author_label,
    op_badge=_op_badge,
)
