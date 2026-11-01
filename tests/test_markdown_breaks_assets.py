"""A single Enter in a post or a comment is a line break (the owner, 2026-10-30: the writing box has to look like the posted post).

`CustomRenderer(breaks=True)` does it (helpers/markdown.py). It is asked for in exactly the places that render the text of a
post or a comment, and the editor's Preview of it, so that what is written, previewed and posted agree. Everything else the
renderer draws (the wiki, bios, descriptions, live updates, notices, the legal pages) keeps standard markdown, where the
lines of a paragraph are joined. A new place that renders a post's or a comment's text must pass it too."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"

# file -> how many `CustomRenderer(` it has, all of which render a post, a comment or the Preview of one
BREAKS = {
    "routes/posts.py": 4,          # submit, edit, and the two that append a body picture
    "routes/comments.py": 3,       # a new comment, a comment with a picture, an edit
    "routes/post_editor.py": 1,    # /api/preview
}


def renderers(path):
    return re.findall(r"CustomRenderer\(([^)]*)\)", (ROOT / path).read_text(encoding="utf-8"))


def test_every_post_comment_and_preview_renderer_asks_for_line_breaks():
    for path, count in BREAKS.items():
        calls = renderers(path)
        assert len(calls) == count, (path, calls)
        assert all("breaks=True" in call for call in calls), (path, calls)


def test_nothing_else_asks_for_them():
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative in BREAKS or relative == "helpers/markdown.py":
            continue
        assert not any("breaks=True" in call for call in renderers(relative)), relative


def test_the_renderer_only_breaks_when_asked():
    source = (ROOT / "helpers" / "markdown.py").read_text(encoding="utf-8")
    method = source.split("def render_line_break(self, token):", 1)[1].split("\n    def ", 1)[0]
    assert 'getattr(self, "breaks", False)' in method and "<br />" in method
    assert "return HTMLRenderer.render_line_break(token)" in method          # the default stays standard markdown
