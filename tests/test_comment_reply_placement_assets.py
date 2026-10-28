"""A comment on a comment appears where a refreshed page draws it: first in its parent's replies, in the gutter beside the
line, with the form left in place for the next one. It used to replace the reply form's slot, above the gutter, so it
looked like a comment on the post (and the form was gone) until the page was reloaded."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
ALL_JS = (ROOT / "assets" / "js" / "all_js.js").read_text(encoding="utf-8")
POST_COMMENT = ALL_JS[ALL_JS.index("post_comment=function(fullname){"):ALL_JS.index("herald_comment=function(")]
SUCCESS = POST_COMMENT[POST_COMMENT.index("if (xhr.status==200) {"):POST_COMMENT.index("    else {\n      var message")]
ROUTE = (ROOT / "routes" / "comments.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "comments.html").read_text(encoding="utf-8")


def test_a_comment_on_a_comment_goes_into_its_parents_replies_first():
    assert "fullname.indexOf('t3_')===0 ? document.getElementById('comments-of-'+parentId) : null" in SUCCESS
    assert "children.querySelector(':scope > .comment-collapse')" in SUCCESS
    assert "children.insertBefore(fragment.content, bar ? bar.nextSibling : children.firstChild)" in SUCCESS


def test_the_page_toasts_in_the_answer_are_not_added_a_second_time():
    assert "['toast-comment-success', 'toast-comment-error'].forEach" in SUCCESS
    assert "fragment.content.getElementById(id)" in SUCCESS and "toast.remove()" in SUCCESS
    assert "comment-error-text" not in SUCCESS                                # it lives inside the error toast


def test_the_form_is_emptied_and_closed_not_replaced_so_the_next_comment_can_be_written():
    inside = SUCCESS[SUCCESS.index("if (children) {"):SUCCESS.index("} else {")]
    assert "box.value=''" in inside and "file-upload-comment-" in inside
    assert "document.getElementById('save-comment-on-'+fullname).classList.remove('disabled')" in inside
    assert "document.getElementById('comment-on-'+parentId).classList.add('d-none')" in inside
    assert "innerHTML=html" not in inside.replace("fragment.innerHTML=html", "")      # the form's slot is left alone


def test_a_comment_on_the_post_or_a_page_without_the_container_keeps_the_old_placement():
    fallback = SUCCESS[SUCCESS.index("} else {"):]
    assert "var formSpace=document.getElementById('comment-form-space-'+fullname);" in fallback
    assert "formSpace.innerHTML=html;" in fallback
    assert "$('#toast-comment-success').toast('show');" in SUCCESS             # the confirmation is the same either way


def test_the_new_comment_is_drawn_at_its_depth_with_its_own_container_for_the_next_answer():
    reply = ROUTE[ROUTE.index('return {"html": lambda: jsonify({"html": render_template("comments.html",'):]
    reply = reply[:reply.index('"api": lambda: c.json')]
    assert "render_child_comments=True," in reply and "start_level=level," in reply and "render_child_comments=False" not in reply
    assert "{{ single_comment(comment, level=(start_level if start_level is defined else 1)) }}" in TEMPLATE
    assert "{{ single_comment(comment) }}" not in TEMPLATE                      # every page keeps drawing from level 1
