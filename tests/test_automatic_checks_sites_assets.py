"""The sites where the site's own checks used to ban an account now quarantine it (the owner, 2026-11-17 and 18; helpers/auto_quarantine.py).

The spam guard, a link to a banned site, an autoban bad link and the SEO-spam check on a bio each used to suspend an account (a day, a week, a
month or for good) on a guess. They call `auto_quarantine.hold(...)` instead: the account can read the site and change its own settings until an admin
looks, and nothing here bans anyone. What this file holds, on the source (the real app's behaviour is `tests/pg/test_automatic_checks_sites.py`):

* none of the four route files (nor the shared draft's publish) calls `.ban(` any more, and each imports the module explicitly;
* every hold names a trigger of `auto_quarantine.TRIGGERS`, and the two copies of the banned-domain table (posts and comments) agree with each other and with the
  rule: reason 4 digitally malicious, 7 sexualizing minors (a level-4 entry), 9 illegal activity;
* a limited QR sign-in is still only refused (a `qr_limited` guard above every hold: `tests/test_qr_login_assets.py` holds the window; here the order of the
  spam guards);
* the spam guard puts the alts on hold in the same call and removes the similar posts and comments EXACTLY as before (the public removal entries), and
  commits afterwards;
* a hold that follows a refusal is committed before the refusal goes out (an `abort` rolls the request back: the old reason-4 text-link ban was lost to it, and a
  shared draft's publish rolls a refused edit back), and the person is told in the request;
* nothing tells the person they were 'suspended for 1 day' any more."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath("ruqqus", *parts).read_text(encoding="utf-8")


POSTS = read("routes", "posts.py")
COMMENTS = read("routes", "comments.py")
SETTINGS = read("routes", "settings.py")
POPUP = read("routes", "profile_edit_pages.py")
COLLAB = read("routes", "collab.py")
SOURCES = {"posts.py": POSTS, "comments.py": COMMENTS, "settings.py": SETTINGS, "profile_edit_pages.py": POPUP}


def holds(source):
    """[(line number, the call's text, up to two lines)] of every `auto_quarantine.hold(` in the source."""
    lines = source.splitlines()
    return [(n, " ".join(lines[n:n + 2])) for n, line in enumerate(lines) if "auto_quarantine.hold(" in line]


def body(source, name):
    """A function's source, from its `def` to the next top-level `def`/decorator."""
    return re.split(r"\n(?=@app\.|def |class )", source.split(f"def {name}(", 1)[1])[0]


# --- nothing here bans any more ----------------------------------------------------------------------------------------

def test_the_four_files_no_longer_ban_anyone_and_say_so_explicitly():
    for name, source in SOURCES.items():
        assert not re.search(r"\.ban\(", source), f"{name} still calls .ban( (the site's own checks quarantine, an admin decides)"
        assert re.search(r"^from ruqqus\.helpers import .*\bauto_quarantine\b", source, re.M), f"{name} must import auto_quarantine explicitly"
    assert "is_quarantined" in COLLAB


def test_the_old_one_day_suspension_notice_is_not_sent_by_any_route_any_more():
    for name, source in SOURCES.items():
        assert "suspended for 1 day" not in source, name
        assert "notify(v, N_(\"Your %(site)s account has been suspended" not in source, name
    assert "Spamming." not in POSTS + COMMENTS and '"seo spam"' not in SETTINGS + POPUP


# --- the triggers and the two copies of the banned-domain table -------------------------------------------------------------

def test_every_hold_names_a_trigger_of_the_module():
    from ruqqus.helpers import auto_quarantine
    named = set()
    for source in SOURCES.values():
        for _, call in holds(source):
            m = re.search(r'auto_quarantine\.hold\(g\.db, v, ("([a-z_]+)"|_LINK_TRIGGERS\[[a-z_.]+\])', call)
            assert m, call
            if m.group(2):
                assert m.group(2) in auto_quarantine.TRIGGERS, m.group(2)
                named.add(m.group(2))
    assert named == {"spam", "seo_spam", "link_malicious"}                # the three the routes name; the link table names the other two


def test_the_two_copies_of_the_banned_domain_table_agree_and_name_real_triggers():
    from ruqqus.helpers import auto_quarantine
    tables = [re.search(r"^_LINK_TRIGGERS = (\{[^}]*\})", s, re.M).group(1) for s in (POSTS, COMMENTS)]
    assert tables[0] == tables[1]
    table = eval(tables[0])
    assert table == {4: "link_malicious", 7: "link_minors", 9: "link_illegal"}
    assert all(key in auto_quarantine.TRIGGERS for key in table.values())
    # a link about minors is for the level-4 admins' eyes (the staff entry's floor), whichever route saw it
    from ruqqus.helpers import action_log
    assert auto_quarantine.TRIGGERS["link_minors"].floor == action_log.LEGAL


def test_the_old_ban_lengths_and_reasons_are_gone_from_the_domain_checks():
    for source in (POSTS, COMMENTS):
        assert not re.search(r'reason=N_\("(Sexualizing minors|Engaging in illegal activity|Digitally malicious content)', source)


# --- the order of things in the spam guards ------------------------------------------------------------------------------------

def test_a_limited_sign_in_is_refused_before_any_hold_and_before_anything_is_touched():
    post = body(POSTS, "submit_post")
    guard = post[post.index("if max(len(similar_urls), len(similar_posts)) >= threshold:"):]
    assert guard.index("if v.qr_limited:") < guard.index("auto_quarantine.hold(")
    for name in ("api_comment", "edit_comment"):
        comment = body(COMMENTS, name)
        spam = comment[comment.index("similar_comments) > threshold"):]
        assert spam.index("if v.qr_limited:") < spam.index("auto_quarantine.hold(")


def test_the_spam_guards_hold_the_account_and_its_alts_and_remove_the_similar_things_exactly_as_before():
    post = body(POSTS, "submit_post")
    guard = post[post.index("if max(len(similar_urls), len(similar_posts)) >= threshold:"):].split("# catch too-long body")[0]
    hold = guard.index("auto_quarantine.hold(g.db, v, \"spam\", alts=v.alts")
    removal = guard.index("for post in similar_posts + similar_urls:")
    assert hold < removal < guard.index("g.db.commit()") < guard.index("auto_quarantine.send_pending(g.db)") < guard.index('return redirect("/notifications")')
    assert 'post.is_banned = True' in guard and 'kind="ban_post"' in guard and 'note="spam"' in guard           # the public removal entries
    for name in ("api_comment", "edit_comment"):
        comment = body(COMMENTS, name)
        spam = comment[comment.index("similar_comments) > threshold"):]
        spam = spam.split("return jsonify({\"error\": tr(\"Too much spam!\")}), 403")[0]
        assert spam.index("auto_quarantine.hold(g.db, v, \"spam\", alts=v.alts") < spam.index("for comment in similar_comments:")
        assert spam.index("comment.is_banned = True") < spam.index("g.db.commit()") < spam.index("auto_quarantine.send_pending(g.db)")
        assert 'kind="ban_comment"' in spam and 'note="spam"' in spam


def test_the_detail_the_staff_entry_keeps_is_counts_and_domains_only_never_member_text():
    allowed = {"malicious.domain", "domain_obj.domain", "found.domain", "badlink.link", "len(similar_posts)", "len(similar_urls)", "len(similar_comments)"}
    seen = set()
    for source in SOURCES.values():
        for _, call in holds(source):
            if "detail=" not in call:
                continue
            detail = call.split("detail=", 1)[1].split('")', 1)[0]
            for name in re.findall(r"\{([^}]*)\}", detail):
                assert name in allowed, f"{name} in {call}"
                seen.add(name)
    assert seen == allowed


# --- a hold is committed before a refusal goes out ---------------------------------------------------------------------------

def test_a_malicious_link_in_the_text_holds_then_answers_the_ordinary_refusal_and_no_abort_rolls_it_back():
    submit = body(POSTS, "submit_post")
    text_link = submit[submit.index("# Run safety filter"):submit.index("# check spam")]
    assert text_link.index("auto_quarantine.hold(g.db, v, \"link_malicious\"") < text_link.index("g.db.commit()") < text_link.index("return {\"html\"")
    assert "abort(403)" not in text_link and '"api": lambda: ({"error": reason}, 403)' in text_link
    edit = body(POSTS, "apply_post_edit")
    edit_link = edit[edit.index("# Run safety filter"):edit.index("else:\n        body_html = primary.body_html")]
    assert edit_link.index("auto_quarantine.hold(g.db, v, \"link_malicious\"") < edit_link.index("g.db.commit()") < edit_link.index("return _edit_error(reason, 403)")
    assert "abort(403)" not in edit_link


def test_the_autoban_bad_links_hold_commit_and_send_you_to_your_notifications_only_when_the_hold_changed_something():
    """The way to the notifications is for an account the hold really changed. `hold` answers None for an admin, a bot, a banned account or one already held, and
    those get the refusal any other bad link gets: a bare redirect told an admin nothing and a shared draft's publish took it for an edit that went through."""
    for source, name in ((POSTS, "submit_post"), (POSTS, "apply_post_edit")):
        route = body(source, name)
        start = route.index("if (badlink.autoban and not v.qr_limited")
        block = route[start:]
        condition = block.split("):\n", 1)[0]
        assert "auto_quarantine.hold(g.db, v, \"spam\"" in condition and condition.rstrip().endswith("is not None"), name
        assert condition.index("not v.qr_limited") < condition.index("auto_quarantine.hold("), "a limited sign-in is refused before anything is held"
        taken = block.split("):\n", 1)[1]
        assert taken.index("g.db.commit()") < taken.index("auto_quarantine.send_pending(g.db)") < taken.index("redirect('/notifications')"), name
    edit = body(POSTS, "apply_post_edit")
    after = edit[edit.index("return redirect('/notifications')"):]
    assert after.lstrip().startswith("return redirect('/notifications')\n\n            return _edit_error("), "the edit's ordinary refusal follows, whatever the hold did"
    submit = body(POSTS, "submit_post")
    after = submit[submit.index("return redirect('/notifications')\n            else:"):]
    assert '"api": lambda: ({"error": tr("The link `%(link)s` is not allowed. Reason: %(reason)s"' in after


def test_a_banned_site_in_a_new_posts_link_holds_and_is_still_refused_with_a_400():
    submit = body(POSTS, "submit_post")
    block = submit[submit.index("if not domain_obj.can_submit:"):submit.index("# check for embeds")]
    assert "if v.qr_limited:\n                pass" in block and "elif domain_obj.reason in _LINK_TRIGGERS:" in block
    assert "_LINK_TRIGGERS[domain_obj.reason]" in block and "g.db.commit()" in block and "), 400)" in block and "({\"error\": reason}, 400)" in block


def test_a_banned_site_in_a_comment_holds_for_the_first_of_the_worst_kinds_and_answers_403_else_400():
    comment = body(COMMENTS, "api_comment")
    block = comment[comment.index("bans = filter_comment_html(body_html)"):comment.index("# a child-locked account can't comment anonymously")]
    assert "if not v.qr_limited:" in block and "for code in (7, 4, 9):" in block                  # the order the old chain had
    assert "_LINK_TRIGGERS[code]" in block and "g.db.commit()" in block and "403 if banned else 400" in block


def test_a_malicious_link_in_a_comment_edit_holds_and_answers_a_403():
    edit = body(COMMENTS, "edit_comment")
    block = edit[edit.index("bans = filter_comment_html(body_html)"):edit.index("# check badlinks")]
    assert block.index('auto_quarantine.hold(g.db, v, "link_malicious"') < block.index("g.db.commit()") < block.index("), 403")
    assert "if malicious is not None and not v.qr_limited:" in block


# --- the profile --------------------------------------------------------------------------------------------------------------------

def test_a_bio_with_a_malicious_link_holds_the_account_in_settings_and_in_the_popup_and_the_refusal_stands():
    settings = body(SETTINGS, "settings_profile_post")
    block = settings[settings.index('if check.code == "links":'):settings.index("v.bio = check.text")]
    assert "if check.malicious:" in block and 'auto_quarantine.hold(g.db, v, "link_malicious"' in block and "}), 401" in block
    popup = body(POPUP, "settings_profile_edit_post")
    refusal = popup[popup.index("if errors:"):popup.index("# --- 5")]
    assert "if malicious and not v.qr_limited:" in refusal and 'auto_quarantine.hold(g.db, v, "link_malicious"' in refusal
    assert refusal.index("auto_quarantine.hold(") < refusal.index("return _refused(")


def test_the_seo_spam_check_holds_the_account_after_the_bio_is_saved_and_a_limited_sign_in_saves_nothing():
    settings = body(SETTINGS, "settings_profile_post")
    seo = settings[settings.index("if profile_edit.looks_like_link_spam("):settings.index("return {\"html\": lambda: render_template(\"settings_profile.html\", v=v, msg=tr(\"Your bio has been updated.\"))")]
    assert 'auto_quarantine.hold(g.db, v, "seo_spam"' in seo and "g.db.commit()" in seo
    assert settings.index("v.bio = check.text") < settings.index('auto_quarantine.hold(g.db, v, "seo_spam"')           # the bio stays as saved
    popup = body(POPUP, "settings_profile_edit_post")
    seo = popup[popup.index("if bio is not None and profile_edit.looks_like_link_spam("):popup.index("# --- 6")]
    assert seo.index("if v.qr_limited:") < seo.index('auto_quarantine.hold(g.db, v, "seo_spam"')
    assert "g.db.rollback()" in seo and "return qr_limited_answer(v)" in seo
    assert "return jsonify" not in seo.split('auto_quarantine.hold(g.db, v, "seo_spam"')[1]       # the save goes on: the pictures of the same save are taken
    assert 'g.db.commit()' not in seo                                                     # the account's lock is held until the whole save is done


def test_the_popup_still_takes_no_hold_out_of_its_way_for_a_held_account():
    # a held account may change its own profile (helpers/quarantine.py): the route has no `not_quarantined`
    head = POPUP.split("def settings_profile_edit_post(")[0].rsplit("\n\n\n", 1)[1]
    assert "not_quarantined" not in head


# --- the shared draft's publish -----------------------------------------------------------------------------------------------------

def test_a_shared_drafts_publish_refuses_an_account_that_is_held_before_or_after_the_edit():
    publish = COLLAB.split("def _publish(")[1].split("\n\n\n")[0]
    assert "publisher.is_suspended or publisher.is_quarantined or publisher.is_deleted" in publish
    assert "if publisher.is_suspended or publisher.is_quarantined:" in publish


def test_a_shared_drafts_publish_also_refuses_the_redirect_to_the_notifications_an_edit_answers_a_held_account_with():
    """Whoever the hold skips (an admin) never gets that redirect now, but a redirect that is not a tuple was never a refusal to the publish: it must not be
    recorded as published whatever the edit's code answers."""
    publish = COLLAB.split("def _publish(")[1].split("\n\n\n")[0]
    assert publish.index("if publisher.is_suspended or publisher.is_quarantined:") < publish.index("if _went_to_notifications(answer):") < publish.index("g.db.expire_all()")
    refusal = publish.split("if _went_to_notifications(answer):")[1].split("g.db.expire_all()")[0]
    assert "g.db.rollback()" in refusal and "return N_(" in refusal, "a refusal, rolled back, before the revision is recorded"
    assert publish.index("if _went_to_notifications(answer):") < publish.index("store.add_publish(")


def _went_to_notifications():
    """The helper itself, taken out of the module's source (the route module needs the whole app to import) and run on real redirects."""
    import ast

    tree = ast.parse(COLLAB)
    (node,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_went_to_notifications"]
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "collab_helper", "exec"), namespace)
    return namespace["_went_to_notifications"]


def test_only_a_redirect_to_the_notifications_counts_as_the_held_answer():
    from flask import Flask, jsonify
    from werkzeug.utils import redirect

    went = _went_to_notifications()
    with Flask("collab-helper").app_context():
        assert went(redirect("/notifications")) and went(redirect("/notifications?page=2")) and went(redirect("/notifications/"))
        assert not went(redirect("/+pets/post/abc")) and not went(redirect("/post/notifications")) and not went(redirect("/login"))
        assert not went(jsonify({"redirect": "/notifications"})), "an XHR success answers 200 with its address in the body"
        assert not went((jsonify({"error": "no"}), 403)) and not went(None) and not went("/notifications")
