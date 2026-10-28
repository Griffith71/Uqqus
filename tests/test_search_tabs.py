"""The kind of a search and its tab bar (ruqqus/helpers/search_tabs.py, the owner, 2026-10-26: "a tab filter like reddit.com").
A typed + @ # & still wins over `?type=`, a tab-driven guild, account or curation search is the same search written with its
symbol, tab links start from the query without its symbol, and only a post or comment search keeps operators."""
from ruqqus.helpers import search_tabs as st


def kinds(q, t=None):
    r = st.resolve(q, t)
    return r.kind, r.query, r.refused


def test_plain_text_is_posts_and_comments_keep_their_own_type():
    assert kinds("cats") == ("posts", "cats", False)
    assert kinds("cats", "comments") == ("comments", "cats", False)
    assert kinds("cats", "posts") == ("posts", "cats", False)
    assert kinds("cats from:@alice since:2026-01-01", "comments")[1] == "cats from:@alice since:2026-01-01"


def test_a_typed_symbol_wins_over_the_type():
    assert kinds("+pets", "comments") == ("guilds", "+pets", False)
    assert kinds("@alice", "guilds") == ("accounts", "@alice", False)
    assert kinds("&news", "accounts") == ("curations", "&news", False)
    assert kinds("#food", "comments") == ("guilds", "#food", False)          # a topic search is the Guilds tab
    assert kinds("++x") == ("guilds", "++x", False)                            # the branches strip every leading symbol


def test_a_bogus_type_is_posts():
    assert kinds("cats", "nonsense") == ("posts", "cats", False)
    assert kinds("cats", "TOPICS") == ("posts", "cats", False)
    assert kinds("cats", None) == ("posts", "cats", False)


def test_a_name_tab_is_the_same_search_written_with_its_symbol():
    assert kinds("pets", "guilds") == ("guilds", "+pets", False)
    assert kinds("alice", "accounts") == ("accounts", "@alice", False)
    assert kinds("news", "curations") == ("curations", "&news", False)
    assert kinds('"black cat"', "guilds")[1] == "+black cat"                    # a phrase is its words
    assert kinds("pets", "GUILDS")[1] == "+pets"


def test_a_name_tab_takes_the_free_words_and_says_when_it_dropped_filters():
    r = st.resolve("pets from:@alice since:2026-01-01 -dogs", "guilds")
    assert (r.query, r.ignored_filters) == ("+pets", True)
    assert st.resolve("pets", "guilds").ignored_filters is False
    assert st.resolve("pets from:@alice", "comments").ignored_filters is False      # comments keep them
    assert st.resolve("+pets from:@alice", "guilds").ignored_filters is False                 # typed: the text is literal, as always


def test_a_tab_with_nothing_to_look_for_lists_nothing_but_a_bare_symbol_still_lists():
    assert kinds("", "guilds") == ("guilds", "", True)
    assert kinds("   ", "accounts")[2] is True
    assert kinds("from:@alice", "curations")[2] is True                            # only operators
    assert kinds("+", None) == ("guilds", "+", False)                              # typed, as before
    assert kinds("", None) == ("posts", "", False)                                 # an empty post search lists, as before


def test_the_text_of_a_name_search_is_capped():
    assert len(st.resolve("x" * 400, "guilds").query) == 1 + st.MAX_TEXT


def test_the_bar_links_start_from_the_query_without_its_symbol():
    bar = {t["key"]: t for t in st.tabs("+pets")}
    assert [t["key"] for t in st.tabs("+pets")] == ["posts", "comments", "guilds", "accounts", "curations"]
    assert bar["guilds"]["active"] and not bar["posts"]["active"]
    assert bar["posts"]["href"] == "/search?q=pets"
    assert bar["comments"]["href"] == "/search?q=pets&type=comments"
    assert bar["accounts"]["href"] == "/search?q=pets&type=accounts"
    assert bar["guilds"]["href"] == "/search?q=%2Bpets"                             # the active tab is the page as it is
    topic = {t["key"]: t for t in st.tabs("#food")}
    assert topic["guilds"]["active"] and topic["guilds"]["href"] == "/search?q=%23food" and topic["posts"]["href"] == "/search?q=food"


def test_posts_and_comments_carry_their_operators_and_a_name_tab_only_the_words():
    bar = {t["key"]: t for t in st.tabs("cats from:@alice", comment_search=False)}
    assert bar["comments"]["href"] == "/search?q=cats%20from%3A%40alice&type=comments"
    assert bar["guilds"]["href"] == "/search?q=cats&type=guilds"
    comments = {t["key"]: t for t in st.tabs("cats from:@alice", comment_search=True)}
    assert comments["comments"]["active"] and comments["comments"]["href"] == "/search?q=cats%20from%3A%40alice&type=comments"
    assert comments["posts"]["href"] == "/search?q=cats%20from%3A%40alice"
    assert comments["accounts"]["href"] == "/search?q=cats&type=accounts"


def test_a_tab_with_nothing_typed_names_its_own_active_tab():
    bar = {t["key"]: t for t in st.tabs("", kind="guilds")}
    assert bar["guilds"]["active"] and bar["guilds"]["href"] == "/search?q=&type=guilds"


def test_a_guilds_own_search_has_posts_and_comments_only_and_stays_in_the_guild():
    bar = st.tabs("film", guild_name="pets")
    assert [t["key"] for t in bar] == ["posts", "comments"]
    assert bar[0]["href"] == "/+pets/search?q=film" and bar[1]["href"] == "/+pets/search?q=film&type=comments"
    assert [t for t in st.tabs("film", comment_search=True, guild_name="pets") if t["active"]][0]["key"] == "comments"


def test_a_names_symbols_and_spaces_are_encoded_in_links():
    bar = {t["key"]: t for t in st.tabs("a&b <c> d")}
    assert bar["comments"]["href"] == "/search?q=a%26b%20%3Cc%3E%20d&type=comments"
    assert "<" not in "".join(t["href"] for t in st.tabs("<script>"))


def test_the_navbar_box_keeps_the_tab_on_a_results_page_only():
    assert st.kept_type("/search", {"type": "guilds"}) == "guilds"
    assert st.kept_type("/search", {"type": "comments"}) == "comments"
    assert st.kept_type("/search", {"type": "posts"}) == ""
    assert st.kept_type("/search", {"type": "bogus"}) == ""
    assert st.kept_type("/", {"type": "guilds"}) == ""
    assert st.kept_type("/+pets/search", {"type": "comments"}) == "comments"
    assert st.kept_type("/+pets/search", {"type": "guilds"}) == ""                  # a guild's search has no such tab
    assert st.kept_type("/search", {}) == ""
