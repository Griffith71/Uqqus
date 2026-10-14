"""What a page needs to paint right the first time.

- `theme_choice()`: the theme the person saved ("dark" or "light"), or None when they never chose. The
  templates then let the device decide (`partials/theme.html`), so the right stylesheet is applied at the
  first paint and nothing is switched after load. Visitors keep a choice in the session too.
- `asset_url(path)`: the address of a file under ruqqus/assets with `?v=` set to a hash of its content.
  `routes/static.py` caches versioned addresses for a year, so an unchanged file is never fetched again and a
  changed one is fetched at once. A stylesheet that is compiled from SCSS is hashed from its .scss file.
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


def asset_version(path):
    """The first 10 hex digits of the file's sha256, recomputed only when the file changes; "0" if missing."""
    file = _source(path)
    try:
        mtime = os.stat(file).st_mtime
    except OSError:
        return "0"
    cached = _hashes.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
    _hashes[path] = (mtime, digest)
    return digest


def asset_url(path):
    path = path.lstrip("/")
    if path.startswith("assets/"):
        path = path[len("assets/"):]
    return f"/assets/{path}?v={asset_version(path)}"


def register(app):
    app.jinja_env.globals.update(theme_choice=theme_choice, asset_url=asset_url, asset_version=asset_version)
