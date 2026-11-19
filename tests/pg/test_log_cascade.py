"""What an admin's obliteration and nuke leave in the Log, on the real app and Postgres (the second review, C79; the helper's rules and SQL are tested on
SQLite in tests/test_log_cascade.py).

The promises: every forwarded copy an obliteration reaches has an entry of its own (the stub's rule, no reason, no account) linked to the stub, in its own
guild's audience, and a row in that guild's own mod log; a re-run writes nothing twice; a nuke writes one staff-only summary and one entry per post and
comment it removed, linked to it, naming no account, the account's anonymous items staff-only; what a guildmaster had removed is left alone; a long reason
is no 500; and nothing is written for a refused nuke."""
import secrets

from pg.test_log_d3c import add_comment, add_post, get, guild, one, post, rows
from pg.test_log_d3d import admin_of

REASON = "Illegal material, removed on a legal order"
NUKE_REASON = "Spam from a stolen account, every post and comment"


def obliterate(admin, b36, reason_category="illegal_content", reason=REASON):
    return post(admin.browser(), admin, f"/admin/obliterate_post/{b36}", reason_category=reason_category, reason=reason)


def nuke(admin, member, reason=NUKE_REASON):
    return post(admin.browser(), admin, "/admin/nuke_user", user=member.username, reason=reason)


def family(real_app, make_member, anonymous=False):
    """A post in a public guild, a forward copy of it in another public guild and one in a private guild, all by one author."""
    author = make_member()
    a_id, a_name = guild(real_app)
    b_id, b_name = guild(real_app)
    c_id, c_name = guild(real_app, private=True)
    title = f"Cascade {secrets.token_hex(3)}"
    primary, p36 = add_post(author, a_id, title=title, is_anonymous=anonymous)
    public_copy, pub36 = add_post(author, b_id, title=title, repost_id=primary, is_anonymous=anonymous)
    private_copy, priv36 = add_post(author, c_id, title=title, repost_id=primary, post_public=False, is_anonymous=anonymous)
    return {"author": author, "boards": (a_id, b_id, c_id), "names": (a_name, b_name, c_name), "primary": (primary, p36),
            "public": (public_copy, pub36), "private": (private_copy, priv36)}


def stub_of(real_app, post_id):
    return one(real_app, "SELECT * FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = :p AND linked_entry_id IS NULL ORDER BY id DESC LIMIT 1", p=post_id)


# ---------------------------------------------------------------- obliteration

def test_each_forwarded_copy_an_obliteration_reaches_has_its_own_entry_linked_to_the_stub(real_app, make_member):
    f = family(real_app, make_member)
    admin = admin_of(make_member, 4)
    done = obliterate(admin, f["primary"][1])
    assert done.status_code in (200, 302), done.get_data(as_text=True)[:200]
    (primary, _), (public_copy, pub36), (private_copy, priv36) = f["primary"], f["public"], f["private"]
    a_id, b_id, c_id = f["boards"]
    stub = stub_of(real_app, primary)
    assert (stub["audience"], stub["rule_key"], stub["reason"], stub["board_id"], stub["actor_role"]) == (0, "illegal", "", a_id, "admin"), "the public stub: the rule only"
    detail = one(real_app, "SELECT audience, reason, linked_entry_id, actor_role FROM log_entries WHERE action = 'suppress_content' AND target_submission_id = :p", p=primary)
    assert (detail["audience"], detail["reason"], detail["linked_entry_id"]) == (3, REASON, stub["id"]), "the reason, for level 4 only, linked to the stub"
    for copy_id, board_id, audience in ((public_copy, b_id, 0), (private_copy, c_id, 1)):
        entry = one(real_app, "SELECT * FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = :p", p=copy_id)
        assert (entry["audience"], entry["board_id"], entry["rule_key"], entry["reason"], entry["linked_entry_id"], entry["actor_role"], entry["actor_id"],
                entry["log_type"]) == (audience, board_id, "illegal", "", stub["id"], "admin", admin.id, "content")
        assert entry["target_user_id"] is None and entry["legacy_modaction_id"] is not None, "no account, and it is the copy of its own mod-log row"
        mod = one(real_app, "SELECT board_id, kind, user_id, target_submission_id FROM modactions WHERE id = :m", m=entry["legacy_modaction_id"])
        assert (mod["board_id"], mod["kind"], mod["user_id"], mod["target_submission_id"]) == (board_id, "obliterate_post", admin.id, copy_id)
        assert one(real_app, "SELECT _note FROM modactions WHERE id = :m", m=entry["legacy_modaction_id"])["_note"] == "admin action"
    # the copies are really gone, whoever wrote what
    assert all(r["purged_utc"] for r in rows(real_app, "SELECT purged_utc FROM submissions WHERE id = ANY(:ids)", ids=[primary, public_copy, private_copy]))


