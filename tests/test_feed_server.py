"""A curation ranked by an outside feed server (ruqqus/helpers/feed_server.py): what is
asked, what is accepted, and how a slow or broken server is handled."""
import pytest

from ruqqus.helpers import feed_server as fs
from ruqqus.helpers import safe_fetch

URL = "https://feeds.example.org/top"
NOW = 1_800_000_000


def b36(n):
    return fs.post_id(n)


class Server:
    """Stands in for the outside server: hands out `ids` a page at a time."""

    def __init__(self, ids, page=100, fail=None):
        self.ids, self.page, self.fail, self.calls = list(ids), page, fail, []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if self.fail:
            raise safe_fetch.FetchError(self.fail)
        start = int(params.get("cursor") or 0)
        chunk = self.ids[start:start + self.page]
        more = start + self.page < len(self.ids)
        return {"posts": [b36(i) for i in chunk], "cursor": str(start + self.page) if more else None}


def ask(server, need=26, store=None, now=NOW, cid=7, url=URL):
    store = fs.MemoryStore() if store is None else store
    return fs.ids_for(cid, url, need, store=store, fetch=server, now=now), store


# --- what is asked ---------------------------------------------------------------------

def test_nothing_about_the_viewer_is_ever_sent():
    assert fs.request_params() == {"limit": 100}
    assert fs.request_params("abc") == {"limit": 100, "cursor": "abc"}
    import inspect
    # the code that talks to the server never touches the request or the person behind it
    source = "".join(inspect.getsource(f) for f in (fs.ids_for, fs.try_server, fs.request_params, fs.parse_answer))
    for word in ("g.v", "request.", "session", "remote_addr", "cookie", "username", "flask"):
        assert word not in source.replace("requests", ""), word
    assert "from flask" not in inspect.getsource(fs) and "import flask" not in inspect.getsource(fs)
    # ids_for cannot be told who is looking
    assert "v" not in inspect.signature(fs.ids_for).parameters and "viewer" not in inspect.signature(fs.ids_for).parameters


def test_the_same_list_serves_every_viewer_for_a_while():
    server = Server(range(1, 41))
    (ids, notice), store = ask(server)
    assert ids == list(range(1, 41)) and notice is None and len(server.calls) == 1
    for later in (1, 60, 119):
        (again, _), _ = ask(server, store=store, now=NOW + later)
        assert again == ids
    assert len(server.calls) == 1                       # page views are not requests the server can time
    ask(server, store=store, now=NOW + fs.CACHE_SECONDS + 1)
    assert len(server.calls) == 2


# --- what is accepted ------------------------------------------------------------------

def test_a_good_answer_is_post_ids_in_the_servers_order_once_each():
    ids, cursor = fs.parse_answer({"posts": ["z", "a", "z", "10"], "cursor": "p2"})
    assert ids == [35, 10, 36] and cursor == "p2"
    assert fs.parse_answer({"posts": []}) == ([], None)
    assert fs.parse_answer({"posts": ["a"], "cursor": ""}) == ([10], None)
    assert fs.post_id(35) == "z" and fs.post_id(36) == "10" and fs.post_id(0) == "0"
    assert int(fs.post_id(123456789), 36) == 123456789


@pytest.mark.parametrize("data", [
    None, [], "posts", {}, {"posts": "abc"}, {"posts": {"a": 1}}, {"feed": ["a"]},
    {"posts": [1, 2]}, {"posts": ["ABC"]}, {"posts": ["a b"]}, {"posts": ["a/../b"]}, {"posts": [""]}, {"posts": ["a" * 11]},
    {"posts": ["a"], "cursor": 5}, {"posts": ["a"], "cursor": "x" * 201}, {"posts": ["a"], "cursor": "a\nb"},
    {"posts": ["a"] * 0 + [fs.post_id(i) for i in range(1, 102)]},
    {"posts": ["<script>"]}, {"posts": ["1; drop table submissions"]},
])
def test_anything_else_is_a_bad_answer(data):
    with pytest.raises(fs.BadAnswer):
        fs.parse_answer(data)


def test_the_server_only_orders_what_the_viewer_may_see():
    assert fs.keep_order([5, 3, 9, 1], {1, 3, 5}) == [5, 3, 1]
    assert fs.keep_order([5, 3], set()) == [] and fs.keep_order([], {1}) == []
    # ids the site has never heard of simply are not there
    assert fs.keep_order([999999, 2], [2]) == [2]


# --- more pages ------------------------------------------------------------------------

def test_the_cursor_is_followed_only_as_far_as_the_page_needs():
    server = Server(range(1, 451))
    (ids, _), store = ask(server, need=26)
    assert len(ids) == 100 and len(server.calls) == 1
    (ids, _), _ = ask(server, need=226, store=store, now=NOW + 5)
    assert ids == list(range(1, 301)) and [c[1].get("cursor") for c in server.calls] == [None, "100", "200"]


