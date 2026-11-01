"""Ads on the real app and a real Postgres (helpers/ads.py has the rules, tests/test_ads_assets.py runs the tag's script): which pages
draw a box, who gets one, personalisation by country and answer, what a cache can keep, the hold after a password sign-in, and an
admin forcing a post sensitive. Never Google's tag: nothing here loads a page in a browser, and the client id is made up."""
import re
import secrets
import time

import pytest
from sqlalchemy import text

BASE = "http://localhost"
AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
ENV = {"ADSENSE_CLIENT": "ca-pub-0000000000000000", "ADSENSE_SLOT_FEED": "1111111111", "ADSENSE_SLOT_POST": "2222222222",
       "ADSENSE_SLOT_SIDEBAR": "3333333333"}
NON_PERSONAL = "requestNonPersonalizedAds = 1"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country=None):
    headers = {"User-Agent": AGENT, "Accept-Language": "en"}
    if country:
        headers["CF-IPCountry"] = country
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()}, follow_redirects=True)


def html(answer):
    assert answer.status_code == 200, answer.status_code
    return answer.get_data(as_text=True)


def boxes(page):
    return len(re.findall(r'<aside class="ad-slot', page)) + page.count('<template id="feed-ad">')


@pytest.fixture
def ads_on(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def writer(make_member):
    member = make_member()
    while member.id == 1:
        member = make_member()
    return member


def add_post(member, board_id=2, body="<p>Plain words.</p>", title=None, url=None, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):      # the models read the request's address
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()),
                         post_public=True, **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=title or f"Ads test {secrets.token_hex(4)}", body=re.sub("<[^>]+>", "", body), body_html=body, url=url))
    db.commit()
    return base36encode(row.id)


def add_comment(member, post_b36, body="<p>A fair point.</p>", **flags):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36decode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=base36decode(post_b36), level=1, original_board_id=2,
                      created_utc=int(time.time()), **flags)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body=re.sub("<[^>]+>", "", body), body_html=body))
    db.commit()


