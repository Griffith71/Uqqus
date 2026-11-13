"""The privacy page says what the site really does with cookies, browser storage and other sites. The page is English on purpose
(a legal page). What these tests read, and so what they can catch: every `document.cookie =` and `.setItem(` in the site's own scripts and
templates (a call whose key is held in a name fails until the name is listed in KEYS_IN_NAMES), and every script, stylesheet, picture and
frame a template loads from a fixed address on another host, and every https:// address in the site's own scripts (HOSTS_IN_SCRIPTS).
They can't see an address a template fills in at run time (the captcha, the ads client, a linked video), and the chat bundles are not read,
only their sources. The Matrix SDK inside has storage of its own: `createClient` with no store makes one that keeps sync filter ids in
localStorage (`mxjssdk_memory_filter_*`), so the client is given a `MemoryStore` with none (keeper.js makeClient), which writes nothing,
and those keys, left by earlier clients, are only ever removed (test_the_chat_stores_are_named_and_the_sdks_own_keys_only_removed)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "ruqqus" / "templates" / "help" / "privacy.html").read_text(encoding="utf-8")
MAIN = (ROOT / "ruqqus" / "__main__.py").read_text(encoding="utf-8")
LOGIN = (ROOT / "ruqqus" / "routes" / "login.py").read_text(encoding="utf-8")
FRONT = (ROOT / "ruqqus" / "routes" / "front.py").read_text(encoding="utf-8")
JS_DIR = ROOT / "ruqqus" / "assets" / "js"
SOURCES = [p for p in JS_DIR.glob("*.js") if "bundle" not in p.name]
SOURCES += list((ROOT / "ruqqus" / "assets" / "chat_src" / "src").glob("*.js"))
SOURCES += list((ROOT / "ruqqus" / "templates").rglob("*.html"))


def text_of(path):
    return path.read_text(encoding="utf-8")


COOKIE_WRITE = re.compile(r"document\.cookie\s*=\s*(?:\w+\s*\?\s*)?[\"'](\w+)=")


def test_every_cookie_the_scripts_write_is_named():
    written = set()
    for path in SOURCES:
        text = text_of(path)
        by_name = text.count("document.cookie = kept[0]")   # setVideoPref: a table of names
        assert len(re.findall(r"document\.cookie\s*=", text)) == len(COOKIE_WRITE.findall(text)) + by_name, f"{path.name}: a cookie this test can't read the name of"
        written |= set(COOKIE_WRITE.findall(text))
        if by_name:
            written |= set(re.findall(r"\[\"(video_\w+)\",", text))
    assert {"density", "text_size", "feed_paging", "sidebar_pref", "video_autoplay", "video_captions", "video_sound", "video_consent"} <= written
    for name in written:
        assert f"`{name}`" in PAGE, name


def test_the_servers_own_cookies_are_named():
    assert "`session_ruqqus`" in PAGE and '"session_ruqqus"' in MAIN
    assert "`ui_lang`" in PAGE and 'COOKIE = "ui_lang"' in text_of(ROOT / "ruqqus" / "helpers" / "i18n.py")


SET_ITEM = re.compile(r"\.setItem\(\s*([^,]+?)\s*,")
LITERAL_KEY = re.compile(r"""[\"']([\w:-]+)[\"'](?:\s*\+.*)?$""")
# A key held in a name can't be read off the call. Each such call is listed here after someone has read it: (file of the call, name): (file
# that holds the value, the keys it can be). A new name fails test_every_storage_key_is_named until it is listed and the page names its key.
KEYS_IN_NAMES = {
    ("chat_key.js", "STORE"): ("chat_key.js", ("uqqus_chat_prekey",)),
    ("chat_key.js", "CHANGED"): ("chat_key.js", ("uqqus_chat_prekey_before",)),
    ("post_editor.js", "FOLD_KEY"): ("post_editor.js", ("post_toolbar_open",)),
    ("profile_edit.js", "SAVED_FLAG"): ("profile_edit.js", ("profile_edit_saved",)),
    ("video_playback.js", "SOUND_KEY"): ("video_playback.js", ("video_sound",)),
    ("video_playback.js", "AUDIO_KEY"): ("video_playback.js", ("audio_volume",)),
    ("token.js", "INDEX_KEY"): ("token.js", ("uqqus_chat_devices",)),
    ("token.js", "PROBE_KEY"): ("token.js", ("uqqus_chat_probe",)),
    ("token.js", "WAITING_KEY"): ("token.js", ("uqqus_chat_waiting",)),
    ("token.js", "RETRY_KEY + prefix"): ("token.js", ("uqqus_store_retry:",)),
    ("keeper.js", "key"): ("keeper.js", ("uqqus_chat_reloads:",)),
    ("crypto.js", "rescueMarkName(client)"): ("crypto.js", ("uqqus_chat_rescue:",)),
    ("index.js", "CODE_LATER"): ("index.js", ("uqqus_chat_code_later",)),
    ("all_js.js", "CHAT_INDEX_KEY"): ("all_js.js", ("uqqus_chat_devices",)),
}


