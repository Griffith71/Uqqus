"""The Jinja side of the interface languages that needs no Flask: the extensions and policies the app installs
(helpers/i18n_runtime.install) and the tests and the string extractor (babel.cfg, scripts/i18n_catalogs.py) parse
the templates with, so all three read a template the same way."""

# `do` for {% do list.append(x) %} (never {% set _ = ... %}: `_` is gettext), `i18n` for _(), {% trans %} and friends
EXTENSIONS = ("jinja2.ext.do", "jinja2.ext.i18n")

# {% trans %} blocks are trimmed, so re-indenting a template never changes the string (and loses its translation)
POLICIES = {"ext.i18n.trimmed": True}


def environment(**options):
    """A plain Jinja environment that parses the site's templates (tests)."""
    from jinja2 import Environment
    env = Environment(extensions=list(EXTENSIONS), **options)
    env.policies.update(POLICIES)
    env.newstyle_gettext = True
    return env
