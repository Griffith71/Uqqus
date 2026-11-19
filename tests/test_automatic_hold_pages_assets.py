"""The help pages say what the site's automatic checks do now: they put an account on hold (a quarantine), never ban or suspend it (the owner, 2026-11-17 and 18).

`/help/moderation`, the Terms and the privacy page each say it, and what they say is held to the code here, so a page can't promise what the site doesn't do: the public
Log lines are the words `classes/mod_logs.py` writes, the generic reasons are `helpers/action_log.py`'s, the limit of an appeal's words is `log_appeals`'s, the link the
notice sends is `helpers/quarantine.py`'s, and every route the moderation page says a held account can't use carries `@not_quarantined` (and every one it says stays open
doesn't). Source-reading tests: nothing here opens a database."""
import re
from pathlib import Path

from ruqqus.helpers import action_log, auto_quarantine, log_appeals, log_notices, quarantine

ROOT = Path(__file__).resolve().parent.parent
HELP = ROOT / "ruqqus" / "templates" / "help"

MODERATION_HEAD = '<h2>{{ _("Automatic checks") }}</h2>'
MODERATION_NEXT = '<h2>{{ _("If a decision was wrong") }}</h2>'


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def moderation():
    return read("ruqqus", "templates", "help", "moderation.html")


def terms():
    return read("ruqqus", "templates", "help", "terms.html")


def privacy():
    return read("ruqqus", "templates", "help", "privacy.html")


def moderation_section():
    text = moderation()
    assert text.count(MODERATION_HEAD) == 1, "the moderation page has one Automatic checks section"
    return text.split(MODERATION_HEAD, 1)[1].split(MODERATION_NEXT, 1)[0]


def terms_paragraph():
    paragraphs = [p for p in terms().split("\n\n") if p.startswith("Some of our checks run automatically")]
    assert len(paragraphs) == 1, "the Terms have one paragraph about the automatic checks, in Enforcement"
    return paragraphs[0]


def privacy_bullet():
    bullets = [line for line in privacy().splitlines() if line.startswith("* If one of the site's automatic checks puts your account on hold")]
    assert len(bullets) == 1, "the privacy page has one bullet about a hold, in Your information"
    return bullets[0]


def privacy_subsection():
    text = privacy()
    assert text.count("### Automatic checks and holds") == 1
    return text.split("### Automatic checks and holds", 1)[1].split("{% endfilter %}", 1)[0]


def flat(text):
    """The words of a block as a reader meets them: tags, the template's own quoting and line breaks out."""
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.replace('\\"', '"').split())


# --- the three pages say a check holds and never bans ----------------------------------------------------------------------------------------

def test_the_three_pages_say_a_check_holds_and_never_bans():
    assert "An automatic check can put an account on hold. It can never ban one." in flat(moderation_section())
    assert "put your account on hold" in terms_paragraph() and "It can't ban or suspend your account" in terms_paragraph()
    assert "never ban or suspend one" in privacy_subsection()
    # nothing in the new text tells a person a check bans or suspends: every 'ban' and 'suspend' in it is a denial, the administrator's, or a ban someone made
    for name, block in (("moderation", flat(moderation_section())), ("terms", terms_paragraph()), ("privacy", privacy_subsection() + privacy_bullet())):
        for sentence in re.split(r"(?<=[.:;])\s+", block):
            if re.search(r"\b(ban|bans|banned|suspend|suspends|suspension)\b", sentence, re.IGNORECASE):
                assert re.search(r"never|can't|cannot|not\b|administrator|admin\b|separate decision|later bans|bans that|getting round a ban|evading a ban", sentence, re.IGNORECASE), \
                    f"{name}: {sentence!r} could be read as a check banning"


def test_each_page_says_what_an_email_does_not_do_and_how_to_ask_for_a_review():
    for block in (flat(moderation_section()), terms_paragraph(), privacy_subsection()):
        assert re.search(r"[Vv]erifying an email address", block), block[:80]
        assert re.search(r"not (?:lift|verifying)|Not time, and not verifying", block), block[:80]
    assert quarantine.APPEAL_LINK in flat(moderation_section()) and quarantine.APPEAL_LINK in privacy_subsection()
    assert "ask for a review" in terms_paragraph()