def storage_keys(file_name, text):
    """(the keys this source writes, the names it writes under that nobody has reviewed)."""
    keys, unreviewed = set(), []
    for argument in SET_ITEM.findall(text):
        literal = LITERAL_KEY.match(argument)
        if literal:
            keys.add(literal.group(1))
        elif (file_name, argument) in KEYS_IN_NAMES:
            keys.update(KEYS_IN_NAMES[(file_name, argument)][1])
        else:
            unreviewed.append((file_name, argument))
    return keys, unreviewed


def test_a_new_key_is_caught_whichever_way_it_is_written():
    assert storage_keys("x.js", 'localStorage.setItem("brand_new", "1")') == ({"brand_new"}, [])
    assert storage_keys("x.js", "window.sessionStorage.setItem('a:' + b, 1)") == ({"a:"}, [])
    assert storage_keys("x.js", "store.setItem(newKey, 1)") == (set(), [("x.js", "newKey")])
    assert storage_keys("x.js", 'localStorage.setItem(prefix + name, "1")') == (set(), [("x.js", "prefix + name")])


def test_every_storage_key_is_named():
    found, unreviewed = set(), []
    for path in SOURCES:
        text = text_of(path)
        assert not re.search(r"(?:local|session)Storage\[", text), f"{path.name}: a key written with [ ] is one this test can't read"
        keys, names = storage_keys(path.name, text)
        found |= keys
        unreviewed += names
    assert unreviewed == [], "read these calls, list them in KEYS_IN_NAMES and name their keys on the privacy page"
    assert "late_box_h:" in found and "ruqqus-media-connect" in found
    for key in found:
        assert f"`{key}`" in PAGE, key


def test_each_listed_name_still_holds_what_the_list_says():
    by_name = {path.name: text_of(path) for path in SOURCES}
    for (_, _name), (holder, keys) in KEYS_IN_NAMES.items():
        for key in keys:
            assert re.search(rf"""[\"']{re.escape(key)}[\"']""", by_name[holder]), (holder, key)


def test_the_old_claims_are_gone():
    assert "encrypted cookies" not in PAGE and "randomized" not in PAGE
    assert "authentication and abuse mitigation purposes only" not in PAGE
    assert "signed, not encrypted" in PAGE and "a year" in PAGE


def test_it_says_video_sites_load_on_scroll_today():
    assert "Video players that load on scroll" in PAGE and "before you press anything" in PAGE
    assert "set cookies, see your address and record what you watch" in PAGE
    # the rule the sentence describes: only these wait for a press (helpers/social_embeds.py); a change there changes the page
    embeds = text_of(ROOT / "ruqqus" / "helpers" / "social_embeds.py")
    assert 'CLICK_HOSTS = ("facebook.com", "instagram.com", "tiktok.com", "kick.com")' in embeds
    assert "Videos from Facebook, Instagram, TikTok and Kick" in PAGE


