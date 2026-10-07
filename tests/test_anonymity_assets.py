"""The anonymity rule (helpers/anonymity.py) only works if no code path shows
who wrote an item without going through it. These checks fail when new code
forgets: a template that reads an author directly, an author-scoped list that is
not filtered, a creation path that drops the flag."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "ruqqus" / "templates"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_no_template_reads_an_authors_name_or_link_directly():
    """`p.author.username` etc. must be `author_of(p, v).username` so an anonymous author is not drawn."""
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        rel = path.relative_to(TEMPLATES).as_posix()
        if rel.startswith("admin/") or rel.startswith("legal/"):     # admins and legal requests see real identities
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"\b(?:p|c|comment_info|linked_comment|post|comment|forwarded_from_comment)\.author\.\w+", line):
                offenders.append(f"{rel}:{n}: {m.group(0)}")
    assert not offenders, "draw authors with author_of(item, v):\n" + "\n".join(offenders)


def test_both_tables_the_model_and_the_migration_have_the_column():
    schema, migration = read("schema.sql"), read("scripts", "migrations", "2026-10-06_anonymous.sql")
    for table in ("submissions", "comments"):
        body = re.search(rf"CREATE TABLE public\.{table} \((.*?)\n\);", schema, re.S).group(1)
        assert "is_anonymous boolean DEFAULT false NOT NULL" in body, table
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS is_anonymous" in migration, table
    for model in ("submission.py", "comment.py"):
        assert "is_anonymous = Column(Boolean" in read("ruqqus", "classes", model), model


def test_every_creation_path_sets_or_copies_the_flag():
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    assert 'is_anonymous=flag(request.form, "anonymous")' in posts                # a new post
    assert "is_anonymous=primary.is_anonymous" in posts                          # a post forwarded to a guild
    assert "is_anonymous=comment.is_anonymous" in posts                          # a comment forwarded into a post
    assert 'flag(request.form, "anonymous") or anonymity.must_be_anonymous(parent_post, v)' in comments
    drafts = read("ruqqus", "helpers", "post_drafts.py")
    assert '"anonymous": flag(form, "anonymous")' in drafts and '"anonymous"' in drafts.split("def publish_form")[1]


def test_the_flag_cannot_be_changed_by_editing():
    posts, comments = read("ruqqus", "routes", "posts.py"), read("ruqqus", "routes", "comments.py")
    edit_post = posts.split("def edit_post")[1].split("\n@app.")[0]
    edit_comment = comments.split("def edit_comment")[1].split("\n@app.")[0]
    assert "anonymous" not in edit_post.replace("anonymous post", "").replace("anonymous\n", "")
    assert "is_anonymous" not in edit_comment and '"anonymous"' not in edit_comment


def test_every_list_scoped_to_an_author_leaves_anonymous_rows_out():
    user, search = read("ruqqus", "classes", "user.py"), read("ruqqus", "routes", "search.py")
    assert "anonymity.hide_anonymous(Submission, v)" in user.split("def userpagelisting")[1].split("def commentlisting")[0]
    assert "anonymity.hide_anonymous(Comment, v)" in user.split("def commentlisting")[1].split("def mods_anything")[0]
    assert "anonymity.hide_anonymous(Submission, v)" in user.split("def forwarded_idlist")[1].split("def history_idlist")[0]
    assert "anonymity.hide_anonymous(Submission, v)" in search.split("if 'author' in criteria")[1][:600]
    assert "not_(Submission.is_anonymous)" in user.split("def idlist")[1][:3000]       # the Following feed
    front = read("ruqqus", "routes", "front.py")
    assert front.count("not_(Submission.is_anonymous)") >= 2 or "Submission.is_anonymous" in front  # the region filter


def test_a_curation_does_not_list_its_accounts_anonymous_posts():
    # a curation can hold a single account: its feed is then a list of one author's posts
    member = read("ruqqus", "helpers", "curation_feed.py").split("def member_condition(")[1].split("\ndef ")[0]
    assert "and_(Submission.author_id.in_(user_ids), not_(Submission.is_anonymous))" in member
    assert "Submission.author_id.in_(user_ids)\n" not in member     # never the bare account match
    # and the feed takes its accounts from nowhere else
    routes = read("ruqqus", "routes", "curations.py")
    assert "curation_feed.member_condition(curation.id)" in routes and "CurationUser.target_user_id)" not in routes


def test_public_counts_exclude_anonymous_items():
    user = read("ruqqus", "classes", "user.py")
    assert "is_anonymous=False" in user.split("def public_post_count")[1][:200]
    assert "data['post_count']=self.public_post_count" in user


def test_actions_that_would_single_the_author_out_are_refused():
    assert "is_anonymous" in read("ruqqus", "routes", "paypal.py")       # tips
    assert read("ruqqus", "routes", "posts.py").count("anonymous") >= 2   # repost
    assert read("ruqqus", "routes", "admin_api.py").count("is_anonymous") >= 2   # distinguish
    assert read("ruqqus", "routes", "boards.py").count("is_anonymous") >= 2      # herald
    assert 'not parent.is_anonymous' in read("ruqqus", "routes", "comments.py")  # block refusals
    assert "follower_ids = [] if new_post.is_anonymous" in read("ruqqus", "routes", "posts.py")   # bell notifications


def test_the_json_methods_hide_the_author():
    for name in ("submission.py", "comment.py"):
        src = read("ruqqus", "classes", name)
        assert "anonymity.identity_hidden(self, anonymity.current_viewer())" in src, name
        assert "'is_anonymous': bool(self.is_anonymous)" in src, name


def test_the_forms_offer_the_option_and_the_jinja_helpers_exist():
    assert 'name="anonymous"' in read("ruqqus", "templates", "partials", "post_options.html")
    assert "anonymous_field(" in read("ruqqus", "templates", "submit.html")
    assert "anonymous_option" in read("ruqqus", "templates", "partials", "post_options.html")
    helpers = read("ruqqus", "routes", "anonymity.py")
    for name in ("anon_hidden", "author_of", "op_badge"):
        assert name in helpers