def test_the_copys_log_and_its_guilds_log_say_it_is_gone_to_the_people_who_may_know(real_app, make_member):
    f = family(real_app, make_member)
    assert obliterate(admin_of(make_member, 4), f["primary"][1]).status_code in (200, 302)
    (_, p36), (_, pub36), (_, priv36) = f["primary"], f["public"], f["private"]
    a_name, b_name, c_name = f["names"]
    visitor = real_app[0].test_client()
    # the family's log (the primary's, or a copy's: they are one family) lists the primary's stub and the public copy's entry, never the private guild's
    for path in (f"/post/{p36}/log", f"/post/{pub36}/log"):
        page = get(visitor, path).get_data(as_text=True)
        assert f'<a href="/post/{pub36}">#{pub36}</a>' in page and f'<a href="/post/{p36}">#{p36}</a>' in page, path
        assert f'<a href="/post/{priv36}">#{priv36}</a>' not in page, "a private guild's entry is that guild's"
    # the public guild the copy was in: its own log names the obliterated copy by id
    assert f"#{pub36}" in get(visitor, f"/+{b_name}/mod/log").get_data(as_text=True)
    # the author's account log lists the public ones too
    author_page = get(visitor, f"/@{f['author'].username}/log").get_data(as_text=True)
    assert f'<a href="/post/{pub36}">#{pub36}</a>' in author_page and f'<a href="/post/{priv36}">#{priv36}</a>' not in author_page
    # the private guild's own log is a 404 to a stranger, as it always was
    assert get(visitor, f"/+{c_name}/mod/log").status_code == 404
    # the site-wide Log: the public guild's copy, never the private guild's, and nobody's name
    site = get(visitor, f"/log?guild={b_name}").get_data(as_text=True)
    assert f"#{pub36}" in site and f["author"].username not in site
    assert f"#{priv36}" not in get(visitor, f"/log?guild={c_name}").get_data(as_text=True)


def test_an_obliterations_re_run_adds_a_stub_but_no_second_entry_for_a_copy(real_app, make_member):
    f = family(real_app, make_member)
    admin = admin_of(make_member, 4)
    assert obliterate(admin, f["primary"][1]).status_code in (200, 302)
    assert obliterate(admin, f["primary"][1], reason="Again").status_code in (200, 302)
    for copy_id in (f["public"][0], f["private"][0]):
        assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = :p", p=copy_id)) == 1
        assert len(rows(real_app, "SELECT 1 FROM modactions WHERE kind = 'obliterate_post' AND target_submission_id = :p", p=copy_id)) == 1
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = :p AND linked_entry_id IS NULL", p=f["primary"][0])) == 2


def test_obliterating_a_copy_itself_stays_that_one_row(real_app, make_member):
    f = family(real_app, make_member)
    assert obliterate(admin_of(make_member, 4), f["public"][1]).status_code in (200, 302)
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = ANY(:ids)",
                    ids=[f["primary"][0], f["public"][0], f["private"][0]])) == 1, "one stub, no cascade, the primary untouched"
    assert rows(real_app, "SELECT purged_utc FROM submissions WHERE id = :p", p=f["primary"][0])[0]["purged_utc"] in (None, 0)


def test_an_anonymous_family_names_no_account_and_the_accounts_log_leaves_it_out(real_app, make_member):
    f = family(real_app, make_member, anonymous=True)
    assert obliterate(admin_of(make_member, 4), f["primary"][1]).status_code in (200, 302)
    for copy_id in (f["public"][0], f["private"][0]):
        entry = one(real_app, "SELECT target_user_id, subject_user_id FROM log_entries WHERE action = 'obliterate_post' AND target_submission_id = :p", p=copy_id)
        assert entry["target_user_id"] is None and entry["subject_user_id"] is None
    page = get(real_app[0].test_client(), f"/@{f['author'].username}/log").get_data(as_text=True)
    assert f'<a href="/post/{f["public"][1]}">' not in page, "listing it under its author would say who wrote it"


# ---------------------------------------------------------------- nuke