def test_the_sign_up_checks_hold_is_still_lifted_by_an_email_and_the_page_says_only_that_one_is():
    section = flat(moderation_section())
    assert "A hold the sign-up check puts on a brand-new account is different" in section
    assert "unless an automatic check has fired on the account since" in section
    # what the code does: the guard's hold (kind 0) says to verify an email; an automatic one (kind 1) never does
    assert "verify an email" in str(quarantine.HELD) and "email" not in str(quarantine.HELD_AUTOMATIC)


# --- what they quote is what the code says ----------------------------------------------------------------------------------------------------

def test_the_pages_quote_the_two_public_lines_and_the_generic_reasons_as_the_code_writes_them():
    logs = read("ruqqus", "classes", "mod_logs.py")
    held = re.search(r'"automatic_quarantine":\s*\{[^}]*?"str":\s*N_\("([^"]+)"\)', logs, re.DOTALL).group(1)
    lifted = re.search(r'"automatic_quarantine_lifted":\s*\{[^}]*?"str":\s*N_\("([^"]+)"\)', logs, re.DOTALL).group(1)
    section = flat(moderation_section())
    assert f'"{held}"' in section, "the page quotes the Log's line for a hold"
    assert f'"{lifted}"' in section, "the page quotes the Log's line for a lift"
    for reason in (action_log.AUTOMATIC_SPAM_REASON, action_log.AUTOMATIC_LINK_REASON, action_log.AUTOMATIC_IMAGE_REASON):
        assert f'"{reason}"' in section, reason
    # every generic reason a trigger can give is one of those three (or the plain one a ban-evasion hold keeps staff-only)
    spoken = {action_log.AUTOMATIC_SPAM_REASON, action_log.AUTOMATIC_LINK_REASON, action_log.AUTOMATIC_IMAGE_REASON}
    for trigger in auto_quarantine.TRIGGERS.values():
        assert trigger.reason in spoken or not trigger.public, trigger.key


def test_the_lines_are_sanctions_the_removals_are_moderation_and_some_holds_have_no_public_line():
    from ruqqus.helpers import log_view

    for action in action_log.AUTOMATIC_QUARANTINE_ACTIONS:
        assert action_log.type_of(action) == action_log.SANCTION, action
        assert action in action_log.PUBLIC_ACTIONS, action
    assert str(log_view.TAB_LABELS[action_log.SANCTION]) == "Sanctions" and str(log_view.TAB_LABELS[action_log.CONTENT]) == "Moderation"
    assert action_log.type_of("ban_post") == action_log.type_of("ban_comment") == action_log.CONTENT
    section = flat(moderation_section())
    assert "one line on the Sanctions tab" in section and "on the Moderation tab like any removal" in section
    # In the code exactly one trigger has no public line (ban-evasion detection: a public line would teach how alts are caught, the owner's decision 3), and an
    # alt's hold has none either. A page that said "each hold has a line" would promise what the site doesn't do, so the pages say MOST holds have one and that
    # some are the staff's alone, and say nothing about which: neither the check nor the way a hold reaches a second account.
    assert [key for key, trigger in auto_quarantine.TRIGGERS.items() if not trigger.public] == ["evasion"]
    assert "Most holds have one line" in section and "Some holds have no public line and are read by the staff only" in section
    for block in (section, terms_paragraph(), privacy_subsection(), privacy_bullet()):
        for sentence in re.split(r"(?<=[.:;])\s+", block):
            if re.search(r"no public line|staff read the rest|staff read the rest only", sentence, re.IGNORECASE):
                assert not re.search(r"\bban\b|second account|another one|evasion|evad|alt\b", sentence, re.IGNORECASE), sentence
    for block in (section, terms_paragraph(), privacy_subsection(), privacy_bullet()):
        assert not re.search(r"second account|because of another one", block, re.IGNORECASE), block[:80]
        assert not re.search(r"one line for each hold|shows each hold|each hold has", block, re.IGNORECASE), block[:80]
    assert "most holds" in terms_paragraph() and "for most holds" in privacy_subsection()


