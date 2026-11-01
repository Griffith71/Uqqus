"""What the video consent banner hangs on, on the real app (helpers/consent.py, CLAUDE.md "Video consent"): <html data-video-consent> from the country and the
cookie, the Vary header, and what the privacy page tells each reader. scripts/ui/consent.spec.ts watches a real browser."""
import re
import secrets
import time

BASE = "http://localhost"


def addr():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path, country=None):
    headers = {"User-Agent": "ci-consent"}
    if country:
        headers["CF-IPCountry"] = country
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": addr()})


def consent_of(answer):
    html = answer.get_data(as_text=True)
    tag = re.search(r"<html[^>]*>", html).group(0)
    found = re.search(r'data-video-consent="([^"]*)"', tag)
    return found.group(1) if found else None


def answered(client, choice, age=0):
    client.set_cookie("video_consent", "1.%s.%d" % (choice, int(time.time()) - age), domain="localhost")


def test_someone_who_is_asked_and_someone_who_is_not(real_app):
    app, _ = real_app
    for country, expected in (("GB", "ask"), ("DE", "ask"), ("CH", "ask"), (None, "ask"), ("T1", "ask"), ("XX", "ask"), ("US", None), ("JP", None)):
        answer = get(app.test_client(), "/all", country)
        assert answer.status_code == 200
        assert consent_of(answer) == expected, country


def test_the_banner_is_drawn_on_a_page_with_players_and_asks_only_there(real_app):
    app, _ = real_app
    feed = get(app.test_client(), "/all", "GB").get_data(as_text=True)
    assert 'id="consent-banner"' in feed and 'data-consent-auto="1"' in feed and "js/consent.js" in feed
    docs = get(app.test_client(), "/help/privacy", "GB").get_data(as_text=True)
    assert 'id="consent-banner"' in docs and 'data-consent-auto="0"' in docs


def test_an_answer_is_named_wherever_the_reader_is_now(real_app):
    app, _ = real_app
    for country in ("GB", "US", None):
        for choice in ("yes", "no"):
            client = app.test_client()
            answered(client, choice)
            assert consent_of(get(client, "/all", country)) == choice, (country, choice)


def test_six_months_on_the_reader_is_asked_again(real_app):
    app, _ = real_app
    for country, choice in (("GB", "yes"), ("GB", "no"), ("US", "no")):
        client = app.test_client()
        answered(client, choice, age=184 * 86400)
        assert consent_of(get(client, "/all", country)) == "ask", (country, choice)


def test_a_yes_that_lapsed_where_nobody_is_asked_goes_back_to_what_everyone_there_has(real_app):
    app, _ = real_app
    client = app.test_client()
    answered(client, "yes", age=184 * 86400)
    assert consent_of(get(client, "/all", "US")) is None


def test_someone_being_asked_again_is_told_so_by_the_privacy_page(real_app):
    app, _ = real_app
    client = app.test_client()
    answered(client, "no", age=184 * 86400)
    page = get(client, "/help/privacy", "US").get_data(as_text=True)
    assert "It loads only when you press play" in page and "Video players that load on scroll" not in page


def test_a_header_or_a_cookie_never_reaches_the_page(real_app):
    app, _ = real_app
    client = app.test_client()
    client.set_cookie("video_consent", '1.yes"><script>alert(1)</script>', domain="localhost")
    answer = get(client, "/all", '"><script>alert(2)</script>')
    assert consent_of(answer) == "ask"
    assert "alert(1)" not in answer.get_data(as_text=True) and "alert(2)" not in answer.get_data(as_text=True)


def test_pages_say_they_differ_by_country_and_an_asset_does_not(real_app):
    app, _ = real_app
    vary = get(app.test_client(), "/all", "GB").headers.get("Vary", "")
    assert "CF-IPCountry" in vary and "Accept-Language" in vary and "Cookie" in vary
    assert "CF-IPCountry" not in get(app.test_client(), "/assets/js/consent.js").headers.get("Vary", "")
    assert "CF-IPCountry" not in get(app.test_client(), "/healthz").headers.get("Vary", "")