def nuked_account(real_app, make_member):
    author, bystander = make_member(), make_member()
    board_id, board_name = guild(real_app)
    private_id, private_name = guild(real_app, private=True)
    titles = {name: f"NUKE-{name}-{secrets.token_hex(3)}" for name in ("named", "anonymous", "private", "removed", "bystander")}
    named, named36 = add_post(author, board_id, title=titles["named"])
    anonymous, anon36 = add_post(author, board_id, title=titles["anonymous"], is_anonymous=True)
    private, private36 = add_post(author, private_id, title=titles["private"], post_public=False)
    removed, removed36 = add_post(author, board_id, title=titles["removed"], is_banned=True)
    other, other36 = add_post(bystander, board_id, title=titles["bystander"])
    c_named, cn36 = add_comment(author, other, board_id, "A named comment")
    c_anon, ca36 = add_comment(author, other, board_id, "An anonymous comment", is_anonymous=True)
    c_removed, cr36 = add_comment(author, other, board_id, "Removed earlier", is_banned=True)
    c_other, co36 = add_comment(bystander, other, board_id, "Someone else's comment")
    return {"author": author, "bystander": bystander, "board": (board_id, board_name), "private": (private_id, private_name), "titles": titles,
            "posts": {"named": (named, named36), "anonymous": (anonymous, anon36), "private": (private, private36), "removed": (removed, removed36),
                      "bystander": (other, other36)},
            "comments": {"named": (c_named, cn36), "anonymous": (c_anon, ca36), "removed": (c_removed, cr36), "bystander": (c_other, co36)}}


def summary_of(real_app, author):
    return one(real_app, "SELECT * FROM log_entries WHERE action = 'admin_nuke' AND target_user_id = :u", u=author.id)


def test_a_nuke_writes_one_staff_summary_and_an_entry_for_each_item_it_removed(real_app, make_member):
    n = nuked_account(real_app, make_member)
    admin = admin_of(make_member, 4)
    done = nuke(admin, n["author"])
    assert done.status_code in (200, 302), done.get_data(as_text=True)[:200]
    summary = summary_of(real_app, n["author"])
    assert (summary["audience"], summary["actor_role"], summary["reason"], summary["target_user_id"], summary["actor_id"]) == (2, "admin", NUKE_REASON, n["author"].id, admin.id)
    assert "3 posts and 2 comments" in summary["staff_note"], "the three live posts and two live comments; what a guildmaster removed is not counted"
    removed_posts = {n["posts"][k][0] for k in ("named", "anonymous", "private")}
    removed_comments = {n["comments"][k][0] for k in ("named", "anonymous")}
    children = rows(real_app, "SELECT * FROM log_entries WHERE linked_entry_id = :s ORDER BY id", s=summary["id"])
    assert {c["target_submission_id"] for c in children if c["action"] == "ban_post"} == removed_posts
    assert {c["target_comment_id"] for c in children if c["action"] == "ban_comment"} == removed_comments
    assert len(children) == 5
    by_item = {(c["target_submission_id"], c["target_comment_id"]): c for c in children}
    for child in children:
        assert (child["reason"], child["actor_role"], child["actor_id"], child["log_type"]) == (NUKE_REASON, "admin", admin.id, "content")
        assert child["target_user_id"] is None, "an item's entry names no account"
        assert child["legacy_modaction_id"] is not None
    # the audience: the item's guild's own, the staff's alone for what is anonymous
    assert by_item[(n["posts"]["named"][0], None)]["audience"] == 0 and by_item[(None, n["comments"]["named"][0])]["audience"] == 0
    assert by_item[(n["posts"]["private"][0], None)]["audience"] == 1 and by_item[(n["posts"]["private"][0], None)]["board_id"] == n["private"][0]
    assert by_item[(n["posts"]["anonymous"][0], None)]["audience"] == 2 and by_item[(None, n["comments"]["anonymous"][0])]["audience"] == 2
    # what a guildmaster had removed, and what is not theirs, was left alone
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE target_submission_id = ANY(:ids) OR target_comment_id = ANY(:cids)",
                ids=[n["posts"]["removed"][0], n["posts"]["bystander"][0]], cids=[n["comments"]["removed"][0], n["comments"]["bystander"][0]]) == []
    flags = {r["id"]: r["is_banned"] for r in rows(real_app, "SELECT id, is_banned FROM submissions WHERE id = ANY(:ids)", ids=[v[0] for v in n["posts"].values()])}
    assert flags[n["posts"]["named"][0]] and flags[n["posts"]["anonymous"][0]] and flags[n["posts"]["private"][0]] and not flags[n["posts"]["bystander"][0]]
    assert {r["is_banned"] for r in rows(real_app, "SELECT is_banned FROM comments WHERE id = ANY(:ids)", ids=[n["comments"]["named"][0], n["comments"]["anonymous"][0]])} == {True}
    assert not rows(real_app, "SELECT is_banned FROM comments WHERE id = :c", c=n["comments"]["bystander"][0])[0]["is_banned"]
    # the removed post's page reason is the typed text
    assert one(real_app, "SELECT ban_reason FROM submissions_aux WHERE id = :p", p=n["posts"]["named"][0])["ban_reason"] == NUKE_REASON


