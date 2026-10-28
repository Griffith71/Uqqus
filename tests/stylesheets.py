"""The two theme stylesheets as libsass sees them: each sheet with the shared partials it imports
(`assets/style/_*.scss`, e.g. `_tokens.scss`) written in where they are imported. A rule moved out of the sheets into
a partial is still found by a test that reads `expanded(...)`, and the bans (`transition: all`, durations) cover it.

    from stylesheets import expanded, SHEETS, partials
"""
import re
from pathlib import Path

STYLE = Path(__file__).resolve().parent.parent / "ruqqus" / "assets" / "style"
SHEETS = ("main.scss", "main_dark.scss")
_IMPORT = re.compile(r'@import "\{ruqquspath\}/assets/style/(_[\w-]+)(?:\.scss)?";')


def partials():
    """{file name: text} of every shared partial."""
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(STYLE.glob("_*.scss"))}


def expanded(name):
    """The sheet `name` with each partial it imports written in at its import."""
    text = (STYLE / name).read_text(encoding="utf-8")
    return _IMPORT.sub(lambda m: (STYLE / f"{m.group(1)}.scss").read_text(encoding="utf-8"), text)


def expanded_all():
    return {name: expanded(name) for name in SHEETS}
