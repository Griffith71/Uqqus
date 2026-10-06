"""Fixed options for the formatting extras in posts (colour, highlight,
alignment, button).

Authors never write CSS: the markdown syntax only accepts these names
({c:red}, {h:yellow}, ::: center) and the renderer turns them into CSS
classes, and the sanitizer drops every class that is not on the list below for
its tag. The toolbar (all_js.js, PostEditor) offers the same names, and the
stylesheets (main.scss and main_dark.scss) define one rule per class - a test
keeps the four in step. Free of Flask/database imports so it can be tested and
shared by the renderer and the sanitizer.
"""

TEXT_COLORS = ("red", "orange", "green", "blue", "purple", "pink", "gray")
HIGHLIGHT_COLORS = ("yellow", "green", "blue", "pink")
ALIGNMENTS = ("left", "center", "right")

BUTTON_CLASS = "post-button"


def allowed_classes():
    """Per tag, the only CSS classes the sanitizer keeps."""
    return {
        "span": {"spoiler"} | {f"tc-{name}" for name in TEXT_COLORS},
        "mark": {f"hl-{name}" for name in HIGHLIGHT_COLORS},
        "div": {f"ta-{name}" for name in ALIGNMENTS},
        "a": {BUTTON_CLASS},
    }
