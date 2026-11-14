"""Removing an anonymous co-author by the number their credit carries (the owner, 2026-11-14: "they should be able to remove someone by alias, at which point if
the vote succeeds the person is exposed by the fact that they are voted off"), on the real app and Postgres.

The promises: the server finds the account from the number, and only from a credit that IS anonymous (a number held by an author whose credit is named, one nobody
holds and one whose holder left all read as "That account isn't an author."); until it passes the other authors are shown the number and never the account, and the
person it is about is told they are the subject and can't vote on it; a proposal by number is a duplicate of another by the same number only, never of one by name; when
it passes the person is out and the staff are told which account and number, and their credit on the post is still the number."""
import json
import secrets
import time

from sqlalchemy import text

from pg.test_log_d3c import add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of
from pg.test_log_trail_routes import notices_of

REFUSED = "That account isn&#39;t an author."


def _draft(member):
    here = member.browser()
    made = post(here, member, "/collab/new")
    assert made.status_code == 302, made.status_code
    return int(made.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def published(real_app, make_member, anonymous=(1, 2), named=1):
    """A shared post: the author, `named` co-authors with their names, and one co-author for each number in `anonymous` (their credit hidden, that number in the
    post's thread). Everyone is an author of the draft and recently active, so every vote counts."""
    author = make_member()
    doc = _draft(author)
    board_id, _ = guild(real_app)
    post_id, b36 = add_post(author, board_id, title=f"A shared post {secrets.token_hex(3)}")
    people = {"author": author, "named": [make_member() for _ in range(named)], "anon": {n: make_member() for n in anonymous}}
    db = author.db_session()
    now = int(time.time())
    db.execute(text("UPDATE collab_docs SET post_id = :p, published_revision = revision WHERE id = :d"), {"p": post_id, "d": doc})
    everyone = [author, *people["named"], *people["anon"].values()]
    for member in everyone:
        db.execute(text("UPDATE users SET last_active_utc = :n WHERE id = :u"), {"n": now, "u": member.id})
        if member is not author:
            db.execute(text("INSERT INTO collab_authors (doc_id, user_id, joined_utc, left_utc) VALUES (:d, :u, :n, 0)"), {"d": doc, "u": member.id, "n": now})
    for member in people["named"]:
        db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                        "VALUES (:p, :u, :a, 'accepted', :n, :n, false)"), {"p": post_id, "u": member.id, "a": author.id, "n": now})
    for number, member in people["anon"].items():
        db.execute(text("INSERT INTO post_coauthors (post_id, user_id, invited_by_id, status, created_utc, accepted_utc, is_anonymous) "
                        "VALUES (:p, :u, :a, 'accepted', :n, :n, true)"), {"p": post_id, "u": member.id, "a": author.id, "n": now})
        db.execute(text("INSERT INTO anon_aliases (scope_kind, scope_id, user_id, n, created_utc) VALUES ('post', :p, :u, :k, :n)"),
                   {"p": post_id, "u": member.id, "k": number, "n": now})
    db.commit()
    return doc, post_id, people


def propose(member, doc, **fields):
    client = member.browser()
    return post(client, member, f"/api/collab/{doc}/propose", kind="remove_author", **fields)


def vote(member, doc, pid, answer="yes"):
    client = member.browser()
    return client.post(f"/api/collab/{doc}/proposals/{pid}/vote", base_url="http://localhost", data={"formkey": member.formkey(client), "vote": answer},
                       headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})


def panel(member, doc):
    return get(member.browser(), f"/collab/{doc}/panel").get_data(as_text=True)


