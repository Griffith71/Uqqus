"""The words behind a comment box's switches, on the real app and Postgres (the Log's second review, C72; the markup and the script are tested
without Postgres in tests/test_switch_hints_assets.py).

The promises: on your own anonymous post the comment box says, in plain text outside the tools row (a phone draws no row), that a comment there is
anonymous and what a ban can do; it offers "comment as @me" only to you, never to a stranger, a visitor or a limited QR sign-in (a stranger to its own
anonymous items); every switch is a real checkbox, never `hidden`; the other lines wait, hidden, for their switch; and an edit form shows the line
of a flag the comment already has."""
import re
import secrets
import time
from html.parser import HTMLParser

BASE = "http://localhost"
AGENT = "ci-switch-hints"
LOG_WARNING = "the Log may name the account"


def address():
    return f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250) + 1}"


def get(client, path):
    headers = {"User-Agent": AGENT, "Accept-Language": "en", "CF-IPCountry": "US"}
    return client.get(path, base_url=BASE, headers=headers, environ_base={"REMOTE_ADDR": address()}, follow_redirects=True)


def guild(real_app):
    from ruqqus.classes import Board
    _, db_session = real_app
    db = db_session()
    board = Board(name="ci_sh_" + secrets.token_hex(3), description="a guild for the switch hint tests", description_html="<p>a guild</p>", creator_id=1,
                  subcat_id=0, is_private=False, is_circle=False, created_utc=int(time.time()), color="23a9f2")
    db.add(board)
    db.commit()
    return board.id