def test_it_does_not_promise_tap_to_play_where_it_has_no_effect():
    # outside the places that are asked, a Space's replay and a story's video don't go through feed_video_autoplay.js, the only script that applies
    # the choice (in them, they wait for a press: tests/test_consent_assets.py). When either starts to, this fails and the sentence goes with it.
    space = text_of(ROOT / "ruqqus" / "templates" / "space.html").split("{% if video_consent() in ('ask', 'no') %}")[1].split("{% endif %}")[0].split("{% else %}")[1]
    assert 'src="{{ boot.replayPlayer }}"' in space and 'loading="lazy"' in space and "feed_video_autoplay" not in space
    assert "frame.src = 'https://www.youtube.com/embed/'" in text_of(JS_DIR / "stories.js")
    assert "you can stop this on feeds and post pages with Tap to play" in PAGE
    assert "None of that reaches a video replay on a Space's page" in PAGE and "a YouTube video in a story" in PAGE
    assert "A video replay on a Space's page loads as it comes into view too. If you are signed in you can stop this" not in PAGE


def test_it_says_another_accounts_chat_data_is_kept_locked():
    # the chat plan's phase 2: another account's devices are kept, locked, and never used (token.js; tests/test_chat_index.py). Only these
    # three pages start chat.
    callers = {path.name for path in (ROOT / "ruqqus" / "assets" / "chat_src" / "src").glob("*.js")
               if re.search(r"ensureSession\(boot, \"(?:chat|call)\"[,)]", text_of(path))}
    assert callers == {"index.js", "call.js", "space.js"}
    assert "another account can't open them" in PAGE and "Up to four accounts' databases are kept on one browser" in PAGE
    assert "MAX_ACCOUNTS = 4;" in text_of(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js")
    assert "which clears them first" not in PAGE and "signing in on this browser has them cleared" not in PAGE


def test_it_says_what_log_out_and_a_deletion_here_do_to_chat():
    """all_js.js leaveChatSessions and forgetChatDevices (tests/test_chat_session.py runs them): Log out takes the sealed sign-ins and
    keeps every store; a deletion here deletes that account's stores only; a browser that never used chat gets only the counter."""
    js = text_of(JS_DIR / "all_js.js")
    leave = js[js.index("function leaveChatSessions()"):js.index("function forgetChatPrekey()")]
    assert "delete e.sealed;" in leave and "deleteDatabase" not in leave and "{ v: 2, owner: null, epoch: 0, entries: [] }" in leave
    # Log out removes the waiting first messages too (all_js.js leaveChatSessions): the page never promises they stay until sent
    assert "localStorage.removeItem(CHAT_WAITING_KEY);" in leave
    assert ("Pressing Log out removes the locked chat sign-in and any first messages still waiting to be sent, and keeps the locked "
            "databases") in PAGE
    assert "Log out leaves `uqqus_chat_devices` holding only a counter" in PAGE
    assert "They are also deleted when that account is deleted here" in PAGE and "function forgetChatDevices(mxid, children)" in js and "function forgetChildChatDevices(children)" in js
    # build step 5: chat sign-ins last an hour and are renewed through the site, which keeps the means to renew them, encrypted
    assert "keeps working until that chat device is signed out" not in PAGE
    assert "which stops working within an hour: a chat sign-in lasts an hour and is renewed through this site only while you are signed in" in PAGE
    assert "We also keep, encrypted, the means to renew each of those devices' sign-in to the chat server" in PAGE
    # build step 6 (assets/chat_src/src/harvest.js): a kept store's keys move to the account's newer chat device here, and only then does it
    # go; one found holding nothing goes; a broken one goes after 30 days (token.js BROKEN_KEEP); what came while no device existed is lost
    assert "after their keys have moved to a newer chat device of the same account in this browser" in PAGE
    assert "each browser where you used chat then brings back the messages it could already read, once chat is unlocked there again" in PAGE
    assert "when one kept from a signed-out chat device turns out to hold no keys" in PAGE
    # a store that opens but whose export or import threw MAX_FAILS times is `broken` too (harvest.js failed()), so the page names both
    assert "30 days after one stopped opening or its keys couldn't be moved to a newer chat device three times" in PAGE
    failed = text_of(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "harvest.js")
    assert "export const MAX_FAILS = 3;" in failed and "an export or an import threw" in failed
    assert "while your account had no chat device anywhere" in PAGE
    # the per-account cap (token.js overTheCaps, MAX_REMOVED) deletes the oldest kept stores whether harvested or not: the page names it
    token = text_of(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js")
    assert "export const MAX_REMOVED = 6;" in token and "for (const e of removed.slice(MAX_REMOVED)) out.add(e.p);" in token
    assert ("Of one account's signed-out chat devices on a browser, the databases of the six most recent are kept; beyond those, the "
            "oldest go, whether or not their keys have moved to a newer chat device.") in PAGE
    # keys that only a browser never used again holds are lost (CLAUDE.md's harvest bullet says the page says so)
    assert "Messages that only a browser you never use chat on again could read can't be brought back anywhere else." in PAGE
    harvest =text_of(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "harvest.js")
    assert "export async function harvest(" in harvest and "export async function sweepEmpty(" in harvest
    assert "export const BROKEN_KEEP = 30 * 24 * 60 * 60 * 1000;" in text_of(ROOT / "ruqqus" / "assets" / "chat_src" / "src" / "token.js")
    # the ads paragraph reads the index the hold reads (partials/ad_hold.html)
    ads = PAGE[PAGE.index("<strong>Ads and your chat keys.</strong>"):]
    assert "<code>uqqus_chat_devices</code>" in ads and "ruqqus_chat_session" not in ads
    assert "holds its key in that page's memory, which Google's ad script on that page could read" in ads


TAG_HOST = re.compile(r"<(?:script|link|img|iframe|source|video|audio|embed)\b[^>]*?\b(?:src|href)\s*=\s*[\"'](?:https?:)?//([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", re.I)
# named on the page in words instead of by its host: which words
HOSTS_IN_WORDS = {"pagead2.googlesyndication.com": "Google AdSense"}
# every https:// address in the site's own scripts: what the page says about it, None for one no browser asks (a comment, an example)
HOSTS_IN_SCRIPTS = {
    "g.tenor.com": "go straight to Tenor",
    "media.giphy.com": "GIPHY",
    "www.youtube.com": "a YouTube video in a story",
    "stackoverflow.com": None,
    "example.com": None,
}


def test_it_names_every_host_a_template_loads_from():
    hosts = set()
    for path in (ROOT / "ruqqus" / "templates").rglob("*.html"):
        if "email" not in path.parts:                       # a mail app fetches those, not a page of the site
            hosts |= set(TAG_HOST.findall(text_of(path)))
    assert {"cdnjs.cloudflare.com", "code.jquery.com", "stackpath.bootstrapcdn.com", "cdn.jsdelivr.net", "unpkg.com",
            "maxcdn.bootstrapcdn.com"} <= hosts
    for host in hosts:
        assert HOSTS_IN_WORDS.get(host, host) in PAGE, host


def test_it_says_what_each_address_in_the_sites_scripts_is():
    scripts = [p for p in SOURCES if p.suffix == ".js"]
    hosts = set()
    for path in scripts:
        hosts |= set(re.findall(r"https://([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)", text_of(path)))
    assert hosts <= set(HOSTS_IN_SCRIPTS), hosts - set(HOSTS_IN_SCRIPTS)
    for host, words in HOSTS_IN_SCRIPTS.items():
        assert words is None or words in PAGE, host


def test_the_gif_search_and_the_map_and_the_bucket_are_said():
    assert 'g.tenor.com/v1/search?q=" + searchTerm' in text_of(JS_DIR / "all_js.js")
    assert "the words you type into its search box go straight to Tenor" in PAGE
    assert "cdn.jsdelivr.net/npm/jsvectormap" in text_of(ROOT / "ruqqus" / "templates" / "regions.html")
    assert "the world map on the regions page" in PAGE
    # post thumbnails and post pictures come from the same bucket as avatars
    assert "/posts/{self.base36id}/thumb.png" in text_of(ROOT / "ruqqus" / "classes" / "submission.py")
    assert "post thumbnails and pictures uploaded directly to the site for a post or comment" in PAGE


# what a shell does about the password-derived key: partials/forget_prekey.html
FORGET = '{{ forget_prekey() }}'
FORGET_ALWAYS = '{{ forget_prekey(always=True) }}'
# a shell a visitor can land on takes it away; these don't, each for its reason
KEEPS_THE_PREKEY = {
    "login_2fa.html": "the sign-in is still going on",
    "submit.html": "members only", "settings.html": "members only", "guild/guild_settings.html": "members only",
    "call.html": "members only", "space.html": "members only: a visitor is sent to sign in", "media_connected.html": "members only",
    "embeds/embed_default.html": "drawn inside other sites' pages, where it can't reach this site's storage",
    "errors/502.html": "the proxy's", "errors/503.html": "the site is down: nothing can be signed in to",
    "email/default.html": "an email",
}


def test_every_shell_decides_about_the_prekey():
    templates = ROOT / "ruqqus" / "templates"
    for path in templates.rglob("*.html"):
        text = text_of(path)
        if "<!DOCTYPE" not in text.upper():
            continue
        name = path.relative_to(templates).as_posix()
        if name in ("login.html", "sign_up.html"):
            assert FORGET_ALWAYS in text, name
        elif name in KEEPS_THE_PREKEY:
            assert "forget_prekey" not in text, name
        else:
            assert FORGET in text, f"{name}: a new shell takes the key away, or says why not in KEEPS_THE_PREKEY"


def test_the_prekey_goes_for_anyone_not_signed_in_and_the_page_says_when_it_stays():
    partial = text_of(ROOT / "ruqqus" / "templates" / "partials" / "forget_prekey.html")
    assert 'always or not session.get("user_id")' in partial
    assert 'sessionStorage.removeItem("uqqus_chat_prekey")' in partial and 'localStorage.removeItem("uqqus_chat_prekey")' in partial
    line = next(line for line in PAGE.splitlines() if line.startswith("* `uqqus_chat_prekey`"))
    assert "each time you send the sign-in or sign-up form" in line and "whether or not you use chat" in line
    assert "a sign-in we refused doesn't leave it behind" in line
    assert "It is kept for the one tab you typed the password in" in line and "another tab can't read it" in line
    assert "If you sign in and never open chat, it stays until you close that tab or log out in it" in line
    assert "If you use chat" not in line


# every form that leaves the pre-key (assets/js/chat_key.js), and what the page calls it
PREKEY_FORMS = {
    "login.html": "the sign-in or sign-up form",
    "sign_up.html": "the sign-in or sign-up form",
    "settings_security.html": "when you change your password",
    "login_qr_upgrade.html": "when you enter your password after signing in with a QR code",
}


def test_every_form_that_writes_the_prekey_is_said():
    templates = ROOT / "ruqqus" / "templates"
    forms = {str(p.relative_to(templates)) for p in templates.rglob("*.html")
             if re.search(r"<form[^>]*\bdata-chat-(prekey|rekey)\b", text_of(p))}
    assert forms == set(PREKEY_FORMS), "a new form that leaves the key: say it on the page, and hold the ads back for it"
    line = next(line for line in PAGE.splitlines() if line.startswith("* `uqqus_chat_prekey`"))
    for said in PREKEY_FORMS.values():
        assert said in line, said


def test_what_a_sign_in_leaves_in_the_cookie_is_said():
    start = PAGE.index("* **After you sign out,**")
    line = PAGE[start:PAGE.index("\n", start)]
    assert "your feed filters leave with it" in line and "the feed you were last on" in line
    assert "Otherwise it lasts until you close your browser" in line
    # the numbers of the accounts stay only where a password was typed: check_for_alts, and the rule for a QR sign-in
    assert "If someone has signed in here with a password" in line


def test_a_file_sent_to_a_members_own_storage_is_said():
    from ruqqus.helpers.media import rules
    assert '"url": location, "method": "PUT"' in text_of(ROOT / "ruqqus" / "helpers" / "media" / "gdrive.py")
    start = PAGE.index("{% if media_offered() %}* **Your own storage account.**")
    line = PAGE[start:PAGE.index("\n", start)]
    assert "your browser sends each file straight to it, not through us" in line
    for name in ("gdrive", "onedrive", "youtube", "dailymotion"):
        assert rules.PROVIDER_LABELS[name] in line, name


def test_a_picture_in_what_members_write_is_said():
    # the rule the sentence describes: a host on the list keeps its <img>, any other becomes a link
    sanitize = text_of(ROOT / "ruqqus" / "helpers" / "sanitize.py")
    assert "if not(netloc) or (domain and domain.show_thumbnail):" in sanitize
    assert "pictures someone put in the text of a post, a comment or anything else members write" in PAGE
    assert "[our list of picture hosts](/info/image_hosts)" in PAGE


def test_the_network_in_front_of_the_site_is_said_conditionally_because_the_code_can_not_know():
    line = next(line for line in PAGE.splitlines() if "The network in front of the site" in line)
    assert "Depending on how it is deployed" in line and "may be served" in line and "Where it is" in line


def test_the_captcha_named_is_the_one_in_use():
    assert "hCaptcha" not in PAGE and "Turnstile" not in PAGE       # written out, it was wrong when another one was chosen
    assert PAGE.count("{% if captcha_name() %}") == 2 and "{{ captcha_name() }}" in PAGE
    assert '@app.template_global("captcha_name")' in text_of(ROOT / "ruqqus" / "helpers" / "jinja2.py")


def test_it_stays_english_with_only_the_note_translated():
    assert '<div lang="en" dir="ltr">' in PAGE
    assert re.findall(r"\b_\(\s*[\"']([^\"']+)", PAGE) == ["This page is in English: the English text is the binding version."]
    assert "{% trans" not in PAGE


def test_the_cookie_rule_runs_once_for_every_request_and_nothing_else_makes_a_visitor_permanent():
    assert "session_cookie.settle(flask_session" in MAIN
    assert "flask_session.permanent = True" not in MAIN
    assert 'flask_session["session_id"] = secrets.token_hex' not in MAIN


def test_signing_in_and_signing_up_make_the_cookie_year_long():
    assert 'flask_session["session_id"] = token_hex(16)\n    flask_session["login_nonce"] = account.login_nonce\n    flask_session.permanent = True' in LOGIN
    assert 'session["session_id"] = token_hex(16)\n    session.permanent = True' in LOGIN


def test_opening_the_home_page_writes_nothing():
    # `/` is the default feed: writing it would set a cookie on a first-time visitor's first page
    assert FRONT.count("flask_session['base_feed'] =") == 1
    body = FRONT[FRONT.index("def _remember_feed"):FRONT.index("def _feed_home")]
    assert "flask_session.get('base_feed', 'for_you') != name" in body
    assert "request.path.startswith(('/api/', '/inpage/'))" in body


def test_the_chat_stores_are_named_and_the_sdks_own_keys_only_removed():
    chat = ROOT / "ruqqus" / "assets" / "chat_src" / "src"
    token = text_of(chat / "token.js")
    # the browser databases token.js names (uqqus-<kind>-<localpart>-<device>, kind chat or call) are the page's `uqqus-chat-…`, `uqqus-call-…`
    assert "return `uqqus-${kind}-${local[1]}-${device}`;" in token and 'export const KINDS = ["chat", "call"];' in token
    assert "`uqqus-chat-…`" in PAGE and "`uqqus-call-…`" in PAGE
    # the SDK's MemoryStore is given no localStorage, so its filter ids (mxjssdk_memory_filter_*) are never written; the ones earlier
    # clients left are removed, by a chat start and by Log out and deletion (all_js.js), and nowhere written
    assert "store: new sdk.MemoryStore()," in text_of(chat / "keeper.js")
    assert 'const FILTER_KEYS = "mxjssdk_memory_filter_";' in token and "key.startsWith(FILTER_KEYS)) localStorage.removeItem(key)" in token
    assert '"mxjssdk_memory_filter_FILTER_SYNC_" + mxid' in text_of(JS_DIR / "all_js.js")
    for path in SOURCES:
        text = text_of(path)
        assert not any("mxjssdk" in argument or "FILTER" in argument for argument in SET_ITEM.findall(text)), path.name


def test_what_the_locked_chat_store_leaves_in_clear_is_said():
    # the chat plan's raw-dump experiment (chat_session.spec.ts, every object store read after a Log out): the crypto library keeps
    # `tracked_users`, the Matrix ids whose devices the store follows, unlocked, and the database names name the account and device
    assert "their names say which account and device they belong to, and one part of them isn't locked" in PAGE
    assert "which is yours and the people you chat with" in PAGE
    spec = text_of(ROOT / "scripts" / "ui" / "chat_session.spec.ts")
    assert 'expect(Object.keys(kept.hits), "only tracked_users holds a name in clear").toEqual(["tracked_users"]);' in spec