def test_a_review_is_up_to_five_hundred_characters_and_the_notice_carries_the_link_the_pages_name():
    assert log_appeals.REASON_MAX == 500
    assert "up to 500 characters" in flat(moderation_section())
    assert "500 characters" in privacy_bullet()
    assert log_notices.QUARANTINE_APPEAL == quarantine.APPEAL_LINK == "/appeal/quarantine"
    assert "Ask for a review" in str(log_notices.QUARANTINED) and "Ask for a review" in flat(moderation_section())


def test_the_pages_name_the_checks_the_triggers_have():
    section = flat(moderation_section())
    for phrase in ("spam", "links it blocks", "spam in a profile", "getting round a ban", "pictures that match a list"):
        assert phrase in section, phrase
    assert {"spam", "seo_spam", "link_malicious", "link_illegal", "link_minors", "evasion", "image"} == set(auto_quarantine.TRIGGERS)
    privacy_text = privacy_bullet()
    for phrase in ("spam", "a link we block", "spam in a profile", "getting round a ban", "a picture that matched a list"):
        assert phrase in privacy_text, phrase


# --- what an automatic check REMOVES: the page says it is public, that it sends no notice of its own, and where the person finds the appeal -----------------

def test_the_page_does_not_promise_a_notice_for_an_automatic_removal_and_names_the_appeal_link_the_item_shows():
    section = flat(moderation_section())
    found = re.search(r'What an automatic check removes is public\..*?(?=" \) \}\}|"\) \}\})', section)
    assert found, "the moderation page has a paragraph about what an automatic check removes"
    text = found.group(0)
    assert "Only a person lifts a hold" not in text, "the paragraph was cut where it ends"
    assert "The removal itself sends no separate notice" in text and "Appeal this decision" in text
    assert not re.search(r"is told|are told|is sent|are sent|an Appeal button", text), text
    # the link the page names is the one the removed post's and comment's pages draw for the person it is about, and what it opens
    assert "Appeal this decision" in read("ruqqus", "templates", "submission_banned.html") and "Appeal this decision" in read("ruqqus", "templates", "comments.html")
    assert "appeal_for_item(p, v)" in read("ruqqus", "templates", "submission_banned.html") and "appeal_for_item(c, v)" in read("ruqqus", "templates", "comments.html")
    pages = read("ruqqus", "routes", "log_appeal_pages.py")
    item = pages.split("def appeal_for_item(item, v):", 1)[1].split("def appeal_for_guild(", 1)[0]
    assert "'ban_comment'" in item and "'ban_post'" in item
    # an appeal against an automatic removal is the site's admins' (a removal by the system), and the code's APPEALABLE says a removal can be appealed
    assert "ban_post" in log_appeals.APPEALABLE and "ban_comment" in log_appeals.APPEALABLE
    assert log_appeals.APPEALABLE["ban_post"][1] == "content" and "the system's" in (log_appeals.__doc__ or "")
    assert "decide the rest" in moderation() and "the site's admins decide that appeal" in text


