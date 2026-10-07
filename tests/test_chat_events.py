"""The chat appservice webhook counts new messages only (ruqqus/helpers/chat_events.py), and the
chat client (assets/chat_src/src) keeps the features and wording the site expects."""
import re
from pathlib import Path

from ruqqus.helpers.chat_events import is_new_message

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"
SRC = ROOT / "assets" / "chat_src" / "src"
INDEX = (SRC / "index.js").read_text(encoding="utf-8")
UI = (SRC / "ui.js").read_text(encoding="utf-8")
ROUTE = (ROOT / "routes" / "chat.py").read_text(encoding="utf-8")


def test_a_plain_message_counts():
    assert is_new_message({"type": "m.room.message", "content": {"msgtype": "m.text", "body": "hi"}})
    assert is_new_message({"type": "m.room.encrypted", "content": {"algorithm": "m.megolm.v1.aes-sha2", "ciphertext": "x"}})
    assert is_new_message({"type": "m.room.message", "content": {}})
    assert is_new_message({"type": "m.room.message"})


def test_a_reaction_or_an_edit_is_not_a_new_message_even_when_encrypted():
    # in an encrypted room only the relation is readable, never the emoji or the text
    reaction = {"type": "m.room.encrypted", "content": {"ciphertext": "x", "m.relates_to": {"rel_type": "m.annotation", "event_id": "$a"}}}
    edit = {"type": "m.room.encrypted", "content": {"ciphertext": "x", "m.relates_to": {"rel_type": "m.replace", "event_id": "$a"}}}
    assert not is_new_message(reaction)
    assert not is_new_message(edit)


def test_a_quote_is_a_message_and_odd_content_does_not_crash():
    quote = {"type": "m.room.message", "content": {"body": "x", "m.relates_to": {"m.in_reply_to": {"event_id": "$a"}}}}
    assert is_new_message(quote)
    assert is_new_message({"type": "m.room.message", "content": {"m.relates_to": "garbage"}})
    assert is_new_message({"type": "m.room.message", "content": None})


def test_the_webhook_uses_the_rule():
    assert "if not is_new_message(event):" in ROUTE
    # before the room is looked up, so a reaction touches nothing
    assert ROUTE.index("is_new_message(event)") < ROUTE.index("convo = g.db.query(ChatConversation)")


def test_the_client_offers_each_message_action():
    for handler in ("onQuote", "onEdit", "onReact", "onDelete"):
        assert f"{handler}(" in INDEX and handler in UI, handler
    # reactions and edits are events of their own and must not show as bubbles
    assert 'event.isRelation("m.annotation") || event.isRelation("m.replace")' in INDEX
    assert "sendTyping(" in INDEX and "RoomMemberEvent.Typing" in INDEX
    assert "hasUserReadEvent(" in INDEX and "RoomEvent.Receipt" in INDEX
    assert "redactEvent(" in INDEX


def test_edits_replace_the_text_and_quotes_point_at_the_original():
    assert '"m.new_content"' in INDEX and 'rel_type: "m.replace"' in INDEX
    assert '"m.in_reply_to"' in INDEX
    assert 'rel_type: "m.annotation"' in INDEX


def test_message_text_is_never_put_in_through_innerhtml():
    build = UI[UI.index("function buildMessage"):UI.index("export function setTabBadge")]
    assert "innerHTML" not in build.replace('b.innerHTML = `<i class="${iconClass}"></i>`;', "")
    # still text: the words around a shared post's link, or the message as it is
    assert 'bubble.textContent = shared ? item.body.replace(shared[0], "").trim() || "Shared a post" : item.body' in build
    assert "quote.textContent = item.quote" in build


def test_the_wording_says_quote_not_the_banned_word():
    # the site's vocabulary (CLAUDE.md): a response to something is a comment, never a "reply".
    # In chat it is a quote. Matrix's own field name m.in_reply_to is the protocol, not our wording.
    for name, source in (("index.js", INDEX), ("ui.js", UI)):
        text = source.replace("m.in_reply_to", "")
        assert not re.search(r"\breplies|reply|replied|replying\b", text, re.I), name


def test_a_receipt_is_sent_once_per_newest_message_not_once_per_draw():
    # our own receipt comes back as a Receipt event that redraws the thread: sending one on every
    # draw is a request loop that floods the server (and trips its rate limit)
    assert "state.active.readUpTo !== last.getId()" in INDEX
    assert INDEX.count("sendReadReceipt(") == 1 and INDEX.count("markRead(boot.formkey") == 1
    assert "!last.status" in INDEX      # never for a message that is still being sent


def test_the_other_person_is_found_by_id_not_from_the_room_member_list():
    # the member list also holds whoever created the room (and left): the first "other" member is not the person
    assert "function otherMxidOf(conversation)" in INDEX
    assert "getMembers().find" not in INDEX
    assert "room.getMember(otherMxidOf(" in INDEX or "getMember(otherMxidOf(" in INDEX