def add_guild(real_app, private=False, sensitive=False):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    db.rollback()
    name = "ci_ads_" + secrets.token_hex(3)
    board = Board(name=name, description="a guild for the ads test", description_html="<p>a guild for the ads test</p>", creator_id=1,
                  subcat_id=0, is_private=private, is_sensitive=sensitive, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id, name


def set_answer(member, answer):
    db = member.db_session()
    db.rollback()
    filter_level = {"filter_level": 2} if answer == 2 else {}
    db.execute(text("UPDATE users SET age_declared = :a, age_declared_utc = 5" + (", filter_level = 2" if filter_level else "") +
                    " WHERE id = :u"), {"a": answer, "u": member.id})
    db.commit()


def test_nothing_with_ads_off_and_cards_carry_no_mark(real_app, writer):
    app, _ = real_app
    pid = add_post(writer)
    page = html(get(app.test_client(), f"/post/{pid}"))
    assert boxes(page) == 0 and "data-ad-safe" not in page and "googlesyndication" not in page
    assert get(app.test_client(), "/ads.txt").status_code == 404


def test_a_client_id_alone_serves_ads_txt_and_tells_the_reader_nothing(real_app, writer, monkeypatch):
    app, _ = real_app
    monkeypatch.setenv("ADSENSE_CLIENT", ENV["ADSENSE_CLIENT"])
    assert html(get(app.test_client(), "/ads.txt")).startswith("google.com, pub-0000000000000000, DIRECT")
    privacy = get(app.test_client(), "/help/privacy")
    assert 'id="ads"' not in html(privacy) and "googlesyndication" not in html(privacy)
    assert privacy.headers["Referrer-Policy"] == "same-origin"
    assert "does not accept advertising" in html(get(app.test_client(), "/help/faq"))
    assert boxes(html(get(app.test_client(), f"/post/{add_post(writer)}"))) == 0


def test_live_tells_the_reader_and_the_visitors_page_carries_the_tag_by_script_only(real_app, writer, ads_on):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    answer = get(app.test_client(), f"/post/{pid}")
    page = html(answer)
    assert page.count('class="ad-slot ad-slot-post"') == 1 and "<script async" not in page.split("googlesyndication")[0][-200:]
    assert 'tag.src = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-0000000000000000"' in page
    assert NON_PERSONAL in page and 'localStorage.getItem("ruqqus_chat_session")' in page and 'localStorage.getItem("ruqqus_call_session")' in page
    assert answer.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    privacy = html(get(app.test_client(), "/help/privacy"))
    assert 'id="ads"' in privacy and "ruqqus_chat_session" in privacy and "data-ad-consent" in privacy
    assert "does show ads from Google AdSense" in html(get(app.test_client(), "/help/faq"))


@pytest.mark.parametrize("over", [{"is_sensitive": True}, {"word_severity": 1}, {"word_severity": 2}, {"post_public": False}])
def test_an_unfit_post_has_no_box_and_no_tag(real_app, writer, make_member, ads_on, over):
    app, _ = real_app
    if over.get("post_public") is False:          # a non-public post needs someone who may open it
        pid = add_post(writer)
        db = writer.db_session()
        db.execute(text("UPDATE submissions SET post_public = false WHERE id = :i"), {"i": int(pid, 36)})
        db.commit()
        page = html(get(writer.browser(), f"/post/{pid}"))
    else:
        page = html(get(writer.browser(), f"/post/{add_post(writer, **over)}"))
    assert boxes(page) == 0 and "googlesyndication" not in page


@pytest.mark.parametrize("comment", [{"word_severity": 1}, {"is_sensitive": True}, {"body": '<p><img src="/media/1/a.png"></p>'}])
def test_a_post_page_with_an_unfit_comment_has_no_box(real_app, writer, ads_on, comment):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    add_comment(writer, pid, **comment)
    page = html(get(app.test_client(), f"/post/{pid}"))
    assert boxes(page) == 0 and "googlesyndication" not in page


def test_a_post_with_nothing_to_read_has_no_box_until_someone_comments(real_app, writer, ads_on):
    app, _ = real_app
    pid = add_post(writer, body="")
    assert boxes(html(get(app.test_client(), f"/post/{pid}"))) == 0
    add_comment(writer, pid)
    assert boxes(html(get(app.test_client(), f"/post/{pid}"))) >= 1


def test_a_guild_page_one_unsafe_card_takes_every_box(real_app, writer, ads_on):
    app, _ = real_app
    guild_id, name = add_guild(real_app)
    add_post(writer, board_id=guild_id)
    page = html(get(app.test_client(), f"/+{name}"))
    assert '<template id="feed-ad">' in page and 'data-ad-safe="1"' in page and 'data-ad-safe="0"' not in page
    add_post(writer, board_id=guild_id, is_sensitive=True)
    page = html(get(app.test_client(), f"/+{name}"))
    assert boxes(page) == 0 and "googlesyndication" not in page


def test_a_card_the_continuous_feed_will_bring_is_marked(real_app, writer, ads_on):
    app, _ = real_app
    guild_id, name = add_guild(real_app)
    add_post(writer, board_id=guild_id, is_sensitive=True)
    page = html(get(app.test_client(), f"/+{name}?sort=new"))
    assert 'data-ad-safe="0"' in page


def test_a_search_has_no_box(real_app, writer, ads_on):
    app, _ = real_app
    add_post(writer, title="Searchable words about ads")
    page = html(get(app.test_client(), "/search?q=searchable"))
    assert boxes(page) == 0 and "googlesyndication" not in page


def test_a_child_account_has_no_box_and_no_tag(real_app, make_member, writer, ads_on):
    child = make_member()
    set_answer(child, 2)
    pid = add_post(writer)
    add_comment(writer, pid)
    client = child.browser()
    page = html(get(client, f"/post/{pid}"))
    assert boxes(page) == 0 and "googlesyndication" not in page
    # the privacy page doesn't load the tag for them either, until they press the link
    privacy = html(get(client, "/help/privacy"))
    assert 'tag.src = "https://pagead2' in privacy and "if (!window.loadAdTag())" not in privacy


def test_premium_has_no_box_and_no_tag_on_reading_the_privacy_page(real_app, make_member, writer, ads_on):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET premium_expires_utc = :t WHERE id = :u"), {"t": int(time.time()) + 86400, "u": member.id})
    db.commit()
    pid = add_post(writer)
    add_comment(writer, pid)
    client = member.browser()
    assert boxes(html(get(client, f"/post/{pid}"))) == 0
    assert "if (!window.loadAdTag())" not in html(get(client, "/help/privacy"))


def test_a_visitor_and_an_ordinary_member_load_it_on_the_privacy_page(real_app, make_member, ads_on):
    app, _ = real_app
    assert "if (!window.loadAdTag())" in html(get(app.test_client(), "/help/privacy"))
    assert "if (!window.loadAdTag())" in html(get(make_member().browser(), "/help/privacy"))


@pytest.mark.parametrize("answer,country,personal", [(1, "US", True), (1, "JP", True), (1, "GB", False), (1, "DE", False),
                                                      (1, "CH", False), (1, None, False), (1, "T1", False), (1, "XX", False),
                                                      (None, "US", False), (None, "GB", False)])
def test_a_member_is_personalised_only_outside_the_regions_and_having_said_18(real_app, writer, make_member, ads_on, answer, country, personal):
    member = make_member()
    set_answer(member, answer)
    pid = add_post(writer)
    add_comment(writer, pid)
    page = html(get(member.browser(), f"/post/{pid}", country=country))
    assert boxes(page) >= 1
    assert (NON_PERSONAL not in page) is personal


@pytest.mark.parametrize("country", [None, "US", "GB", "T1"])
def test_a_visitor_is_never_personalised_so_a_visitors_page_doesnt_depend_on_the_country(real_app, writer, ads_on, country):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    answer = get(app.test_client(), f"/post/{pid}", country=country)
    assert NON_PERSONAL in html(answer)
    assert "public" in answer.headers["Cache-Control"]
    vary = answer.headers["Vary"]
    assert "CF-IPCountry" in vary and "Cookie" in vary       # the banner's question depends on it, so it is covered anyway


def test_a_members_page_is_private_and_covered_by_vary(real_app, writer, make_member, ads_on):
    pid = add_post(writer)
    add_comment(writer, pid)
    answer = get(make_member().browser(), f"/post/{pid}", country="US")
    assert "private" in answer.headers["Cache-Control"] and "public" not in answer.headers["Cache-Control"]
    assert "CF-IPCountry" in answer.headers["Vary"] and "Cookie" in answer.headers["Vary"]


def sign_in(client, member):
    return client.post("/login", base_url=BASE, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": address()},
                       data={"username": member.username, "password": member.password})


def test_a_password_sign_in_holds_the_tag_for_fifteen_minutes(real_app, writer, make_member, ads_on):
    app, _ = real_app
    member = make_member()
    pid = add_post(writer)
    add_comment(writer, pid)
    client = app.test_client()
    assert sign_in(client, member).status_code == 302
    with client.session_transaction() as session:
        until = session["no_ads_until"]
    assert 14 * 60 < until - time.time() <= 15 * 60
    answer = get(client, f"/post/{pid}")
    page = html(answer)
    assert boxes(page) == 0 and "googlesyndication" not in page and "private" in answer.headers["Cache-Control"]
    assert "googlesyndication" not in html(get(client, "/help/privacy"))
    with client.session_transaction() as session:
        session["no_ads_until"] = int(time.time()) - 1
    assert boxes(html(get(client, f"/post/{pid}"))) >= 1


def test_no_hold_is_written_while_ads_are_off(real_app, make_member):
    app, _ = real_app
    client = app.test_client()
    assert sign_in(client, make_member()).status_code == 302
    with client.session_transaction() as session:
        assert "no_ads_until" not in session


def test_the_hold_goes_at_log_out_and_never_reaches_a_page_a_cache_may_keep(real_app, writer, make_member, ads_on):
    app, _ = real_app
    member = make_member()
    pid = add_post(writer)
    add_comment(writer, pid)
    client = app.test_client()
    sign_in(client, member)
    with client.session_transaction() as session:
        session["user_id"], session["session_id"] = None, None     # what /logout writes
    answer = get(client, f"/post/{pid}")
    assert "public" in answer.headers["Cache-Control"] and boxes(html(answer)) >= 1
    with client.session_transaction() as session:
        assert "no_ads_until" not in session


def test_a_sign_in_ended_elsewhere_reads_as_a_visitor_whatever_the_cookie_holds(real_app, writer, make_member, ads_on):
    """`user_id` still in the cookie, the nonce moved: drawn for a visitor, public, so the hold must not change it."""
    app, _ = real_app
    member = make_member()
    pid = add_post(writer)
    add_comment(writer, pid)
    client = app.test_client()
    sign_in(client, member)
    db = member.db_session()
    db.execute(text("UPDATE users SET login_nonce = login_nonce + 1 WHERE id = :u"), {"u": member.id})
    db.commit()
    answer = get(client, f"/post/{pid}")
    assert "public" in answer.headers["Cache-Control"]
    assert boxes(html(answer)) >= 1 and NON_PERSONAL in html(answer)


def visitor_sees(app, path):
    page = html(get(app.test_client(), path))
    return boxes(page), "googlesyndication" in page, page


def test_a_post_its_guilds_moderators_removed_has_no_box_and_no_tag(real_app, writer, ads_on):
    app, _ = real_app
    guild_id, _ = add_guild(real_app)
    pid = add_post(writer, board_id=guild_id, title="Removed by the guild's moderators", hidden_by_guild=True)
    assert visitor_sees(app, f"/post/{pid}")[:2] == (0, False)


def add_collab(member, pid, *titles):
    """A published collaborative post, with one revision per title. Only its own page's text is judged, so a page that shows an old one has no ad."""
    db = member.db_session()
    db.rollback()
    now = int(time.time())
    doc = db.execute(text("INSERT INTO collab_docs (creator_id, title, body, revision, post_id, published_revision, created_utc, updated_utc) "
                          "VALUES (:u, 'A draft', 'Words', 1, :p, 1, :n, :n) RETURNING id"), {"u": member.id, "p": int(pid, 36), "n": now}).scalar()
    db.execute(text("INSERT INTO collab_authors (doc_id, user_id, joined_utc) VALUES (:d, :u, :n)"), {"d": doc, "u": member.id, "n": now})
    for number, title in enumerate(titles, 1):
        db.execute(text("INSERT INTO collab_publishes (doc_id, number, revision, title, body, authorship, published_by, published_utc) "
                        "VALUES (:d, :num, :num, :t, 'Words', '{\"title\": [], \"body\": []}', :u, :n)"),
                   {"d": doc, "num": number, "t": title, "u": member.id, "n": now})
    db.commit()


def test_a_shared_drafts_pages_have_no_box_and_no_tag_though_the_post_is_fit(real_app, writer, ads_on):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    add_collab(writer, pid, "RUDEOLDTITLE", "A clean title")
    for path in (f"/post/{pid}/revisions", f"/post/{pid}/revisions/2", f"/post/{pid}/revisions/1", f"/post/{pid}/written_by?revision=1"):
        got, tag, page = visitor_sees(app, path)
        assert (got, tag) == (0, False), path
    assert "RUDEOLDTITLE" in page       # the page really does show it
    assert visitor_sees(app, f"/post/{pid}")[0] >= 1      # and the post's own page keeps its box
    member_page = html(get(writer.browser(), f"/collab/fork/{pid}"))
    assert boxes(member_page) == 0 and "googlesyndication" not in member_page


def test_a_comments_own_address_is_a_post_page_too(real_app, writer, ads_on):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    db = writer.db_session()
    cid = db.execute(text("SELECT id FROM comments WHERE parent_submission = :p"), {"p": int(pid, 36)}).scalar()
    from ruqqus.helpers.base36 import base36encode
    assert visitor_sees(app, f"/post/{pid}/x/{base36encode(cid)}")[0] >= 1


def link_to(app, pid):
    return f"https://{app.config['SERVER_NAME']}/post/{pid}"


@pytest.mark.parametrize("over", [{"word_severity": 1}, {"is_sensitive": True}])
def test_a_post_quoting_an_unfit_post_has_no_box(real_app, writer, ads_on, over):
    app, _ = real_app
    quoted = add_post(writer, title="The words being quoted", **over)
    pid = add_post(writer, url=link_to(app, quoted))
    add_comment(writer, pid)
    got, tag, page = visitor_sees(app, f"/post/{pid}")
    assert "quote-card" in page and (got, tag) == (0, False)


def test_a_quote_inside_a_quote_counts_and_a_fit_one_doesnt_take_the_box(real_app, writer, ads_on):
    app, _ = real_app
    rude = add_post(writer, title="Rude words", word_severity=1)
    between = add_post(writer, url=link_to(app, rude))
    outer = add_post(writer, url=link_to(app, between))
    add_comment(writer, outer)
    assert visitor_sees(app, f"/post/{outer}")[:2] == (0, False)
    fit = add_post(writer, title="Fair words")
    pid = add_post(writer, url=link_to(app, fit))
    add_comment(writer, pid)
    got, _, page = visitor_sees(app, f"/post/{pid}")
    assert "quote-card" in page and "Fair words" in page and got >= 1


@pytest.mark.parametrize("over", [{"deleted_utc": 5}, {"word_severity": 2}])      # gone, or hidden by a visitor's Standard filter
def test_a_quote_that_shows_only_a_notice_doesnt_take_the_box(real_app, writer, ads_on, over):
    app, _ = real_app
    gone = add_post(writer, title="Words that went", **over)
    pid = add_post(writer, url=link_to(app, gone))
    add_comment(writer, pid)
    got, _, page = visitor_sees(app, f"/post/{pid}")
    assert "Words that went" not in page and got >= 1


def test_a_card_quoting_an_unfit_post_counts_on_a_page_of_cards_and_is_marked_for_the_continuous_feed(real_app, writer, ads_on):
    app, _ = real_app
    guild_id, name = add_guild(real_app)
    add_post(writer, board_id=guild_id)
    assert visitor_sees(app, f"/+{name}?sort=new")[0] >= 1
    rude = add_post(writer, title="Rude words", word_severity=1)
    add_post(writer, board_id=guild_id, url=link_to(app, rude))
    got, tag, page = visitor_sees(app, f"/+{name}?sort=new")
    assert (got, tag) == (0, False) and 'data-ad-safe="0"' in page


def add_live_thread(member, pid, ended=True, update=None, severity=0, summary="", summary_severity=0):
    db = member.db_session()
    db.rollback()
    now = int(time.time())
    db.execute(text("INSERT INTO live_threads (post_id, started_utc, ended_utc, last_update_utc, started_by_id, ends_utc, summary, summary_html, summary_severity) "
                    "VALUES (:p, :s, :e, :s, :u, :until, :sum, :sum, :sev)"),
               {"p": int(pid, 36), "s": now - 3600, "e": now - 60 if ended else 0, "u": member.id, "until": now + 3600, "sum": summary, "sev": summary_severity})
    if update:
        db.execute(text("INSERT INTO live_updates (post_id, author_id, body, body_html, word_severity, created_utc, edited_utc, deleted_utc) "
                        "VALUES (:p, :u, 'An update', :html, :sev, :n, 0, 0)"), {"p": int(pid, 36), "u": member.id, "html": update, "sev": severity, "n": now - 120})
    db.commit()


@pytest.mark.parametrize("thread,fit", [
    ({"update": "<p>A fair update.</p>", "summary": "<p>A fair summary.</p>"}, True),
    ({"update": "<p>A rude update.</p>", "severity": 1}, False),
    ({"update": '<p><img src="/media/5/abc.png"></p>'}, False),
    ({"summary": "<p>A rude summary.</p>", "summary_severity": 2}, False),
    ({"summary": '<p><img src="/media/5/abc.png"></p>'}, False),
    ({"ended": False, "update": "<p>A fair update.</p>"}, False),
])
def test_a_live_threads_page_by_what_it_draws_and_none_while_it_is_live(real_app, writer, ads_on, thread, fit):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    add_live_thread(writer, pid, **thread)
    got, tag, page = visitor_sees(app, f"/post/{pid}")
    assert "live-panel" in page and tag is fit and (got >= 1) is fit


def test_a_sensitive_accounts_comment_takes_the_box_unless_it_is_anonymous(real_app, writer, make_member, ads_on):
    app, _ = real_app
    sensitive = make_member()
    db = sensitive.db_session()
    db.execute(text("UPDATE users SET is_sensitive = true WHERE id = :u"), {"u": sensitive.id})
    db.commit()
    pid = add_post(writer)
    add_comment(writer, pid)
    add_comment(sensitive, pid, is_anonymous=True)
    assert visitor_sees(app, f"/post/{pid}")[0] >= 1
    add_comment(sensitive, pid)
    assert visitor_sees(app, f"/post/{pid}")[:2] == (0, False)


def test_a_picture_typed_with_the_emojis_class_through_the_real_route_takes_the_box(real_app, writer, make_member, ads_on):
    app, _ = real_app
    pid = add_post(writer)
    add_comment(writer, pid)
    assert visitor_sees(app, f"/post/{pid}")[0] >= 1
    commenter = make_member()
    here = commenter.browser()
    answer = here.post("/api/comment", base_url=BASE, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": address()},
                       data={"formkey": commenter.formkey(here), "parent_fullname": f"t2_{pid}", "submission": pid,
                             "body": 'look <img class="emoji" src="/media/5/abc.png">'})
    assert answer.status_code == 200, answer.get_data(as_text=True)[:300]
    got, tag, page = visitor_sees(app, f"/post/{pid}")
    assert 'class="emoji" src="/media/5/abc.png"' in page and (got, tag) == (0, False)


def test_a_password_change_holds_the_tag_as_a_sign_in_does(real_app, writer, make_member, ads_on):
    """The form left a new pre-key for chat (assets/js/chat_key.js), and the tag would take it on the next page."""
    member = make_member()
    pid = add_post(writer)
    add_comment(writer, pid)
    client = member.browser()
    assert boxes(html(get(client, f"/post/{pid}"))) >= 1
    answer = post_form(client, "/settings/security", member, old_password=member.password, new_password="ci-Password-2", cnf_password="ci-Password-2")
    assert answer.status_code == 302
    with client.session_transaction() as session:
        assert 14 * 60 < session["no_ads_until"] - time.time() <= 15 * 60
    page = html(get(client, f"/post/{pid}"))
    assert boxes(page) == 0 and "googlesyndication" not in page
    # another change of settings isn't one
    other = make_member()
    client = other.browser()
    post_form(client, "/settings/security", other, old_password="wrong", new_password="ci-Password-2", cnf_password="ci-Password-2")
    with client.session_transaction() as session:
        assert "no_ads_until" not in session


def test_the_page_says_whose_chat_may_be_in_the_browser(real_app, writer, make_member, ads_on):
    """Another account's session holds the tag on a member's page too (tests/test_ads_assets.py runs the check). A visitor's page, which a cache may keep, names nobody."""
    from ruqqus.helpers import matrix_client
    app, _ = real_app
    member = make_member()
    pid = add_post(writer)
    add_comment(writer, pid)
    page = html(get(member.browser(), f"/post/{pid}"))
    held = 'localStorage.getItem("ruqqus_call_session")); } catch (e) { return false; } })(%s)'
    mxid = matrix_client.mxid_for(member.id)
    assert page.count(held % f'"{mxid}"') == 2         # the post's box, and the tag
    page = html(get(app.test_client(), f"/post/{pid}"))
    assert page.count(held % "null") == 2 and mxid not in page


def post_row(member, pid):
    from ruqqus.classes import Submission
    db = member.db_session()
    db.expire_all()
    return db.query(Submission).filter_by(id=int(pid, 36)).first()


def admin_of(make_member):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET admin_level = 3 WHERE id = :u"), {"u": member.id})
    db.commit()
    return member


def post_form(client, path, member, **form):
    return client.post(path, base_url=BASE, headers={"User-Agent": AGENT}, environ_base={"REMOTE_ADDR": address()},
                       data={"formkey": member.formkey(client), **form})


def test_an_admin_forces_a_post_sensitive_and_its_author_cant_take_it_off(real_app, writer, make_member):
    admin = admin_of(make_member)
    pid = add_post(writer)
    here = admin.browser()
    answer = post_form(here, "/admin/sensitive/post", admin, post=pid, force="1")
    assert answer.status_code == 302
    row = post_row(writer, pid)
    assert row.is_sensitive is True and row.sensitive_forced is True
    author = writer.browser()
    refused = post_form(author, f"/api/toggle_post_sensitive/{pid}", writer)
    assert refused.status_code == 403 and "error" in refused.get_json()
    assert post_row(writer, pid).is_sensitive is True
    # the Edit form can't take it off either
    post_form(author, f"/edit_post/{pid}", writer, title="An edited title", body="An edited body")
    assert post_row(writer, pid).is_sensitive is True
    db = writer.db_session()
    told = db.execute(text("SELECT count(*) FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
                           "WHERE n.user_id = :u AND a.notice_msgid LIKE 'An admin marked [your post]%'"), {"u": writer.id}).scalar()
    assert told == 1
    # unlocking gives the choice back, and leaves the mark until it is used
    assert post_form(here, "/admin/sensitive/post", admin, post=pid, force="0").status_code == 302
    row = post_row(writer, pid)
    assert row.is_sensitive is True and row.sensitive_forced is False
    assert post_form(author, f"/api/toggle_post_sensitive/{pid}", writer).status_code == 204
    assert post_row(writer, pid).is_sensitive is False


def test_the_mark_reaches_every_copy_and_a_copy_reads_the_lock_of_its_original(real_app, writer, make_member):
    admin = admin_of(make_member)
    guild_id, _ = add_guild(real_app)
    pid = add_post(writer)
    copy = add_post(writer, board_id=guild_id, repost_id=int(pid, 36))
    assert post_form(admin.browser(), "/admin/sensitive/post", admin, post=copy, force="1").status_code == 302
    assert post_row(writer, pid).sensitive_forced is True and post_row(writer, copy).is_sensitive is True
    assert post_form(writer.browser(), f"/api/toggle_post_sensitive/{copy}", writer).status_code == 403


@pytest.mark.parametrize("level", [0, 2])
def test_only_an_admin_at_level_3_forces_it(real_app, writer, make_member, level):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET admin_level = :l WHERE id = :u"), {"l": level, "u": member.id})
    db.commit()
    pid = add_post(writer)
    assert post_form(member.browser(), "/admin/sensitive/post", member, post=pid, force="1").status_code in (401, 403, 404)
    assert post_row(writer, pid).is_sensitive is False


def test_the_form_is_on_the_post_page_for_an_admin_only(real_app, writer, make_member):
    admin = admin_of(make_member)
    pid = add_post(writer)
    assert 'action="/admin/sensitive/post"' in html(get(admin.browser(), f"/post/{pid}"))
    assert 'action="/admin/sensitive/post"' not in html(get(writer.browser(), f"/post/{pid}"))