def test_no_automatic_removal_sends_a_notice_of_its_own_so_the_page_may_say_so():
    """`log_notices.tell_decision` is what tells a person a removal or hide was made. It is called for an admin's removal and for a guild's or a post author's hide, and
    for no removal an automatic check makes (the spam guard's, the image checks'). If one is added, the moderation page's sentence has to change with it."""
    callers = {}
    for path in (ROOT / "ruqqus").rglob("*.py"):
        if path.name == "log_notices.py":
            continue
        count = path.read_text(encoding="utf-8").count("log_notices.tell_decision(")
        if count:
            callers[path.relative_to(ROOT).as_posix()] = count
    assert callers == {"ruqqus/routes/admin_api.py": 2, "ruqqus/routes/boards.py": 1, "ruqqus/routes/comments.py": 2}, callers
    # the two in comments.py are a guild's hide and a post author's hide, never the spam guard's removal
    comments = read("ruqqus", "routes", "comments.py")
    for needle in ('"hide_comment_from_guild"', '"author_hide_comment"'):
        assert f"log_notices.tell_decision(g.db, entry, {needle}" in comments, needle
    for name in ("posts.py",):
        assert "tell_decision" not in read("ruqqus", "routes", name)
    for name in ("safety.py",):
        assert "tell_decision" not in read("ruqqus", "helpers", "media", name)
    assert "tell_decision" not in read("ruqqus", "helpers", "action_log.py")
    # a removal by the spam guard or an image check is written as the system's (an admin's removal is not)
    assert "record_image_check_removal" in read("ruqqus", "helpers", "action_log.py")


# --- what a held account can and cannot do is what the routes do ------------------------------------------------------------------------------------

def decorators_of(source, name):
    match = re.search(rf"((?:@[^\n]+\n)+)def {name}\(", source)
    assert match, name
    return match.group(1).strip().split("\n")


# the things the moderation page says a held account can't do, each with a route that does it and the action the wrapper counts it as
CANT = {
    "post": ("posts.py", "submit_post"),
    "edit": ("posts.py", "edit_post"),
    "comment": ("comments.py", "api_comment"),
    "edit comment": ("comments.py", "edit_comment"),
    "vote": ("votes.py", "api_vote_post"),
    "follow someone": ("users.py", "follow_user"),
    "join a guild": ("boards.py", "subscribe_board"),
    "forward": ("posts.py", "forward_post"),
    "repost": ("posts.py", "repost_post"),
    "tip": ("paypal.py", "gift_post_pid"),
    "report": ("flagging.py", "api_flag_post"),
    "start a chat": ("chat.py", "chat_start"),
    "start a group": ("chat_groups.py", "chat_group_create"),
    "use chat (the chat server's session)": ("chat.py", "chat_token"),
    "use chat (the chat page)": ("chat.py", "chat_page"),
    "use calls": ("calls.py", "call_join"),
    "write in a channel": ("guild_channels.py", "guild_channel_message"),
    "write in a wiki": ("guild_wiki.py", "guild_wiki_save"),
    "write an event": ("guild_events.py", "guild_event_create"),
    "make a Space": ("spaces.py", "space_create"),
    "make a circle": ("circles.py", "circle_create"),
    "make a curation": ("curations.py", "curations_create_post"),
    "make a story": ("stories.py", "story_create"),
    "use a guildmaster's powers": ("guild_channels.py", "guild_channel_remove"),
}

# the things it says stay open
CAN = {
    "delete what it wrote (a post)": ("posts.py", "delete_post_pid"),
    "delete what it wrote (a comment)": ("comments.py", "delete_comment"),
    "bookmark": ("posts.py", "save_post"),
    "unfollow": ("users.py", "unfollow_user"),
    "leave guilds": ("boards.py", "unsubscribe_board"),
    "send feedback": ("site_feedback.py", "send_feedback"),
}


def test_every_route_the_moderation_page_says_a_held_account_cannot_use_refuses_it():
    for what, (module, name) in CANT.items():
        stack = decorators_of(read("ruqqus", "routes", module), name)
        wrapper = [d for d in stack if d.startswith("@not_quarantined(")]
        assert wrapper, f"{what}: {module} {name} doesn't carry @not_quarantined"
        action = re.search(r'@not_quarantined\("(\w+)"\)', wrapper[0]).group(1)
        assert action in quarantine.ACTIONS, (what, action)
    section = flat(moderation_section())
    for phrase in ("post, comment, edit, vote, follow someone, join a guild, forward, repost, tip, report, use chat or calls",
                   "write in a channel, a wiki or an event", "make a Space, a circle, a curation or a story", "use a guildmaster's powers"):
        assert phrase in section, phrase


