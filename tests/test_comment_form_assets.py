"""A comment the server refuses (the posting cooldown, a ban, a guild's rules) must tell the
member why. The error toast is drawn with the comment list, so on a post that has no
comments yet it does not exist: post_comment in all_js.js used to crash there, show
nothing and leave the button disabled."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
ALL_JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
POST_COMMENT = ALL_JS[ALL_JS.index("post_comment=function(fullname){"):ALL_JS.index("herald_comment=function(")]


def test_the_error_toast_really_is_missing_on_a_post_without_comments():
    # the reason for the fallback: only comments.html has it, and that is included for a list of comments
    owners = [p.name for p in (ROOT / "templates").rglob("*.html") if 'id="comment-error-text"' in p.read_text(encoding="utf-8")]
    assert owners == ["comments.html"]


def test_a_refused_comment_is_explained_with_or_without_the_toast():
    refused = POST_COMMENT[POST_COMMENT.index("else {"):]
    assert "if (commentError) {" in refused and "alert(message);" in refused
    assert refused.index("if (commentError) {") < refused.index("commentError.textContent = message;")
    # an answer that is not JSON (a proxy error page) must not crash it either
    assert "try { message = JSON.parse(xhr.response)" in refused


def test_the_button_comes_back_so_the_member_can_try_again():
    refused = POST_COMMENT[POST_COMMENT.index("else {"):POST_COMMENT.index("xhr.send(form)")]
    assert "classList.remove('disabled')" in refused