def test_the_readers_of_a_nuke_see_the_named_removals_and_never_the_anonymous_ones(real_app, make_member):
    n = nuked_account(real_app, make_member)
    assert nuke(admin_of(make_member, 4), n["author"]).status_code in (200, 302)
    visitor = real_app[0].test_client()
    named36, anon36 = n["posts"]["named"][1], n["posts"]["anonymous"][1]
    guild_log = get(visitor, f"/+{n['board'][1]}/mod/log").get_data(as_text=True)
    assert f"#{named36}" in guild_log and f"#{anon36}" not in guild_log, "the guild's own log hides the anonymous post's row"
    assert f'<a href="/post/{named36}">#{named36}</a>' in get(visitor, f"/post/{named36}/log").get_data(as_text=True)
    account = get(visitor, f"/@{n['author'].username}/log").get_data(as_text=True)
    assert f'<a href="/post/{named36}">#{named36}</a>' in account and f'<a href="/post/{anon36}">' not in account
    assert get(visitor, f"/+{n['private'][1]}/mod/log").status_code == 404
    site = get(visitor, f"/log?guild={n['board'][1]}").get_data(as_text=True)
    assert f"#{named36}" in site and f"#{anon36}" not in site and n["author"].username not in site
    assert "admin_nuke" not in site and "admin nuke" not in site, "the summary names the account: staff only"


def test_a_nuke_run_again_removes_and_records_nothing_twice(real_app, make_member):
    n = nuked_account(real_app, make_member)
    admin = admin_of(make_member, 4)
    assert nuke(admin, n["author"]).status_code in (200, 302)
    before = len(rows(real_app, "SELECT 1 FROM log_entries WHERE action IN ('ban_post', 'ban_comment', 'admin_nuke') AND (actor_id = :a)", a=admin.id))
    mods = len(rows(real_app, "SELECT 1 FROM modactions WHERE user_id = :a", a=admin.id))
    assert nuke(admin, n["author"]).status_code in (200, 302)
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action IN ('ban_post', 'ban_comment', 'admin_nuke') AND (actor_id = :a)", a=admin.id)) == before == 6
    assert len(rows(real_app, "SELECT 1 FROM modactions WHERE user_id = :a", a=admin.id)) == mods == 5
    assert len(rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'admin_nuke' AND target_user_id = :u", u=n["author"].id)) == 1


def test_a_long_reason_full_of_markup_is_no_error_and_the_entry_keeps_all_of_it(real_app, make_member):
    n = nuked_account(real_app, make_member)
    reason = ("<b>&\"'x</b> " * 40)[:500]
    done = nuke(admin_of(make_member, 4), n["author"], reason=reason)
    assert done.status_code in (200, 302), "the guild log's note and the removed post's reason are short columns: a long reason was a DataError"
    entry = one(real_app, "SELECT reason, legacy_modaction_id FROM log_entries WHERE action = 'ban_post' AND target_submission_id = :p", p=n["posts"]["named"][0])
    assert entry["reason"] == " ".join(reason.split()), "the entry keeps the whole reason, as plain text"
    note = one(real_app, "SELECT _note FROM modactions WHERE id = :m", m=entry["legacy_modaction_id"])["_note"]
    assert len(note) <= 256 and note.startswith("admin action | &lt;b&gt;&amp;&quot;") and "<" not in note
    shown = one(real_app, "SELECT ban_reason FROM submissions_aux WHERE id = :p", p=n["posts"]["named"][0])["ban_reason"]
    assert len(shown) <= 128 and shown.startswith("&lt;b&gt;&amp;") and "<" not in shown
    page = get(make_member().browser(), f"/post/{n['posts']['named'][1]}").get_data(as_text=True)
    assert "&lt;b&gt;&amp;" in page, "a removed post's page draws the typed reason as text"


def test_a_refused_nuke_writes_nothing(real_app, make_member):
    n = nuked_account(real_app, make_member)
    senior, peer = admin_of(make_member, 4), admin_of(make_member, 4)
    assert post(senior.browser(), senior, "/admin/nuke_user", user=n["author"].username).status_code == 400, "a reason is asked first"
    assert nuke(senior, peer).status_code == 403, "an admin of the same level can't be nuked"
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE actor_id = :a", a=senior.id) == []
    assert not rows(real_app, "SELECT is_banned FROM submissions WHERE id = :p", p=n["posts"]["named"][0])[0]["is_banned"]


def test_a_nuke_of_an_account_with_nothing_left_to_remove_writes_no_summary(real_app, make_member):
    empty = make_member()
    assert nuke(admin_of(make_member, 4), empty).status_code in (200, 302)
    assert rows(real_app, "SELECT 1 FROM log_entries WHERE action = 'admin_nuke' AND target_user_id = :u", u=empty.id) == []