def test_the_privacy_page_tells_each_reader_what_happens_to_them(real_app):
    app, _ = real_app
    asked = get(app.test_client(), "/help/privacy", "GB").get_data(as_text=True)
    assert "Videos from other sites" in asked and "It loads only when you press play" in asked
    assert "Video players that load on scroll" not in asked and "before you press anything" not in asked
    elsewhere = get(app.test_client(), "/help/privacy", "US").get_data(as_text=True)
    assert "Videos from other sites" in elsewhere and "Video players that load on scroll" in elsewhere
    assert "It loads only when you press play" not in elsewhere
    for page in (asked, elsewhere):
        assert "data-consent-open" in page and "video_consent" in page


def test_a_member_is_asked_too_and_a_side_panels_frame_has_no_banner(real_app, make_member):
    member = make_member()
    client = member.browser()
    page = get(client, "/all", "FR")
    assert consent_of(page) == "ask" and 'id="consent-banner"' in page.get_data(as_text=True)
    assert 'data-consent-open' in page.get_data(as_text=True)
    assert 'id="consent-banner"' not in get(client, "/notifications?embed=1", "FR").get_data(as_text=True)


# --- POST /consent/video: the server sets the same cookie, and stores nothing -------------------------------------------------------------------------

ANSWER = "/consent/video"


def answer(client, data=None, base=BASE, origin=None, fetch_site="same-origin", address=None, **kwargs):
    headers = {"User-Agent": "ci-consent"}
    if origin:
        headers["Origin"] = origin
    if fetch_site:
        headers["Sec-Fetch-Site"] = fetch_site
    return client.post(ANSWER, base_url=base, headers=headers, data={"choice": "yes"} if data is None else data,
                       environ_base={"REMOTE_ADDR": address or addr()}, **kwargs)


def set_cookies(reply):
    return reply.headers.getlist("Set-Cookie")


def test_the_answer_is_set_from_the_server_with_the_same_attributes(real_app):
    app, _ = real_app
    for choice in ("yes", "no"):
        before = int(time.time())
        reply = answer(app.test_client(), {"choice": choice}, origin=BASE)
        assert reply.status_code == 204 and reply.get_data() == b""
        written = set_cookies(reply)
        assert len(written) == 1, written
        parts = [p.strip() for p in written[0].split(";")]
        name, value = parts[0].split("=", 1)
        version, said, date = value.split(".")
        assert (name, version, said) == ("video_consent", "1", choice)
        assert before <= int(date) <= int(time.time())                                 # its own clock, never the browser's
        attributes = {p.split("=")[0].lower(): (p.split("=") + [""])[1] for p in parts[1:]}
        assert attributes["max-age"] == str(400 * 86400) and attributes["path"] == "/" and attributes["samesite"] == "Lax"
        assert "domain" not in attributes and "httponly" not in attributes             # the script reads it and may write over it
        assert "secure" not in attributes                                              # the page is on http here
        assert reply.headers["Cache-Control"] == "no-store"


def test_it_is_secure_when_the_page_is_on_https(real_app):
    app, _ = real_app
    assert "Secure" in set_cookies(answer(app.test_client(), base="https://localhost", origin="https://localhost"))[0]
    # a proxy that says nothing about the scheme: the browser's Origin still does
    assert "Secure" in set_cookies(answer(app.test_client(), origin="https://localhost"))[0]
    assert "Secure" not in set_cookies(answer(app.test_client(), origin="http://localhost"))[0]


def test_the_next_page_reads_it_wherever_the_reader_is(real_app):
    app, _ = real_app
    for country in ("GB", "US", None):
        for choice in ("yes", "no"):
            client = app.test_client()
            assert answer(client, {"choice": choice}).status_code == 204
            assert consent_of(get(client, "/all", country)) == choice, (country, choice)


def test_a_request_from_another_site_is_refused(real_app):
    app, _ = real_app
    for origin, fetch_site in (("https://evil.example", "cross-site"), ("https://evil.example", None), (None, "cross-site"), ("null", "cross-site"), ("null", None),
                               ("https://localhost.evil.example", "same-site")):
        client = app.test_client()
        reply = answer(client, origin=origin, fetch_site=fetch_site)
        assert reply.status_code == 403, (origin, fetch_site)
        assert set_cookies(reply) == [] and reply.get_data() == b"" and reply.headers["Cache-Control"] == "no-store"
        assert consent_of(get(client, "/all", "GB")) == "ask"
    # a request with neither header is no browser on another site (helpers/same_origin.py), and sets only its own cookie
    assert answer(app.test_client(), fetch_site=None).status_code == 204


