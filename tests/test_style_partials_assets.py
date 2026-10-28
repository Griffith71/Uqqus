"""Shared stylesheet partials (`assets/style/_*.scss`, the UI modernisation of 2026-10): both theme sheets import
them, and a change to a partial alone must reach browsers. The sheets' addresses are cached for a year by their
`?v=` hash, so the hash, the compiled copy (routes/static.py) and the guild copies (routes/boards.py) all follow
the partials too."""
import os
import re
import time
from pathlib import Path

from stylesheets import SHEETS, expanded, partials

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_a_partial_alone_changes_the_stylesheet_address(tmp_path, monkeypatch):
    from ruqqus.helpers import page_assets
    style = tmp_path / "style"
    style.mkdir()
    (style / "main.scss").write_text("a { color: red; }")
    partial = style / "_tokens.scss"
    partial.write_text(":root { --x: 1px; }")
    monkeypatch.setattr(page_assets, "ASSETS", tmp_path)
    monkeypatch.setattr(page_assets, "_hashes", {})
    before = page_assets.asset_version("style/main.css")
    partial.write_text(":root { --x: 2px; }")
    later = time.time() + 5
    os.utime(partial, (later, later))
    assert page_assets.asset_version("style/main.css") != before
    # a partial added later counts too
    added = style / "_motion.scss"
    added.write_text(":root { --y: 1ms; }")
    after_add = page_assets.asset_version("style/main.css")
    assert after_add != before
    # ...and the module that mirrors the sheet for right-to-left pages (its .rtl.css copies share the version)
    assert [p.name for p in page_assets.style_sources("main")] == ["main.scss", "_motion.scss", "_tokens.scss",
                                                                    "css_flip.py"]


def test_the_compiled_copies_are_keyed_on_the_partials():
    static = read("routes", "static.py").split("def main_css(")[1].split("@app.route")[0]
    assert "page_assets.style_stamp(file)" in static and "css[0] != stamp" in static
    # the last good copy is never kept for a year under a new address
    assert '"no-store" if stale else "public"' in static
    main = read("__main__.py")
    assert '"no-store" not in response.headers.get("Cache-Control", "")' in main
    boards = read("routes", "boards.py")
    assert "_compiled_board_css(colour, sheet, asset_version(f\"style/{sheet}.css\"), rtl)" in boards
    assert "@cache.memoize()\ndef _compiled_board_css(colour, sheet, style_version, rtl=False):" in boards
    assert "cache.delete_memoized(board_css" not in boards
    assert "cache.delete_memoized(_compiled_board_css)" in main


def test_partials_never_carry_the_placeholders_the_server_fills_in():
    # the server replaces {boardcolor} and the others in the sheet's own text only, never in what it imports
    for name, text in partials().items():
        for placeholder in ("{boardcolor}", "{maincolor}", "{ruqquspath}"):
            assert placeholder not in text, (name, placeholder)


def test_every_partial_is_imported_by_both_sheets():
    for name in partials():
        stem = name[:-len(".scss")]
        for sheet in SHEETS:
            text = (ROOT / "assets" / "style" / sheet).read_text(encoding="utf-8")
            assert f'@import "{{ruqquspath}}/assets/style/{stem}";' in text, (sheet, name)


def test_the_motion_rules_hold_in_the_partials_too():
    for name, text in partials().items():
        assert not re.search(r"transition:\s*all", text), name
        for duration in re.findall(r"(\d+)ms", text):
            assert int(duration) <= 300, (name, duration)
    for sheet in SHEETS:
        assert not re.search(r"transition:\s*all", expanded(sheet)), sheet
