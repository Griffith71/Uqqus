"""Block-list export and import: the file format, the limits and what an import says (helpers/blocklist.py). The blocking
itself, on the real app, is tests/pg/test_blocklist.py."""
import pytest

from ruqqus.helpers import blocklist


def test_a_file_exported_here_reads_back_the_same():
    text = blocklist.render(["Bob", "alice"], ["Carol"])
    assert text.splitlines() == ["kind,username", "block,alice", "block,Bob", "mute,Carol"]
    rows, problem = blocklist.parse(text)
    assert problem is None
    assert rows == [("block", "alice"), ("block", "Bob"), ("mute", "Carol")]


def test_a_plain_list_of_names_is_a_list_of_blocks():
    rows, _ = blocklist.parse("alice\n@bob\r\n  carol  \n\n# a note\nalice\n")
    assert rows == [("block", "alice"), ("block", "bob"), ("block", "carol")]


def test_names_may_share_a_line_and_a_byte_order_mark_is_ignored():
    rows, _ = blocklist.parse("﻿kind,username\nblock,alice, bob  carol")
    assert rows == [("block", "alice"), ("block", "bob"), ("block", "carol")]


def test_a_name_that_is_also_a_word_of_the_format_is_still_a_name():
    rows, _ = blocklist.parse("mute\nblock\nkind\nmute,mute")
    assert rows == [("block", "mute"), ("block", "block"), ("block", "kind"), ("mute", "mute")]


def test_duplicates_are_dropped_per_kind_and_ignoring_case():
    rows, _ = blocklist.parse("block,Alice\nblock,alice\nmute,ALICE\n@alice")
    assert rows == [("block", "Alice"), ("mute", "ALICE")]


@pytest.mark.parametrize("typed", ["a", "ab", "has?space", "x" * 26, "bad-name", "ünïcode", "<script>", "a;b", "../etc"])
def test_a_name_that_could_not_be_a_username_counts_as_not_found_and_is_never_looked_up(typed):
    rows, problem = blocklist.parse(typed)
    assert problem is None and all(name is None for _, name in rows), rows


def test_nothing_in_the_file_is_an_error():
    for raw in ("", "   \n\n", "kind,username\n", "# only a note", None):
        assert blocklist.parse(raw) == ([], "empty")


def test_at_most_200_names_and_a_file_size_limit():
    ok = "\n".join(f"user{i:03}" for i in range(200))
    assert blocklist.parse(ok)[1] is None
    assert blocklist.parse(ok + "\nuser200") == ([], "count")
    assert blocklist.parse("x" * (blocklist.MAX_CHARS + 1)) == ([], "size")
    assert set(blocklist.PROBLEMS) == {"size", "count", "empty"}


def test_a_report_is_counts_only_and_a_refusal_reads_like_a_missing_name():
    counts = blocklist.tally(["blocked", "blocked", "muted", "already", "missing", "self", "protected", "deleted", None])
    assert counts == {"blocked": 2, "muted": 1, "already": 1, "not_found": 5}
    assert blocklist.summary(counts) == "Imported: 2 blocked, 1 muted, 1 already on your list, 5 not found."
    assert blocklist.summary(blocklist.tally([])) == "Nothing was imported."


def test_the_result_travels_in_the_address_as_numbers_only():
    counts = {"blocked": 3, "muted": 0, "already": 2, "not_found": 7}
    assert blocklist.counts_from_query(blocklist.counts_to_query(counts)) == counts
    for hostile in ("<b>hi</b>", "1.2.3", "1.2.3.4.5", "a.b.c.d", "-1.0.0.0", "99999.0.0.0", "", None):
        assert blocklist.counts_from_query(hostile) is None, hostile


def test_the_refusals_keep_the_block_buttons_messages_and_statuses():
    assert blocklist.REFUSALS["missing"] == ("That user doesn't exist.", 404)
    assert blocklist.REFUSALS["self"] == ("You can't block yourself.", 409)
    assert blocklist.REFUSALS["already"][1] == 409 and "{name}" in blocklist.REFUSALS["already"][0]
    assert blocklist.REFUSALS["protected"][1] == 409
    assert blocklist.REFUSALS["deleted"] == ("That account has been deactivated", 410)