def test_only_yes_or_no_makes_a_cookie(real_app):
    app, _ = real_app
    for data in ({}, {"choice": ""}, {"choice": "maybe"}, {"choice": "YES"}, {"choice": " yes"}, {"choice": "ask"}, {"choice": "1.yes.1800000000"}, {"other": "yes"},
                 {"choice": "yes;Domain=e"}, {"choice": "yes\r\nSet-Cookie: x=1"}, {"choice": ["yes", "no"]}, {"choice": "y" * 500}):
        client = app.test_client()
        reply = answer(client, data)
        assert reply.status_code == 400, data
        assert set_cookies(reply) == [] and reply.headers["Cache-Control"] == "no-store"
    # the answer travels in the body and nowhere else, so it is in no address and no access log
    assert answer(app.test_client(), {}, query_string={"choice": "yes"}).status_code == 400
    assert answer(app.test_client(), data="choice=yes", content_type="text/plain").status_code == 400
    # GET does nothing, and is an ordinary 405 rather than a failure
    reply = app.test_client().get(ANSWER, base_url=BASE, query_string={"choice": "yes"}, environ_base={"REMOTE_ADDR": addr()})
    assert reply.status_code == 405 and set_cookies(reply) == []


def test_it_writes_no_session_cookie_for_a_visitor_or_a_member(real_app, make_member):
    app, _ = real_app
    visitor = app.test_client()
    assert [c.split("=")[0] for c in set_cookies(answer(visitor))] == ["video_consent"]
    assert visitor.get_cookie("session_ruqqus") is None
    client = make_member().browser()
    held = client.get_cookie("session_ruqqus").value
    assert [c.split("=")[0] for c in set_cookies(answer(client))] == ["video_consent"]
    assert client.get_cookie("session_ruqqus").value == held
    assert set_cookies(answer(client, {"choice": "maybe"})) == []


