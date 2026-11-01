"""The Preview overlay's card, on the real app and Postgres (routes/post_editor.py `post_preview`, templates/partials/post_preview.html):
the post as its own page draws it, built for the person writing it from what the composer holds. Nothing is saved, nothing in it
can run, and only a signed-in member with a form key gets it."""
import secrets

from sqlalchemy import text

BASE = "http://localhost"
AGENT = "ci-post-preview/1.0 (Test)"


def address():
    # the preview's limit (60 a minute) is per member, but the address is also kept in Redis between runs: each call its own
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def call(client, method, path, **kw):
    return getattr(client, method)(path, base_url=BASE, headers={"User-Agent": AGENT},
                                   environ_base={"REMOTE_ADDR": address()}, **kw)


def preview(member, here=None, **fields):
    here = here or member.browser()
    data = {"formkey": member.formkey(here)}
    data.update(fields)
    return call(here, "post", "/api/post_preview", data=data)


def card(member, **fields):
    response = preview(member, **fields)
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    return response.get_json()["html"]


def test_the_card_is_the_post_as_its_page_draws_it(make_member):
    member = make_member()
    html = card(member, title="A title that is long", body="one\ntwo\n\n**three**", url="https://www.example.com/a?b=1")
    assert 'class="card border-0 post-preview-card"' in html
    assert "just now" in html and member.username in html and "profile-pic-20" in html     # the byline, as the page has it
    assert "(example.com)" in html and "(text post)" not in html                           # the link's domain, without www.
    assert '<h1 dir="auto" class="card-title post-title' in html and "A title that is long" in html
    assert '<div class="post-text" dir="auto">' in html and "<br" in html and "<strong>three</strong>" in html   # an Enter is a break
    assert 'class="voting mb-auto" aria-hidden="true"' in html and 'class="post-actions mt-2 flex-grow-1"' in html


def test_a_post_with_no_link_is_a_text_post(make_member):
    html = card(make_member(), title="T", body="text")
    assert "(text post)" in html and "post-preview-lg" not in html


def test_nothing_in_it_can_run(make_member):
    member = make_member()
    html = card(member, title="Hello <script>alert(1)</script> & <b>friends</b>",
                body="<script>alert(2)</script><img src=x onerror=alert(3)>")
    assert "<script" not in html and "onerror" not in html
    # the title is what a post stores (clean_title: bleach escapes a script and keeps the harmless <b>)
    assert "Hello &lt;script&gt;alert(1)&lt;/script&gt; &amp; <b>friends</b>" in html
    # a link that would run is not made one
    link = card(member, title="T", body="[x](javascript:alert(4)) and <a href=\"javascript:alert(5)\">y</a>")
    assert 'href="javascript:alert' not in link


def test_the_flags_show_as_they_do_on_the_page(make_member):
    member = make_member()
    html = card(member, title="T", paid_partnership="true", made_with_ai="true", anonymous="true", sensitive="true")
    assert "Commercial promotion" in html and "Made with AI" in html and "sensitive content" in html
    # the writer is told who wrote their own anonymous post, as on their own page: their name, and the badge that says others see none
    assert "Anonymous" in html and member.username in html
    plain = card(member, title="T", paid_partnership="false", made_with_ai="0")
    assert "disclosure-badges" not in plain and "sensitive content" not in plain


def test_a_title_is_not_needed_to_preview_the_text(make_member):
    html = card(make_member(), title="  ", body="just the words")
    assert "<h1" not in html and "just the words" in html


def test_what_a_post_could_not_have_is_refused_as_a_post_would_be(make_member):
    member = make_member()
    assert preview(member, title="x" * 400).status_code == 400
    assert preview(member, title="T", url="not a link").status_code == 400
    assert "valid link" in preview(member, title="T", url="not a link").get_json()["error"]


def test_nothing_is_saved(make_member):
    member = make_member()
    card(member, title="Not a post", body="not a post either", url="https://example.com")
    db = member.db_session()
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM submissions WHERE author_id = :u"), {"u": member.id}).scalar() == 0


def test_only_a_signed_in_member_with_a_form_key_gets_it(make_member, real_app):
    app, _ = real_app
    member = make_member()
    visitor = call(app.test_client(), "post", "/api/post_preview", data={"title": "T"})
    assert visitor.status_code in (401, 403) or (visitor.status_code == 302 and "/login" in visitor.headers["Location"])
    here = member.browser()
    refused = call(here, "post", "/api/post_preview", data={"title": "T", "formkey": "wrong"})                      # a wrong form key
    assert refused.status_code != 200 and "post-preview-card" not in refused.get_data(as_text=True)
    assert call(here, "get", "/api/post_preview").status_code == 405
