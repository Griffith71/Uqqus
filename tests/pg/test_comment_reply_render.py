"""The comment the server hands back after a comment on a comment (routes/comments.py api_comment): drawn at its depth, with
the container its own comments will go into while the page still draws one there, so the page can put it under its
parent and the member can answer it at once (assets/js/all_js.js post_comment)."""
import secrets

BASE = "http://localhost"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def post(client, path, data):
    return client.post(path, base_url=BASE, headers={"User-Agent": "ci-reply"}, environ_base={"REMOTE_ADDR": address()}, data=data)


def a_post(member, here):
    answer = post(here, "/api/vue/submit", {"title": f"Title {secrets.token_hex(4)}", "body": "A post", "formkey": member.formkey(here),
                                           "post_to_profile": "1"})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    return answer.get_json()["id"]


def comment_on(member, here, post_b36, parent_fullname):
    answer = post(here, "/api/comment", {"parent_fullname": parent_fullname, "submission": post_b36, "formkey": member.formkey(here),
                                         "body": "Comment " + secrets.token_hex(6)})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    return answer.get_json()["html"]


def the_new_id(html):
    """The id of the comment the answer is about: the first card in it."""
    marker = '<div id="comment-'
    start = html.index(marker) + len(marker)
    return html[start:html.index('"', start)]


def test_a_comment_on_a_comment_comes_back_ready_to_be_answered_until_the_depth_the_page_stops_drawing_replies(make_member, real_app):
    member = make_member()
    here = member.browser()
    post_id = a_post(member, here)
    html = comment_on(member, here, post_id, f"t2_{post_id}")
    parent = the_new_id(html)
    assert f'id="comments-of-{parent}" class="comment-children"' in html          # a comment on the post: level 1
    for level in (2, 3, 4):
        html = comment_on(member, here, post_id, f"t3_{parent}")
        parent = the_new_id(html)
        assert f'id="comments-of-{parent}" class="comment-children"' in html, level   # it can be answered where it stands
        assert f'id="comment-form-space-t3_{parent}"' in html                     # and has its own form
    # the fifth level is the last one the page draws: its replies are on its own page, as on a refresh
    html = comment_on(member, here, post_id, f"t3_{parent}")
    deepest = the_new_id(html)
    assert f'id="comments-of-{deepest}"' not in html
    assert f'id="comment-form-space-t3_{deepest}"' in html


def test_the_answer_still_carries_the_page_toasts_the_script_removes(make_member, real_app):
    member = make_member()
    here = member.browser()
    post_id = a_post(member, here)
    html = comment_on(member, here, post_id, f"t2_{post_id}")
    assert 'id="toast-comment-success"' in html and 'id="toast-comment-error"' in html     # the script strips these on a reply
