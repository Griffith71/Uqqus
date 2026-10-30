"""What a page needs to paint right the first time.

- `theme_choice()`: the theme the person saved ("dark" or "light"), or None when they never chose. The
  templates then let the device decide (`partials/theme.html`), so the right stylesheet is applied at the
  first paint and nothing is switched after load. Visitors keep a choice in the session too.
- `asset_url(path)`: the address of a file under ruqqus/assets with `?v=` set to a hash of its content.
  `routes/static.py` caches versioned addresses for a year, so an unchanged file is never fetched again and a
  changed one is fetched at once. A stylesheet that is compiled from SCSS is hashed from its .scss file and every
  shared partial it can import (`assets/style/_*.scss`), so a change to a partial alone still changes the address.
- `style_sources(name)` / `style_stamp(name)`: the files one compiled stylesheet is made of and their newest mtime,
  which `routes/static.py` and `routes/boards.py` key their compiled copies on.
"""
import hashlib
import os
from pathlib import Path

from flask import session

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_hashes = {}   # path -> (mtime, hash), per worker


def theme_choice():
    if "dark_mode_enabled" not in session:
        return None
    return "dark" if session.get("dark_mode_enabled") else "light"


def _source(path):
    file = ASSETS / path
    if not file.exists() and file.suffix == ".css":
        file = file.with_suffix(".scss")
    return file


def style_sources(name):
    """The files the stylesheet `name` ("main" or "main_dark") is compiled from: the sheet, then the partials, then
    the module that mirrors it for right-to-left languages (helpers/css_flip.py: its *.rtl.css copies share the
    version, so a change to the flipper changes the address too)."""
    return [ASSETS / "style" / f"{name}.scss", *sorted((ASSETS / "style").glob("_*.scss")),
            Path(__file__).resolve().parent / "css_flip.py"]


def _stamp(files):
    return tuple((f.name, os.stat(f).st_mtime) for f in files)


def style_stamp(name):
    """What a compiled copy of `name` is keyed on: each source's name and mtime (a partial added or removed counts)."""
    return _stamp(style_sources(name))


def asset_version(path):
    """The first 10 hex digits of the sha256 of the file (and, for a stylesheet compiled from SCSS, of its shared
    partials), recomputed only when one of them changes; "0" if missing."""
    file = _source(path)
    files = style_sources(file.stem) if file.suffix == ".scss" else [file]
    try:
        stamp = _stamp(files)
    except OSError:
        return "0"
    cached = _hashes.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    sha = hashlib.sha256()
    for f in files:
        sha.update(f.read_bytes())
    digest = sha.hexdigest()[:10]
    _hashes[path] = (stamp, digest)
    return digest


def asset_url(path):
    path = path.lstrip("/")
    if path.startswith("assets/"):
        path = path[len("assets/"):]
    return f"/assets/{path}?v={asset_version(path)}"


def script_font_links(lang):
    """The stylesheets of the Noto families the interface language `lang` is drawn in (helpers/script_fonts.py)."""
    from ruqqus.helpers import script_fonts
    return [(key, asset_url(script_fonts.FAMILIES[key][1])) for key in script_fonts.interface_keys(lang)]


def script_font_sheets():
    from ruqqus.helpers import script_fonts
    return script_fonts.sheets(asset_url)


def register(app):
    app.jinja_env.globals.update(theme_choice=theme_choice, asset_url=asset_url, asset_version=asset_version,
                                 script_font_links=script_font_links, script_font_sheets=script_font_sheets)
