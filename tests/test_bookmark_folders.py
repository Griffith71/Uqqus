"""Bookmark folders (ruqqus/helpers/bookmark_folders.py): the pure rules, without a database."""
import pytest

from ruqqus.helpers import bookmark_folders as bf


# --- the name ---------------------------------------------------------------------------------

def test_a_name_is_one_line_with_single_spaces():
    assert bf.clean_name("  Read   later \n") == "Read later"
    assert bf.clean_name("Recipes\tand\r\nmeals") == "Recipes and meals"
    assert bf.clean_name("été ☀") == "été ☀"          # any language, symbols


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t "])
def test_an_empty_name_is_refused(raw):
    with pytest.raises(bf.FolderError) as err:
        bf.clean_name(raw)
    assert err.value.message == "Give the folder a name."


def test_the_name_has_a_limit():
    assert bf.clean_name("x" * bf.NAME_CHARS) == "x" * bf.NAME_CHARS
    with pytest.raises(bf.FolderError) as err:
        bf.clean_name("x" * (bf.NAME_CHARS + 1))
    assert "40 characters at most" in err.value.message


def test_control_characters_are_refused():
    with pytest.raises(bf.FolderError):
        bf.clean_name("bell\x07")
    # a NUL is read as a space, like any other whitespace, rather than kept
    assert bf.clean_name("a\x00b") == "a b"


@pytest.mark.parametrize("raw", ["all", "ALL", " None ", "none"])
def test_a_folder_cannot_take_the_name_of_a_view(raw):
    with pytest.raises(bf.FolderError) as err:
        bf.clean_name(raw)
    assert "already used for a view" in err.value.message


# --- making and renaming -----------------------------------------------------------------------

def test_a_name_is_unique_per_member_ignoring_case():
    assert bf.check_new("Recipes", ["Travel", "News"], 2) is None
    assert bf.check_new("recipes", ["Travel", "Recipes"], 2) == "You already have a folder called recipes."


def test_at_most_thirty_folders():
    assert bf.check_new("New", [f"f{i}" for i in range(bf.MAX_FOLDERS - 1)], bf.MAX_FOLDERS - 1) is None
    assert bf.check_new("New", [f"f{i}" for i in range(bf.MAX_FOLDERS)], bf.MAX_FOLDERS) == "You can have 30 folders at most."
    assert bf.MAX_FOLDERS == 30 and bf.NAME_CHARS == 40


def test_renaming_may_change_only_the_case_of_its_own_name():
    # the folder's own current name is not among the "others"
    assert bf.check_rename("recipes", ["Travel"]) is None
    assert bf.check_rename("travel", ["Travel"]) == "You already have a folder called travel."


# --- the view ----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, (bf.ALL, None)), ("", (bf.ALL, None)), ("all", (bf.ALL, None)), ("ALL", (bf.ALL, None)),
    ("none", (bf.UNSORTED, None)), (" None ", (bf.UNSORTED, None)),
    ("7", ("folder", 7)), ("12", ("folder", 12)),
])
def test_the_folder_filter_is_read_from_the_address(raw, expected):
    assert bf.parse_filter(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1;drop table users", "1.5", "99999999999999999999", "٣"])
def test_anything_else_is_all_the_bookmarks(raw):
    assert bf.parse_filter(raw) == (bf.ALL, None)


def test_an_id_is_plain_ascii_digits_inside_a_database_integer():
    assert bf.parse_id("12") == 12 and bf.parse_id(" 7 ") == 7
    for raw in (None, "", "0", "-3", "1.5", "abc", "٣", "99999999999", str(2 ** 31)):
        assert bf.parse_id(raw) is None, raw
    assert bf.parse_id(str(2 ** 31 - 1)) == 2 ** 31 - 1


def test_a_link_to_a_view_says_which_one():
    assert bf.query_value(bf.ALL, None) == ""
    assert bf.query_value(bf.UNSORTED, None) == "none"
    assert bf.query_value("folder", 12) == "12"