def add_post(member, board_id, **flags):
    from ruqqus.classes import Submission, SubmissionAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Submission(author_id=member.id, board_id=board_id, original_board_id=board_id, created_utc=int(time.time()) - 3600,
                         post_public=True, **flags)
    db.add(row)
    db.flush()
    db.add(SubmissionAux(id=row.id, title=f"Switch hints {secrets.token_hex(3)}", body="words about gardens", body_html="<p>words about gardens</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def add_comment(member, post_id, board_id, **flags):
    from ruqqus.classes import Comment, CommentAux
    from ruqqus.helpers.base36 import base36encode
    db = member.db_session()
    db.rollback()
    with member.app.test_request_context("/"):
        row = Comment(author_id=member.id, parent_submission=post_id, level=1, original_board_id=board_id, created_utc=int(time.time()) - 3600, **flags)
    db.add(row)
    db.flush()
    db.add(CommentAux(id=row.id, body="A comment.", body_html="<p>A comment.</p>"))
    db.commit()
    return row.id, base36encode(row.id)


def limited_browser(member):
    client = member.browser()
    with client.session_transaction() as session:
        session["qr_limited"] = True
        session["qr_expires_utc"] = int(time.time()) + 3600
    return client


class Elements(HTMLParser):
    """Every element of an HTML fragment: its tag, attributes (a bare one is "") and own text."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.elements, self.open = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": {k: ("" if v is None else v) for k, v in attrs}, "bare": [k for k, v in attrs if v is None], "text": ""}
        self.elements.append(element)
        if tag not in ("input", "br", "hr", "img", "meta", "link"):
            self.open.append(element)

    def handle_endtag(self, tag):
        if self.open and self.open[-1]["tag"] == tag:
            self.open.pop()

    def handle_data(self, data):
        if self.open:
            self.open[-1]["text"] += data

    def by_id(self, id):
        found = [e for e in self.elements if e["attrs"].get("id") == id]
        assert len(found) == 1, f"{id}: {len(found)} found"
        return found[0]

    def ids(self):
        return {e["attrs"]["id"] for e in self.elements if "id" in e["attrs"]}


def comment_form(page, fullname):
    """The post's own comment form, cut out of the page: from its wrapper to the end of the form."""
    start = page.index(f'id="comment-form-space-{fullname}"')
    return page[start:page.index("</form>", start) + len("</form>")]


def prefix_of(fullname):
    return f"comment-disc-{fullname}"


# ---------------------------------------------------------------- your own anonymous post

def test_on_your_own_anonymous_post_the_line_is_in_the_page_outside_the_row_and_comment_as_me_is_yours_alone(real_app, make_member):
    author, stranger = make_member(), make_member()
    board_id = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    fullname, prefix = f"t2_{base36}", prefix_of(f"t2_{base36}")

    page = get(author.browser(), f"/post/{base36}").get_data(as_text=True)
    form = comment_form(page, fullname)
    parts = Elements(form)
    line = parts.by_id(f"{prefix}-anon-hint")
    assert "hidden" not in line["bare"], "your own anonymous post: the comment is anonymous, said without a hover"
    assert line["text"].startswith("Comments on your anonymous post are anonymous.") and LOG_WARNING in line["text"]
    # outside the tools row (a phone draws no row, and the line must still be read there): the row is closed before the line starts
    # from the row's own opening tag up to (not including) the hints block's: every div opened in it is closed, so the row is shut before the lines start
    row = form[form.index('<div class="comment-format"'):form.index('<div class="comment-format-hints"')]
    assert row.count("<div") >= 1 and row.count("</div>") == row.count("<div"), "the tools row closes before the lines begin"
    # the locked switch, and "comment as @me", a real checkbox with its own sentence as its name
    switch = parts.by_id(f"{prefix}-anon")
    assert "checked" in switch["bare"] and "disabled" in switch["bare"] and switch["attrs"]["aria-describedby"] == f"{prefix}-anon-hint"
    me = parts.by_id(f"{prefix}-me")
    assert me["attrs"]["aria-labelledby"] == f"{prefix}-me-hint" and "hidden" not in me["bare"] and me["attrs"]["class"] == "sr-only"
    assert "hidden" in parts.by_id(f"{prefix}-me-hint")["bare"], "its line waits for the switch"

    # a stranger on the same post: the ordinary anonymous switch, its line waiting, and no way to comment as someone else
    other = Elements(comment_form(get(stranger.browser(), f"/post/{base36}").get_data(as_text=True), fullname))
    assert "hidden" in other.by_id(f"{prefix}-anon-hint")["bare"] and LOG_WARNING in other.by_id(f"{prefix}-anon-hint")["text"]
    assert f"{prefix}-me" not in other.ids() and f"{prefix}-me-hint" not in other.ids()
    assert "disabled" not in other.by_id(f"{prefix}-anon")["bare"]


def test_a_limited_sign_in_is_a_stranger_to_its_own_anonymous_post_here_too(real_app, make_member):
    author = make_member()
    board_id = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    fullname, prefix = f"t2_{base36}", prefix_of(f"t2_{base36}")
    page = get(limited_browser(author), f"/post/{base36}").get_data(as_text=True)
    parts = Elements(comment_form(page, fullname))
    assert f"{prefix}-me" not in parts.ids() and f"{prefix}-me-hint" not in parts.ids(), "it can't comment as yourself, so it is not offered"
    anon = parts.by_id(f"{prefix}-anon-hint")
    assert "hidden" in anon["bare"], "no locked line: the page does not tell it which anonymous posts are the member's"
    assert "disabled" not in parts.by_id(f"{prefix}-anon")["bare"]


def test_a_visitor_is_given_no_switches_and_no_lines(real_app, make_member):
    author = make_member()
    board_id = guild(real_app)
    post_id, base36 = add_post(author, board_id, is_anonymous=True)
    page = get(real_app[0].test_client(), f"/post/{base36}").get_data(as_text=True)
    assert "comment-format-hints" not in page and "disclosureSwitch" not in page


# ---------------------------------------------------------------- an ordinary post, and the edit form

def test_a_plain_comment_box_has_real_checkboxes_and_every_line_waiting_hidden(real_app, make_member):
    author, reader = make_member(), make_member()
    board_id = guild(real_app)
    post_id, base36 = add_post(author, board_id)
    fullname, prefix = f"t2_{base36}", prefix_of(f"t2_{base36}")
    form = comment_form(get(reader.browser(), f"/post/{base36}").get_data(as_text=True), fullname)
    parts = Elements(form)
    for key in ("paid", "ai", "anon"):
        box = parts.by_id(f"{prefix}-{key}")
        assert box["tag"] == "input" and box["attrs"]["type"] == "checkbox" and box["attrs"]["class"] == "sr-only"
        assert "hidden" not in box["bare"], key
        assert box["attrs"]["aria-label"] and box["attrs"]["aria-describedby"] == f"{prefix}-{key}-hint" and box["attrs"]["data-hint"] == f"{prefix}-{key}-hint"
        assert "hidden" in parts.by_id(f"{prefix}-{key}-hint")["bare"], key
    assert f"{prefix}-me" not in parts.ids()
    # no checkbox in the tools row carries the old `hidden` attribute any more: only the file picker and Sensitive still do
    row = form[form.index('class="comment-format"'):form.index('class="comment-format-hints"')]
    for tag in re.findall(r"<input\b[^>]*>", row):
        if re.search(r'id="comment-disc-[^"]*-(paid|ai|anon|me)"', tag):
            assert " hidden" not in tag, tag


def test_an_edit_form_shows_the_line_of_a_flag_the_comment_already_has(real_app, make_member):
    author = make_member()
    board_id = guild(real_app)
    post_id, base36 = add_post(author, board_id)
    comment_id, comment36 = add_comment(author, post_id, board_id, paid_partnership=True)
    page = get(author.browser(), f"/post/{base36}").get_data(as_text=True)
    start = page.index(f'id="comment-edit-{comment36}"')
    parts = Elements(page[start:page.index("</form>", start)])
    prefix = f"comment-edit-disc-{comment36}"
    assert "hidden" not in parts.by_id(f"{prefix}-paid-hint")["bare"], "paid is already on: its line is there from the start"
    assert "hidden" in parts.by_id(f"{prefix}-ai-hint")["bare"]
    assert "checked" in parts.by_id(f"{prefix}-paid")["bare"] and "checked" not in parts.by_id(f"{prefix}-ai")["bare"]
    assert f"{prefix}-anon" not in parts.ids() and f"{prefix}-me" not in parts.ids()
