"""Run the real markdown pipeline (preprocess -> CustomRenderer -> sanitize)
without the app or a database.

ruqqus/helpers/get.py imports every model and needs a database session, but
markdown.py and sanitize.py only use a handful of its names (user / guild /
curation lookups for @mentions, +guilds and &curations, and get_domain for
image hosts). Those are stubbed here: no user, guild or curation exists, so
mentions render as plain text, and only the hosts passed in count as image
hosts we embed.
"""
import importlib
import sys
import types
from types import SimpleNamespace as NS


def load(monkeypatch, image_hosts=("i.imgur.com",)):
    stub = types.ModuleType("ruqqus.helpers.get")
    stub.get_domain = lambda netloc: NS(show_thumbnail=True) if netloc in image_hosts else None
    stub.get_user = lambda *a, **k: None
    stub.get_guild = lambda *a, **k: None
    stub.get_curation = lambda *a, **k: None
    stub.app = NS(config={"RUQQUSPATH": "."})
    monkeypatch.setitem(sys.modules, "ruqqus.helpers.get", stub)
    for name in ("ruqqus.helpers.markdown", "ruqqus.helpers.sanitize"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    markdown = importlib.import_module("ruqqus.helpers.markdown")
    sanitize = importlib.import_module("ruqqus.helpers.sanitize")
    return markdown, sanitize


def render(monkeypatch, text, **load_kwargs):
    """Markdown text -> the sanitized HTML that is stored as body_html."""
    import mistletoe

    markdown, sanitize = load(monkeypatch, **load_kwargs)
    with markdown.CustomRenderer() as renderer:
        html = renderer.render(mistletoe.Document(markdown.preprocess(text)))
    return sanitize.sanitize(html, linkgen=True)
