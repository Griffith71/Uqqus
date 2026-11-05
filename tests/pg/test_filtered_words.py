"""The members' page that lists the word filter's words, on the real app and Postgres (routes/filtered_words_pages.py):
a visitor is sent to sign in, a member sees the warning and then, with ?show=1, the live list (an admin's new word at
once, a switched-off one never), a member on the Child filter sees no words, and every answer is private, no-store and
noindex. The words are made-up stand-ins, as in the other word filter tests."""
import secrets
import time

from sqlalchemy import text

BASE = "http://localhost"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    return client.get(path, base_url=BASE, headers={"User-Agent": "ci-filtered-words/1.0 (Test)"},
                      environ_base={"REMOTE_ADDR": address()})


def private(response):
    assert response.headers.get_all("Cache-Control") == ["private, no-store"]
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"


def seed(member, **words):
    """Rows in word_filter_entries ({word: (severity, enabled, language)}), taken out again by the caller."""
    db = member.db_session()
    for word, (severity, enabled, language) in words.items():
        db.execute(text("INSERT INTO word_filter_entries (word, severity, mode, variants, suffixes, enabled, note, "
                        "created_utc, language) VALUES (:w, :s, 'word', :v, '', :e, 'ci', :t, :l)"),
                   {"w": word, "s": severity, "v": word + "zz", "e": enabled, "t": int(time.time()), "l": language})
    db.commit()


def unseed(member, *words):
    db = member.db_session()
    db.execute(text("DELETE FROM word_filter_entries WHERE word = ANY(:w)"), {"w": list(words)})
    db.commit()


def test_a_visitor_is_sent_to_sign_in(real_app):
    app, _ = real_app
    answer = get(app.test_client(), "/filtered_words?show=1")
    assert answer.status_code == 302
    assert "/login?redirect=" in answer.headers["Location"] and "filtered_words" in answer.headers["Location"]


def test_a_member_sees_the_warning_first_then_the_live_list(make_member):
    member = make_member()
    tag = secrets.token_hex(3)
    shown, hidden, russian = f"ciworda{tag}", f"ciwordb{tag}", f"цикл{tag}"
    seed(member, **{shown: (2, True, None), hidden: (1, False, None), russian: (1, True, "ru")})
    try:
        here = member.browser()
        warning = get(here, "/filtered_words")
        assert warning.status_code == 200
        private(warning)
        page = warning.get_data(as_text=True)
        assert "This page lists offensive words" in page and 'href="?show=1"' in page
        assert '<meta name="robots" content="noindex, nofollow">' in page
        assert shown not in page                                     # the warning shows no words

        listing = get(here, "/filtered_words?show=1")
        assert listing.status_code == 200
        private(listing)
        page = listing.get_data(as_text=True)
        assert f'<span class="fw-term" lang="en" dir="auto">{shown}</span>' in page
        assert f'<span class="fw-term" lang="ru" dir="auto">{russian}</span>' in page
        assert f"{shown}zz" in page                                  # its other spelling
        assert hidden not in page                                    # switched off: never listed
        assert 'id="lang-ru"' in page and 'href="#lang-ru"' in page
        assert "Hidden on the Standard and Child filters" in page and "Hidden on the Child filter" in page
        assert "ad-slot" not in page
    finally:
        unseed(member, shown, hidden, russian)


def test_a_word_is_escaped(make_member):
    member = make_member()
    word = f"<b>ci{secrets.token_hex(3)}"
    seed(member, **{word: (1, True, None)})
    try:
        page = get(member.browser(), "/filtered_words?show=1").get_data(as_text=True)
        assert word not in page and word.replace("<", "&lt;").replace(">", "&gt;") in page
    finally:
        unseed(member, word)


def test_a_locked_account_is_told_to_confirm_its_age_not_to_change_its_filter(make_member):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET age_declared = 2, age_declared_utc = 5, filter_level = 2, age_lock = 1 WHERE id = :u"), {"u": member.id})
    db.commit()
    page = get(member.browser(), "/filtered_words").get_data(as_text=True)
    assert "Change your word filter" not in page
    assert "its word filter stays on Child until you confirm your age in Settings." in page

    # a member who chose the Child filter themselves can still change it, and is told so
    other = make_member()
    db = other.db_session()
    db.execute(text("UPDATE users SET filter_level = 2 WHERE id = :u"), {"u": other.id})
    db.commit()
    assert "Change your word filter" in get(other.browser(), "/filtered_words").get_data(as_text=True)


def test_a_member_on_the_child_filter_sees_no_words(make_member):
    member = make_member()
    db = member.db_session()
    db.execute(text("UPDATE users SET filter_level = 2 WHERE id = :u"), {"u": member.id})
    db.commit()
    word = f"ciwordc{secrets.token_hex(3)}"
    seed(member, **{word: (2, True, None)})
    try:
        for path in ("/filtered_words", "/filtered_words?show=1"):
            answer = get(member.browser(), path)
            assert answer.status_code == 200
            private(answer)
            page = answer.get_data(as_text=True)
            assert "The list isn't shown on the Child filter" in page or "The list isn&#39;t shown" in page
            assert word not in page and 'class="fw-word"' not in page
            assert 'href="/settings/content"' in page
    finally:
        unseed(member, word)


def test_settings_links_to_it(make_member):
    page = get(make_member().browser(), "/settings/content").get_data(as_text=True)
    assert 'href="/filtered_words"' in page
