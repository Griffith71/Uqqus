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


def _author_of(item, v=None):
    return anonymity.author_of(item, _viewer(v))


def _author_label(item, v=None):
    return anonymity.label_of(item, _viewer(v))


def _op_badge(comment, post, v=None):
    return anonymity.op_badge(comment, post, _viewer(v))


app.jinja_env.globals.update(
    anon_hidden=_anon_hidden,
    author_of=_author_of,
    author_label=_author_label,
    op_badge=_op_badge,
)