def test_a_curation_never_holds_more_than_the_limit():
    server = Server(range(1, 2000))
    (ids, _), store = ask(server, need=10 ** 6)
    assert len(ids) == fs.MAX_IDS and len(server.calls) == fs.MAX_REQUESTS
    (again, _), _ = ask(server, need=10 ** 6, store=store, now=NOW + 5)
    assert again == ids and len(server.calls) == fs.MAX_REQUESTS       # it is done: nothing more is asked


def test_a_server_that_repeats_itself_does_not_loop():
    class Stuck(Server):
        def __call__(self, url, params):
            self.calls.append(params)
            return {"posts": ["a", "b"], "cursor": "again"}

    server = Stuck([])
    (ids, _), _ = ask(server, need=400)
    assert ids == [10, 11] and len(server.calls) == 2      # the second answer added nothing


# --- a slow or broken server -----------------------------------------------------------

def test_a_failing_server_shows_a_notice_and_nothing_else():
    server = Server([], fail="The feed server did not answer in time.")
    (ids, notice), _ = ask(server)
    assert ids == [] and notice == "The feed server did not answer in time."


def test_the_last_good_list_is_shown_while_the_server_is_down():
    good = Server(range(1, 31))
    (ids, _), store = ask(good)
    bad = Server([], fail="The feed server answered with an error (500).")
    (kept, notice), _ = ask(bad, store=store, now=NOW + fs.CACHE_SECONDS + 5)
    assert kept == ids and notice == "The feed server answered with an error (500). Showing its last answer."


def test_a_bad_answer_counts_as_a_failure_too():
    class Junk(Server):
        def __call__(self, url, params):
            self.calls.append(params)
            return {"posts": ["DROP TABLE"]}

    (ids, notice), _ = ask(Junk([]))
    assert ids == [] and "not a post id" in notice


def test_after_three_failures_in_a_row_the_server_is_left_alone():
    bad, store = Server([], fail="down"), fs.MemoryStore()
    for n in range(fs.FAILS_BEFORE_PAUSE):
        fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + n)
    assert len(bad.calls) == 3
    for n in range(5):
        ids, notice = fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + 10 + n)
        assert notice == fs.PAUSED_EMPTY
    assert len(bad.calls) == 3                          # no more requests while it is paused
    # another curation's server is not affected
    (ids, notice), _ = ask(Server([1, 2]), store=store, cid=8)
    assert ids == [1, 2] and notice is None


def test_a_success_clears_the_failures():
    store = fs.MemoryStore()
    bad, good = Server([], fail="down"), Server([4, 5])
    fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW)
    fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + 1)
    assert fs.ids_for(7, URL, 26, store=store, fetch=good, now=NOW + 2) == ([4, 5], None)
    fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + 500)
    fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + 501)
    ids, notice = fs.ids_for(7, URL, 26, store=store, fetch=bad, now=NOW + 502)
    assert ids == [4, 5] and notice != fs.PAUSED        # only now the third in a row


def test_one_host_is_only_asked_so_often_by_the_whole_site():
    store, server = fs.MemoryStore(), Server([1, 2, 3])
    for cid in range(fs.HOST_BUDGET):
        assert fs.ids_for(cid, URL, 26, store=store, fetch=server, now=NOW) == ([1, 2, 3], None)
    ids, notice = fs.ids_for(999, URL, 26, store=store, fetch=server, now=NOW)
    assert ids == [] and notice == fs.BUSY and len(server.calls) == fs.HOST_BUDGET
    # the site holding back is not the server failing
    assert store.get(fs._keys(999, URL)[1]) is None
    # another host has its own budget
    assert fs.ids_for(999, "https://other.example.net/feed", 26, store=store, fetch=server, now=NOW) == ([1, 2, 3], None)


def test_a_new_address_starts_afresh_and_can_be_forgotten():
    store = fs.MemoryStore()
    fs.ids_for(7, URL, 26, store=store, fetch=Server([1, 2]), now=NOW)
    other = "https://feeds.example.org/new"
    assert fs.ids_for(7, other, 26, store=store, fetch=Server([8, 9]), now=NOW + 1) == ([8, 9], None)
    fs.forget(7, URL, store=store)
    server = Server([3])
    assert fs.ids_for(7, URL, 26, store=store, fetch=server, now=NOW + 2) == ([3], None) and len(server.calls) == 1


def test_while_one_page_view_is_asking_the_others_do_not():
    store, server = fs.MemoryStore(), Server([1, 2])
    assert store.claim(fs._keys(7, URL)[2], 10)
    assert fs.ids_for(7, URL, 26, store=store, fetch=server, now=NOW) == ([], None) and server.calls == []


def test_the_owners_preview_asks_once_and_says_what_went_wrong():
    assert fs.try_server(URL, fetch=Server([3, 1, 2])) == [3, 1, 2]
    with pytest.raises(safe_fetch.FetchError) as err:
        fs.try_server(URL, fetch=Server([], fail="The feed server took too long to answer."))
    assert "too long" in err.value.message


def test_it_can_be_switched_off_for_the_whole_site(monkeypatch):
    monkeypatch.delenv("FEED_SERVERS_ENABLED", raising=False)
    assert fs.enabled()
    monkeypatch.setenv("FEED_SERVERS_ENABLED", "0")
    assert not fs.enabled()
