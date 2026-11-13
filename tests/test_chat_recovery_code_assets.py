"""The recovery code's "kept" time (the chat plan's phase 3): the migration and schema agree, Settings > Chat is linked in both navs, the
reminder's words are marked, and the code never goes to the site (Copy and Download are made in the browser)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ruqqus" / "assets" / "chat_src" / "src"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_the_column_is_migrated_and_mirrored():
    migration = read("scripts", "migrations", "2026-12-23_chat_recovery_code.sql")
    assert "ADD COLUMN IF NOT EXISTS code_saved_utc integer DEFAULT 0 NOT NULL" in migration
    schema = read("schema.sql")
    assert "    code_saved_utc integer DEFAULT 0 NOT NULL\n);" in schema and "('2026-12-23_chat_recovery_code.sql')" in schema


def test_settings_chat_is_in_both_navs():
    nav = read("ruqqus", "templates", "settings.html")
    assert nav.count('href="/settings/chat"') == 2
    assert "from .chat_settings_pages import *" in read("ruqqus", "routes", "__init__.py")


def test_the_code_is_copied_and_downloaded_in_the_browser_only():
    keylock = (SRC / "keylock.js").read_text(encoding="utf-8")
    box = keylock[keylock.index("// Copy and Download, made here in the browser"):keylock.index("let input = null;")]
    assert "navigator.clipboard.writeText(code)" in box and "new Blob(" in box
    assert "fetch(" not in box and "sitePost(" not in box and "post(" not in box


def test_the_reminder_is_marked_and_hidden_only_for_the_tab():
    index = (SRC / "index.js").read_text(encoding="utf-8")
    reminder = index[index.index("async function codeReminder()"):index.index("function keysWaitLine(text)")]
    for words in ("Have you kept your chat recovery code? Without it, a forgotten password can lose your old messages.",
                  "Make a new code", "I have it", "Later"):
        assert f'window.tr("{words}")' in reminder
    assert "sessionStorage.setItem(CODE_LATER" in reminder and "localStorage" not in reminder