def test_every_route_it_says_stays_open_is_open_and_the_page_says_so():
    for what, (module, name) in CAN.items():
        stack = decorators_of(read("ruqqus", "routes", module), name)
        assert not any(d.startswith("@not_quarantined(") for d in stack), f"{what}: {module} {name} refuses a held account"
    section = flat(moderation_section())
    assert "read the site, change its own settings and profile, delete what it wrote, bookmark, unfollow, leave guilds and send feedback" in section


def test_a_held_guildmaster_loses_the_powers_the_page_says_it_loses():
    wrappers = read("ruqqus", "helpers", "wrappers.py")
    guild = wrappers.split("def is_guildmaster(", 1)[1].split("\ndef ", 1)[0]
    assert 'getattr(v, "is_quarantined", False)' in guild, "is_guildmaster asks whether the account is held"
    has_mod = read("ruqqus", "classes", "boards.py").split("def has_mod(self, user, perm=None):", 1)[1].split("def leads(", 1)[0]
    assert 'getattr(user, "is_quarantined", False)' in has_mod


# --- the new text is marked, in place, and breaks none of the pins the old pages carry ---------------------------------------------------------------

def test_the_moderation_section_sits_above_the_line_that_points_at_it_and_every_line_of_it_is_marked():
    text = moderation()
    assert text.index(MODERATION_HEAD) < text.index(MODERATION_NEXT) < text.index("see Automatic checks above")
    lines = [line for line in moderation_section().splitlines() if line.strip()]
    assert len(lines) >= 6
    for line in lines:
        assert (line.startswith('<p>{{ _("') and line.endswith('") }}</p>')) or (line.startswith('<h2>{{ _("') and line.endswith('") }}</h2>')), line[:80]
    # the paragraph under "If a decision was wrong" is marked too
    pointer = [line for line in text.splitlines() if "see Automatic checks above" in line]
    assert len(pointer) == 1 and pointer[0].startswith('<p>{{ _("') and pointer[0].endswith('") }}</p>')


def test_the_new_text_uses_none_of_the_phrases_other_pages_tests_look_lines_up_by():
    new_text = flat(moderation_section()) + " " + terms_paragraph() + " " + privacy_bullet() + " " + privacy_subsection()
    assert not re.search(r"second admin", new_text, re.IGNORECASE), "tests/test_sole_admin_wording_assets.py wants a sole-administrator clause beside this phrase"
    for line_finder in ("If you appeal a decision", "Report this", "When the Log cannot write an entry"):
        assert line_finder not in privacy_bullet() + privacy_subsection(), line_finder
    for banned in ("reply", "replies", "promote", "yank", "crosspost"):
        assert not re.search(rf"\b{banned}\b", new_text, re.IGNORECASE), banned
    # the privacy page keeps exactly one marked string (its "this page is in English" note)
    assert privacy().count("_(") == 1


def test_the_terms_and_the_privacy_page_stay_in_english_with_no_marked_string_added():
    assert "_(" not in terms_paragraph() and "_(" not in privacy_bullet() and "_(" not in privacy_subsection()
    assert terms().count('<div lang="en"') == 1 and privacy().count('<div lang="en"') == 1


def test_the_privacy_bullet_and_the_data_export_agree_on_what_a_member_can_download():
    export = read("ruqqus", "helpers", "data_export.py")
    assert "quarantined_utc" in export and "an automatic check" in export and "the sign-up check" in export
    bullet = privacy_bullet()
    assert "in your data download" in bullet and "whether an automatic check or the sign-up check did it" in bullet
    # a deleted account's hold ends with it and the staff's entries stay, which the bullet says
    cleanup = read("ruqqus", "helpers", "account_cleanup.py")
    assert "UPDATE users SET quarantined_utc = 0, quarantine_kind = 0 WHERE id = :u" in cleanup
    assert "The staff's note stays in the [Log](/log)'s record if you delete your account" in bullet