def test_a_number_that_finds_no_credit_is_refused_the_same_whatever_the_reason(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author = people["author"]
    named = people["named"][0]
    db = author.db_session()
    db.execute(text("INSERT INTO anon_aliases (scope_kind, scope_id, user_id, n, created_utc) VALUES ('post', :p, :u, 7, :n)"), {"p": post_id, "u": named.id, "n": int(time.time())})
    db.commit()                                                   # the named co-author wrote anonymous comments in the thread: a number, but no anonymous credit
    gone = people["anon"][2]
    db.execute(text("UPDATE collab_authors SET left_utc = :n WHERE doc_id = :d AND user_id = :u"), {"n": int(time.time()), "d": doc, "u": gone.id})
    db.commit()
    answers = [propose(author, doc, alias=a) for a in ("9", "7", "2", "0", "abc", "-1", "1.5")]
    assert {a.status_code for a in answers} == {400} and len({a.get_data(as_text=True) for a in answers}) == 1, "one answer for every number that finds nobody"
    assert REFUSED in answers[0].get_data(as_text=True) or "isn't an author" in answers[0].get_data(as_text=True).replace("&#39;", "'")
    assert rows(real_app, "SELECT 1 FROM collab_proposals WHERE doc_id = :d", d=doc) == []
    assert propose(author, doc, alias="1").status_code == 200, "a credit that is anonymous and still an author is found"


def test_a_removal_by_number_shows_the_number_to_the_others_and_the_person_that_they_are_the_subject(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first, second = people["anon"][1], people["anon"][2]
    assert propose(author, doc, alias="1").status_code == 200
    pid = one(real_app, "SELECT id, subject_user_id, payload FROM collab_proposals WHERE doc_id = :d", d=doc)
    assert pid["subject_user_id"] == first.id and json.loads(pid["payload"]) == {"alias_n": 1}
    for viewer in (author, named, second):
        page = panel(viewer, doc)
        assert "Remove Anonymous #1" in page, viewer.username
        assert "Remove you as an author" not in page
    mine = panel(first, doc)
    assert "Remove you as an author" in mine and "/vote?vote=yes" not in mine.split("Remove you as an author")[1].split("</li>")[0], "no vote buttons for the subject"
    assert vote(first, doc, pid["id"]).status_code == 403, "the subject can't vote on their own removal"


def test_a_proposal_by_number_is_a_duplicate_of_another_by_the_same_number_only(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first = people["anon"][1]
    by_name = propose(named, doc, username=first.username)
    assert by_name.status_code == 200
    by_number = propose(author, doc, alias="1")
    assert by_number.status_code == 200, "an open proposal by name about the same person doesn't make a number's a duplicate: it would say the number is that person"
    again = propose(named, doc, alias="1")
    assert again.status_code == 409 and "already an open proposal" in again.get_data(as_text=True)


def test_when_it_passes_the_person_is_out_told_and_noted_for_the_staff_and_their_credit_stays_anonymous(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first, second = people["anon"][1], people["anon"][2]
    assert propose(author, doc, alias="1").status_code == 200
    pid = one(real_app, "SELECT id FROM collab_proposals WHERE doc_id = :d", d=doc)["id"]
    assert one(real_app, "SELECT outcome FROM collab_proposals WHERE id = :i", i=pid)["outcome"] == "open"
    assert vote(named, doc, pid).status_code == 200
    assert one(real_app, "SELECT outcome FROM collab_proposals WHERE id = :i", i=pid)["outcome"] == "open", "everyone else counted has to agree"
    assert vote(second, doc, pid).status_code == 200
    assert one(real_app, "SELECT outcome FROM collab_proposals WHERE id = :i", i=pid)["outcome"] == "passed"
    assert one(real_app, "SELECT left_utc FROM collab_authors WHERE doc_id = :d AND user_id = :u", d=doc, u=first.id)["left_utc"] > 0
    assert any("removed you as an author" in n for n in notices_of(real_app, first.id))
    entry = one(real_app, "SELECT action, actor_role, audience, target_user_id, staff_note FROM log_entries WHERE action = 'collab_alias_removal' AND target_user_id = :u", u=first.id)
    assert (entry["audience"], entry["actor_role"]) == (2, "author") and "Anonymous #1" in entry["staff_note"]
    page = panel(author, doc)
    assert f"Remove {first.username}" in page, "once it has passed, the person is known to the authors: they are not among them"
    assert one(real_app, "SELECT is_anonymous FROM post_coauthors WHERE post_id = :p AND user_id = :u", p=post_id, u=first.id)["is_anonymous"] is True, "the public credit stays a number"
    log = get(real_app[0].test_client(), "/log").get_data(as_text=True)
    assert "collab_alias_removal" not in log and first.username not in log, "nothing public"
    staff = get(admin_of(make_member).browser(), "/admin/log?action=collab_alias_removal").get_data(as_text=True)
    assert "collab_alias_removal" in staff


def test_a_failed_or_withdrawn_removal_never_names_the_account_and_the_panel_asks_first(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first = people["anon"][1]
    assert propose(author, doc, alias="1").status_code == 200
    pid = one(real_app, "SELECT id FROM collab_proposals WHERE doc_id = :d", d=doc)["id"]
    assert vote(named, doc, pid, "no").status_code == 200
    assert one(real_app, "SELECT outcome FROM collab_proposals WHERE id = :i", i=pid)["outcome"] == "failed"
    assert first.username not in panel(author, doc).split("Remove Anonymous #1")[0].split("collab-proposals-h")[-1], "a removal that failed names nobody"
    assert "Remove Anonymous #1" in panel(named, doc)
    page = panel(author, doc)
    assert 'name="alias"' in page and "Propose removing by number" in page
    assert 'data-confirm="Propose removing this anonymous author?' in page, "the form asks before it sends"
    lonely = make_member()
    assert 'name="alias"' not in panel(lonely, _draft(lonely)), "a draft that isn't published has no numbers to remove by"


def test_a_removal_by_name_is_not_a_duplicate_of_an_open_one_by_number_either(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first = people["anon"][1]
    assert propose(author, doc, alias="1").status_code == 200
    by_name = propose(named, doc, username=first.username)
    assert by_name.status_code == 200, "a 409 here would say the open removal of a number is about this person"
    again = propose(author, doc, username=first.username)
    assert again.status_code == 409, "two by name about the same person are duplicates"


def test_a_removal_by_number_that_passes_after_the_person_left_removes_nobody_and_names_nobody(real_app, make_member):
    doc, post_id, people = published(real_app, make_member)
    author, named = people["author"], people["named"][0]
    first, second = people["anon"][1], people["anon"][2]
    assert propose(author, doc, alias="1").status_code == 200
    pid = one(real_app, "SELECT id FROM collab_proposals WHERE doc_id = :d", d=doc)["id"]
    db = author.db_session()
    db.execute(text("UPDATE collab_authors SET left_utc = :n WHERE doc_id = :d AND user_id = :u"), {"n": int(time.time()), "d": doc, "u": first.id})
    db.commit()                                                    # they leave while the vote is open
    assert vote(named, doc, pid).status_code == 200 and vote(second, doc, pid).status_code == 200
    assert one(real_app, "SELECT outcome FROM collab_proposals WHERE id = :i", i=pid)["outcome"] == "passed"
    assert not any("removed you as an author" in n for n in notices_of(real_app, first.id)), "they were not removed: they left"
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'collab_alias_removal' AND target_user_id = :u", u=first.id) == []
    page = panel(author, doc)
    assert f"Remove {first.username}" not in page and "Remove Anonymous #1" in page, "a vote that removed nobody names nobody"