def test_it_never_asks_the_database(real_app, make_member):
    """Every statement any connection sends while it answers: none, whatever the answer, for a visitor or a member, and on the 405 and the 429 pages."""
    from sqlalchemy import event
    from ruqqus.__main__ import _engine
    app, _ = real_app
    client = make_member().browser()
    address = addr()
    statements = []

    def note(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(_engine, "before_cursor_execute", note)
    try:
        replies = [answer(c, data, origin=origin, address=address) for c in (app.test_client(), client)
                   for data, origin in (({"choice": "yes"}, BASE), ({"choice": "no"}, None), ({"choice": "maybe"}, None), ({"choice": "yes"}, "https://evil.example"))]
        assert [r.status_code for r in replies] == [204, 204, 400, 403] * 2
        assert app.test_client().get(ANSWER, base_url=BASE, environ_base={"REMOTE_ADDR": addr()}).status_code == 405
        crowded = addr()
        last = [answer(app.test_client(), address=crowded).status_code for _ in range(31)][-1]
        assert last == 429
        assert statements == []
        get(app.test_client(), "/all", "GB")
        assert statements, "a page does ask, so the listener can tell"
    finally:
        event.remove(_engine, "before_cursor_execute", note)


def test_it_is_limited_per_address_and_a_limited_answer_sets_nothing(real_app, make_member):
    app, _ = real_app
    address = addr()
    client = make_member().browser()
    for _ in range(30):
        assert answer(app.test_client(), address=address).status_code == 204
    limited = answer(client, address=address)
    assert limited.status_code == 429 and set_cookies(limited) == []                     # no cookie of any kind, the member's session included
    assert answer(app.test_client(), address=addr()).status_code == 204                  # someone else's address is untouched


def assert_bare(reply):
    assert reply.status_code == 429
    assert reply.get_data() == b"" and reply.headers["Cache-Control"] == "no-store"


def test_a_limited_answer_is_bare_and_still_counts_toward_the_time_out(real_app, make_member):
    """The script ignores the body, so this route never draws the site's 429 page (some 230 KB), and it never asks the database for one, for a member either."""
    from sqlalchemy import event
    from ruqqus.__main__ import _engine, r
    app, _ = real_app
    address = addr()
    client = make_member().browser()
    statements = []

    def note(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(_engine, "before_cursor_execute", note)
    try:
        replies = [answer(client, address=address) for _ in range(31)]
    finally:
        event.remove(_engine, "before_cursor_execute", note)
    assert replies[-1].status_code == 429, [x.status_code for x in replies]
    assert_bare(replies[-1])
    assert statements == []
    try:
        # thirty of them without a break is the time out, as anywhere on the site; and the one that starts it is bare too
        limited = [answer(app.test_client(), address=address) for _ in range(29)]
        assert_bare(limited[-1])
        assert r.get(f"ban_ip_{address}")
        assert_bare(answer(app.test_client(), address=address))
    finally:
        r.delete(f"ban_ip_{address}")
        r.delete(f"429_count_{address}")


def test_an_address_in_time_out_waits_and_read_only_mode_does_not_stop_it(real_app):
    from ruqqus.__main__ import r
    app, _ = real_app
    address = addr()
    r.set(f"ban_ip_{address}", 1)
    try:
        reply = answer(app.test_client(), address=address)
        assert_bare(reply)
        assert set_cookies(reply) == []
    finally:
        r.delete(f"ban_ip_{address}")
    app.config["READ_ONLY"] = True
    try:
        assert answer(app.test_client()).status_code == 204
    finally:
        app.config["READ_ONLY"] = False


# --- asked at sign-up ------------------------------------------------------------------------------------------------------------

def _sign_up_form(client, agent, **fields):
    import hmac
    import os
    now = str(int(time.time()) - 60)
    token = secrets.token_hex(16)
    with client.session_transaction() as session:
        session["signup_token"] = token
    key = hmac.new(key=bytes(os.environ["MASTER_KEY"], "utf-16"), msg=bytes(now + token + agent, "utf-16"), digestmod="md5").hexdigest()
    name = "ci" + "".join(secrets.choice("abcdefghjkmnoprstuvy") for _ in range(8))
    return dict(formkey=key, now=now, username=name, password="ci-Password-1", password_confirm="ci-Password-1", age_declaration="1", **fields)


def _sign_up(client, **fields):
    agent = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0"
    form = _sign_up_form(client, agent, **fields)
    return client.post("/signup", base_url=BASE, data=form, environ_base={"REMOTE_ADDR": addr()},
                       headers={"User-Agent": agent, "Accept-Language": "en", "Origin": BASE})


def test_the_sign_up_form_asks_only_someone_who_would_be_asked(real_app):
    app, _ = real_app
    asks = get(app.test_client(), "/signup", "GB").get_data(as_text=True)
    assert 'name="video_consent" value="yes" required' in asks and 'name="video_consent" value="no" required' in asks
    assert not re.search(r'name="video_consent"[^>]*checked', asks), "nothing is ticked first"
    assert 'name="video_consent"' not in get(app.test_client(), "/signup", "US").get_data(as_text=True)
    client = app.test_client()
    answered(client, "no")
    assert 'name="video_consent"' not in get(client, "/signup", "GB").get_data(as_text=True), "they have answered"


def test_an_answer_at_sign_up_is_the_cookie_and_the_first_page_asks_nothing(real_app):
    app, _ = real_app
    for choice in ("yes", "no"):
        client = app.test_client()
        made = _sign_up(client, video_consent=choice)
        assert made.status_code == 302 and made.headers["Location"].rstrip("/") in ("", BASE)
        cookies = [c for c in made.headers.getlist("Set-Cookie") if c.startswith("video_consent=")]
        assert len(cookies) == 1 and cookies[0].startswith("video_consent=1.%s." % choice)
        assert "Max-Age=%d" % (400 * 86400) in cookies[0] and "HttpOnly" not in cookies[0] and "Domain" not in cookies[0]
        assert consent_of(get(client, "/all", "GB")) == choice


def test_sign_up_takes_only_the_two_answers(real_app):
    app, _ = real_app
    for sent in ({}, {"video_consent": "maybe"}, {"video_consent": "yes; Domain=evil.example"}):
        client = app.test_client()
        made = _sign_up(client, **sent)
        assert made.status_code == 302 and "error=" not in made.headers["Location"], "no answer doesn't stop an account being made"
        assert not [c for c in made.headers.getlist("Set-Cookie") if c.startswith("video_consent=")]
        assert consent_of(get(client, "/all", "GB")) == "ask", "and that browser is asked by the banner"
