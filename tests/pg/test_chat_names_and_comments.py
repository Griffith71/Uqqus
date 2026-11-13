"""Names in chat and comments sent in a chat, on the real app (CLAUDE.md "Send a post to a chat", "Display names"): a 1:1
chat's other person comes with the name the rest of the site shows (never the chat server's), the @handle alone when the
viewer's word filter hides it; a comment's card is made for its reader by the rules of opening it and its post, and never
names an anonymous author. Nothing here reaches Matrix."""
import secrets
import time

from sqlalchemy import text

from .test_characterization import fresh
from .test_comment_reply_render import a_post, post, the_new_id


def _comment(member, here, post_b36, anonymous=False):
    data = {"parent_fullname": f"t2_{post_b36}", "submission": post_b36, "formkey": member.formkey(here),
            "body": "Words of the comment " + secrets.token_hex(4)}
    if anonymous:
        data["anonymous"] = "true"
    answer = post(here, "/api/comment", data)
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    return the_new_id(answer.get_json()["html"]), data["body"]


def _pair(db, a, b):
    now = int(time.time())
    db.execute(text("INSERT INTO chat_conversations (user_a_id, user_b_id, initiator_id, matrix_room_id, status, created_utc, "
                    "last_activity_utc) VALUES (:a, :b, :a, :r, 'inbox', :n, :n)"),
               {"a": a.id, "b": b.id, "r": f"!p{secrets.token_hex(6)}:chat.localhost", "n": now})
    db.commit()


def _other(member):
    answer = member.browser().get("/api/chat/conversations?status=inbox")
    assert answer.status_code == 200
    return answer.get_json()["conversations"][0]["other_user"]


def test_a_1to1_chat_shows_the_other_persons_name_as_the_site_does(make_member):
    me, them = make_member(), make_member()
    db = fresh(me)
    db.execute(text("UPDATE users SET display_name = 'Sam Lee' WHERE id = :u"), {"u": them.id})
    db.commit()
    _pair(db, me, them)
    shown = _other(me)
    assert (shown["username"], shown["display_name"], shown["shown_name"]) == (them.username, "Sam Lee", "Sam Lee")

    # a name the viewer's word filter hides (the Standard level hides a rating of 2): the @handle alone
    db = fresh(me)
    db.execute(text("UPDATE users SET name_severity = 2 WHERE id = :u"), {"u": them.id})
    db.commit()
    shown = _other(me)
    assert (shown["display_name"], shown["shown_name"]) == ("", them.username)


def test_a_comment_sent_in_a_chat_shows_its_words_and_post_and_never_an_anonymous_author(make_member):
    author, reader = make_member(), make_member()
    here = author.browser()
    post_b36 = a_post(author, here)
    named, words = _comment(author, here, post_b36)
    card = reader.browser().get(f"/api/chat/comment_preview/{named}").get_json()
    assert card["ok"] and card["text"] == words and card["title"].startswith("Title ") and card["guild"] is None
    assert card["url"].endswith(f"/comment/{named}")
    assert author.username not in str({k: v for k, v in card.items() if k != "url"})

    anonymous, _ = _comment(author, here, post_b36, anonymous=True)
    card = reader.browser().get(f"/api/chat/comment_preview/{anonymous}").get_json()
    assert card["ok"] and author.username not in str(card)                     # its address names nobody either

    assert reader.browser().get("/api/chat/comment_preview/zzzzzz").get_json() == {"ok": False, "notice": "Not available to you."}
    assert reader.browser().get("/api/chat/comment_preview/a%2Fb").status_code in (200, 404)


def test_a_comment_card_follows_blocks_and_deletes(make_member):
    author, reader = make_member(), make_member()
    here = author.browser()
    post_b36 = a_post(author, here)
    cid, _ = _comment(author, here, post_b36)
    db = fresh(author)
    db.execute(text("INSERT INTO userblocks (user_id, target_id, created_utc) VALUES (:a, :b, :n)"),
               {"a": reader.id, "b": author.id, "n": int(time.time())})
    db.commit()
    assert reader.browser().get(f"/api/chat/comment_preview/{cid}").get_json()["ok"] is False
    db = fresh(author)
    db.execute(text("DELETE FROM userblocks WHERE user_id = :a"), {"a": reader.id})
    db.execute(text("UPDATE comments SET deleted_utc = :n WHERE id = :c"), {"n": int(time.time()), "c": int(cid, 36)})
    db.commit()
    assert reader.browser().get(f"/api/chat/comment_preview/{cid}").get_json() == {"ok": False, "notice": "This post was deleted."}


def test_send_in_chat_is_in_a_comments_share_menus(make_member):
    author, reader = make_member(), make_member()
    here = author.browser()
    post_b36 = a_post(author, here)
    cid, _ = _comment(author, here, post_b36)
    page = reader.browser().get(f"/post/{post_b36}", follow_redirects=True).get_data(as_text=True)
    assert page.count(f'share-chat-item" href="javascript:void(0);" role="button" data-share-url="https://localhost/@{author.username}/comment/{cid}"') == 1
    assert page.count(f'share-chat-item" data-dismiss="modal" data-share-url="https://localhost/@{author.username}/comment/{cid}"') == 1
