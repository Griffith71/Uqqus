"""A member's display name (ruqqus/helpers/display_names.py): what can be saved and how names are compared.

Every invisible or look-alike character is built with chr() so this file holds no hidden characters."""
import pytest

from ruqqus.helpers import display_names as dn

ZWJ, VS16, NBSP = chr(0x200D), chr(0xFE0F), chr(0x00A0)
ZERO_WIDTH_SPACE, RTL_OVERRIDE, BOM, LINE_SEP = chr(0x200B), chr(0x202E), chr(0xFEFF), chr(0x2028)
WOMAN, LAPTOP, HEART, NEWSPAPER = chr(0x1F469), chr(0x1F4BB), chr(0x2764), chr(0x1F4F0)
FULLWIDTH_A = chr(0xFF21)


def test_a_normal_name_is_kept_with_its_spaces_accents_and_emoji():
    assert dn.clean("Ada Lovelace") == "Ada Lovelace"
    assert dn.clean("Zoë Müller-Ñandú") == "Zoë Müller-Ñandú"
    assert dn.clean("BBC Top Stories " + NEWSPAPER) == "BBC Top Stories " + NEWSPAPER
    joined = WOMAN + ZWJ + LAPTOP + " coder"                              # an emoji sequence is glued with a joiner
    assert dn.clean(joined) == joined
    hearts = HEART + VS16 + " hearts"
    assert dn.clean(hearts) == hearts


def test_spaces_are_tidied_and_empty_clears_it():
    assert dn.clean("  Ada \t  Lovelace \n") == "Ada Lovelace"
    assert dn.clean("a" + NBSP + "b") == "a b"                            # a non-breaking space is a space
    assert dn.clean("line" + LINE_SEP + "break") == "line break"          # so is a line separator
    assert dn.clean("") == "" and dn.clean(None) == "" and dn.clean("   \n ") == ""


def test_the_length_limit_counts_characters():
    assert dn.clean("x" * dn.MAX_LEN) == "x" * dn.MAX_LEN
    with pytest.raises(dn.Refused, match="at most 50"):
        dn.clean("x" * (dn.MAX_LEN + 1))
    assert dn.clean("é" * dn.MAX_LEN) == "é" * dn.MAX_LEN                  # characters, not bytes


@pytest.mark.parametrize("value,words", [
    ("<b>bold</b>", "< or >"),
    ("a > b", "< or >"),
    ("@admin", "@"),
    ("not @admin really", "@"),
    ("zero" + ZERO_WIDTH_SPACE + "width", "invisible"),
    ("right" + RTL_OVERRIDE + "left", "invisible"),                       # a right-to-left override flips what follows
    ("bell" + chr(7), "invisible"),
    (BOM + "bom", "invisible"),
])
def test_what_cannot_be_saved(value, words):
    with pytest.raises(dn.Refused) as refused:
        dn.clean(value)
    assert words in str(refused.value)


def test_names_are_compared_without_case_spaces_dots_dashes_or_underscores():
    for name in ("Admin", "A D M I N", "a.d.min", "ad-min", "ad_min", "ADMIN"):
        assert dn.key(name) == dn.key("admin")
    assert dn.key("admin") != dn.key("admins")
    assert dn.key(FULLWIDTH_A + "dmin") == dn.key("admin")                # a full-width "A" reads as an "A"
    assert dn.key("") == "" and dn.key(None) == ""


def test_the_name_to_draw_is_the_display_name_or_the_handle():
    assert dn.shown("Ada Lovelace", "ada") == "Ada Lovelace"
    assert dn.shown("", "ada") == "ada"
    assert dn.shown(None, "ada") == "ada"


def test_this_file_holds_no_hidden_characters():
    import pathlib

    text = pathlib.Path(__file__).read_text(encoding="utf-8")
    hidden = [hex(ord(c)) for c in text if ord(c) in (0x200B, 0x200D, 0xFE0F, 0x202E, 0xFEFF, 0x00A0, 0x2028)]
    assert hidden == []


# --- the database side: nobody wears another account's handle ------------------------------------------------------

def _users():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    db = Session(create_engine("sqlite://"))
    db.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, original_username TEXT)"))
    for uid, name, original in ((1, "ada", "ada"), (2, "Big_Admin", "Big_Admin"), (3, "renamed", "first_name")):
        db.execute(text("INSERT INTO users VALUES (:i, :n, :o)"), {"i": uid, "n": name, "o": original})
    return db


def test_a_display_name_that_is_another_accounts_handle_is_taken():
    from ruqqus.helpers import display_name_store as store

    db = _users()
    for name in ("Big_Admin", "big admin", "BigAdmin", "b.i.g-a-d-m-i-n", "FIRST NAME", "first_name"):
        assert store.takes_a_handle(db, 1, name), name        # the current handle, or the one it started with
    assert not store.takes_a_handle(db, 1, "Ada Lovelace")
    assert not store.takes_a_handle(db, 1, "Big Administrator")
    assert not store.takes_a_handle(db, 1, "")


def test_your_own_handle_is_fine_as_your_own_display_name():
    from ruqqus.helpers import display_name_store as store

    db = _users()
    assert not store.takes_a_handle(db, 1, "Ada")             # it is the account's own
    assert not store.takes_a_handle(db, 2, "big admin")
    assert store.takes_a_handle(db, 1, "big admin")
    assert store.takes_a_handle(db, None, "ada")              # an account that does not exist yet has no handle of its own
