"""Community notes (ruqqus/helpers/community_notes.py): what a note may say, and which id a
post's requests and note are kept under."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import community_notes as cn


# --- the id a post's requests and note are kept under -----------------------------------

def test_a_post_is_its_own_target_and_a_forwarded_copy_points_at_the_original():
    assert cn.primary_post_id(NS(id=7, repost_id=0)) == 7
    assert cn.primary_post_id(NS(id=7, repost_id=None)) == 7
    assert cn.primary_post_id(NS(id=9, repost_id=7)) == 7          # a forward of post 7


# --- what a note may say ------------------------------------------------------------------

def test_a_note_is_trimmed_plain_text():
    assert cn.clean_body("  This claim is disputed.  ") == "This claim is disputed."
    assert cn.clean_body("one\r\ntwo\rthree") == "one\ntwo\nthree"
    assert cn.clean_body("first\n\n\n\n\nsecond") == "first\n\nsecond"
    assert cn.clean_body("trailing spaces   \nstay out  ") == "trailing spaces\nstay out"


def test_control_and_invisible_characters_are_dropped():
    assert cn.clean_body("a\x00b\x07c​d﻿e f") == "abcdef"
    assert cn.clean_body("tab\tbecomes a space") == "tab becomes a space"


def test_html_is_text_not_markup():
    # it is escaped when drawn; nothing here tries to be clever about it
    assert cn.clean_body("<script>alert(1)</script>") == "<script>alert(1)</script>"


@pytest.mark.parametrize("text", [None, "", "   ", "\n\n", "​​", "\x00"])
def test_an_empty_note_is_refused(text):
    with pytest.raises(cn.NoteError) as err:
        cn.clean_body(text)
    assert err.value.message == "Write the note."


def test_a_long_note_is_refused_with_its_length():
    assert len(cn.clean_body("x" * cn.MAX_CHARS)) == cn.MAX_CHARS
    with pytest.raises(cn.NoteError) as err:
        cn.clean_body("x" * (cn.MAX_CHARS + 1))
    assert "600 characters at most (this one is 601)" in err.value.message


def test_the_limits_match_the_column():
    import re
    from pathlib import Path

    model = (Path(__file__).resolve().parent.parent / "ruqqus" / "classes" / "community_notes.py").read_text(encoding="utf-8")
    assert f"body = Column(String({cn.MAX_CHARS})" in model
    assert re.search(rf"body character varying\({cn.MAX_CHARS}\)", (Path(__file__).resolve().parent.parent / "schema.sql").read_text(encoding="utf-8"))
