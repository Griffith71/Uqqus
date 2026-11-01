"""The consent banner's wiring (CLAUDE.md "Video consent"): who reads the answer, the one script that starts a player, every player gated, the
banner's manners, every Cookie choices link, the wait for Google's message, and what the privacy page says. The rules are tests/test_consent.py;
scripts/ui/consent.spec.ts watches a real browser."""
import re
from pathlib import Path

import jinja2

from stylesheets import SHEETS, expanded

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


THEME = read("templates", "partials", "theme.html")
PLAYER = read("templates", "partials", "player.html")
BANNER = read("templates", "partials", "consent_banner.html")
DEFAULT = read("templates", "default.html")
HEADER = read("templates", "header.html")
DOCS = read("templates", "docs_default.html")
SPACE = read("templates", "space.html")
FEED = read("assets", "js", "feed_video_autoplay.js")
RULES = read("assets", "js", "video_playback.js")
SCRIPT = read("assets", "js", "consent.js")
STORIES = read("assets", "js", "stories.js")
STYLE = read("assets", "style", "_consent.scss")
PRIVACY = read("templates", "help", "privacy.html")
MAIN = read("__main__.py")
ROUTE = read("routes", "consent_pages.py")


def html_attrs(state):
    macro = THEME.split("{% macro html_attrs() %}")[1].split("{% endmacro %}")[0]
    template = jinja2.Environment().from_string("{% macro html_attrs() %}" + macro + "{% endmacro %}{{ html_attrs() }}")
    return template.render(request=type("Request", (), {"cookies": {}})(), ui_lang=lambda: "en", ui_dir=lambda: "ltr", video_consent=lambda: state)


# --- <html> and the headers ----------------------------------------------------------------------------------------------

def test_html_names_the_answer_only_for_someone_it_concerns():
    assert 'data-video-consent' not in html_attrs("")
    for state in ("ask", "no", "yes"):
        assert html_attrs(state).endswith(' data-video-consent="%s"' % state)


def test_pages_vary_by_country_and_files_do_not():
    block = MAIN.split('response.vary.add("Accept-Language")')[1].split('if "Cache-Control" not in response.headers')[0]
    assert 'response.mimetype == "text/html"' in block
    assert 'response.vary.add("CF-IPCountry")' in block and 'response.vary.add("Cookie")' in block


# --- one place decides, one place starts ---------------------------------------------------------------------------------

def test_the_rule_is_in_video_playback_and_the_feed_script_asks_it():
    assert "consentWaits(env.consent)" in RULES and "function autoplayAllowed(env)" in RULES
    assert "VP.autoplayAllowed(window.VideoConsent.environment())" in FEED and "function environment()" not in FEED
    env = SCRIPT.split("function environment() {")[1].split("api.environment = environment;")[0]
    assert "consent: data.videoConsent," in env and "pref: data.videoAutoplay," in env and "reducedMotion:" in env and "saveData:" in env
    assert "videoConsent" not in RULES                     # the rule has no page in it: node runs it


def test_the_answer_is_read_before_the_script_that_starts_players():
    assert DEFAULT.index("js/video_playback.js") < DEFAULT.index("js/consent.js") < DEFAULT.index("js/feed_video_autoplay.js")
    assert SPACE.index("js/video_playback.js") < SPACE.index("js/consent.js") < SPACE.index("js/feed_video_autoplay.js")


def test_no_page_builds_a_player_outside_the_script_that_starts_them():
    """Every frame a page can make, and each one's gate. A new one needs a line here and a gate."""
    frames = {}
    for path in (ROOT / "assets" / "js").glob("*.js"):
        if "bundle" in path.name or path.name.endswith(".min.js"):
            continue
        for found in re.finditer(r"""createElement\(.iframe|element\(.iframe|['"]<iframe""", path.read_text(encoding="utf-8")):
            frames.setdefault(path.name, 0)
            frames[path.name] += 1
    assert frames == {
        "feed_video_autoplay.js": 1,      # the players
        "stories.js": 1,                  # a story's video: drawVideoPress first (below)
        "share_chat.js": 1,               # the site's own chat page
        "side_panels.js": 1,              # the site's own notifications and chat pages
        "all_js.js": 2,                   # the typed-in Konami egg, and #ytEmbed, which no template has
    }, frames


def test_a_held_player_goes_back_to_scrolling_after_allow_and_never_starts_while_held():
    observe = FEED.split("function observeAll() {")[1].split("// a looping GIF")[0]
    assert "container.dataset.embedHeld = '1'" in observe and "delete container.dataset.embedHeld" in observe
    assert observe.index("embedHeld") < observe.index("if (pressOnly) container.dataset.embedLoad = 'click'")
    best = FEED.split("function bestCandidate() {")[1].split("function reconcile()")[0]
    assert "container.dataset.embedLoad !== 'click'" in best
    assert "if (container.dataset.embedLoad === 'click') return playChosen(container);" in observe


def test_the_reader_taking_it_back_takes_players_down_and_only_then():
    assert 'if (choice === "no" && (had === "yes" || !had)) takeDown();' in SCRIPT
    assert "window.takeDownFeedVideos()" in SCRIPT and 'iframe.space-replay-frame, iframe.story-frame' in SCRIPT
    assert "window.takeDownFeedVideos = takeDown;" in FEED
    assert "takeDownFeedVideos" not in FEED.split("function observeAll")[1].split("function start")[0]


def test_taking_it_back_removes_every_kind_and_scrolling_away_still_keeps_a_video():
    down = FEED.split("function takeDown() {")[1].split("document.addEventListener")[0]
    assert "deactivateNow" not in down
    assert "media.removeAttribute('src')" in down and "media.load()" in down and "media.remove()" in down and "thumb.style.display = ''" in down
    assert down.index("media.removeAttribute('src')") < down.index("media.load()") < down.index("media.remove()")
    scrolling = FEED.split("function deactivateNow(container) {")[1].split("function bestCandidate")[0]
    assert "media.pause();" in scrolling and "keep the element" in scrolling


def test_a_page_the_browser_kept_follows_an_answer_given_since():
    kept = SCRIPT.split('window.addEventListener("pageshow"')[1].split("var banner = null")[0]
    assert "if (!event.persisted) return;" in kept
    assert "readChoice" not in kept and "nowSeconds" not in kept                                 # no clock: the date may be one this browser can't place
    assert "if (text && text !== cookieSeen) page.setAttribute(\"data-video-consent\", choiceIn(text));" in kept
    assert kept.index('var had = page.getAttribute("data-video-consent");') < kept.index("var text = cookieText(document.cookie);")
    assert "if (choice !== had) {" in kept and "setOpen(false);" in kept and "follow(choice, had);" in kept
    assert "follow(choice, had);" in SCRIPT.split("function choose(choice) {")[1]


def test_the_line_on_a_reopened_banner_reads_the_same_rule_as_the_players():
    opened = SCRIPT.split("function setOpen(open, reopened) {")[1].split("function close()")[0]
    assert "scrollingStartsPlayers()" in opened
    assert "[data-consent-now='wait']\").hidden = !reopened || scrolling" in opened and "[data-consent-now='allowed']\").hidden = !reopened || !scrolling" in opened
    assert "window.VideoPlayback.autoplayAllowed(environment())" in SCRIPT
    # every shell that carries the banner has the rule before the script
    for shell in (DEFAULT, HEADER, DOCS, SPACE):
        assert shell.index("js/video_playback.js") < shell.index("js/consent.js")


def test_the_cookie_is_kept_past_the_answer():
    assert '"; path=/; max-age=" + KEEP_SECONDS + "; samesite=lax"' in SCRIPT


# --- the server sets the same cookie ---------------------------------------------------------------------------------------

def test_the_route_stores_nothing_and_builds_the_cookie_itself():
    assert "from .consent_pages import *" in read("routes", "__init__.py")
    assert "@app.post(consent_rules.ANSWER_PATH)" in ROUTE and "methods=" not in ROUTE
    assert '@limiter.limit("30/minute")' in ROUTE and "key_func" not in ROUTE                      # per address: a visitor has no account
    assert "same_origin.from_another_site(" in ROUTE
    for word in ("g.db", "session", "render_template", "request.cookies", "request.args", "request.values", "abort("):
        assert word not in ROUTE.split('"""', 2)[2], word
    # the one thing from the request that reaches the cookie has been through the rule; the name is the rule's too
    assert 'consent_rules.cookie_value(sent[0] if len(sent) == 1 else None, time.time())' in ROUTE
    assert 'consent_rules.set_answer(resp, value, request.is_secure, request.headers.get("Origin"))' in ROUTE and "set_cookie" not in ROUTE
    rule = read("helpers", "consent.py").split("def set_answer(", 1)[1].split("\ndef ", 1)[0]
    assert "response.set_cookie(COOKIE, value, max_age=KEEP_SECONDS" in rule and "httponly=False" in rule
    assert 'samesite="Lax"' in rule and "domain=" not in rule
    # sign-up sets it the same way, only from what the rule built, on the answer that follows the account's making (the owner, 2026-10-31)
    sign_up = read("routes", "login.py").split("def sign_up_post(", 1)[1].split("\n@app.route", 1)[0]
    assert 'consent_rules.cookie_value(request.form.get("video_consent"), time.time())' in sign_up
    assert 'consent_rules.set_answer(answer, video_answer, request.is_secure, request.headers.get("Origin"))' in sign_up
    # no-store on whatever answers on this path, and a limited answer is as bare as a refusal
    after = MAIN.split("def after_request(response):", 1)[1]
    assert 'if request.path == consent_rules.ANSWER_PATH:\n        response.headers["Cache-Control"] = "no-store"' in after
    limited = read("routes", "errors.py").split("def error_429(e):", 1)[1].split("@app.errorhandler", 1)[0]
    assert limited.index('r.set(f"429_count_{ip}", count_429s)') < limited.index("if request.path == consent_rules.ANSWER_PATH:") < limited.index("render_template(")


def test_the_path_is_let_through_before_the_database_is_opened():
    hook = MAIN.split("def before_request():", 1)[1]
    first = hook.index("if request.path == consent_rules.ANSWER_PATH:")
    assert first < hook.index("READ_ONLY") and first < hook.index("g.db = db_session()") and first < hook.index("session_cookie.settle(")
    assert "g.skip_session_cookie = True" in hook[first:first + 120]


def test_the_browser_writes_first_then_sends_and_says_nothing_if_it_fails():
    chosen = SCRIPT.split("function choose(choice) {")[1].split("function afterGoogle")[0]
    assert chosen.index("writeCookie(choice);") < chosen.index("sendAnswer(choice);") < chosen.index("follow(choice, had);")
    assert "postAnswer(choice).then(settled, settled);" in SCRIPT                                   # (a failure is the same as an answer)
    post = SCRIPT.split("function postAnswer(choice) {")[1].split("// WebKit keeps")[0]
    assert 'method: "POST"' in post and "keepalive: true" in post and 'body: "choice=" + choice' in post


def test_nothing_is_sent_until_the_reader_answers():
    # the only caller is choosing, and what sends is not reached from loading a page or from the back button
    assert SCRIPT.count("sendAnswer(") == 3                     # itself, the next in line, and choose()
    assert SCRIPT.count("postAnswer(") == 3                     # itself, sendAnswer, and leaving with one in line
    before = SCRIPT.split("var banner = null;")[0]
    assert "sendAnswer" not in before and "fetch(" not in before


def test_a_later_answer_can_not_be_overtaken_by_an_earlier_one():
    send = SCRIPT.split("function sendAnswer(choice) {")[1].split("function choose(choice) {")[0]
    assert "if (sending) {" in send and "waiting = choice;" in send
    assert send.index("writeCookie(next);") < send.index("sendAnswer(next);")
    assert 'window.addEventListener("pagehide"' in send


# --- every player ----------------------------------------------------------------------------------------------------------

def test_the_line_is_inside_the_box_and_marked():
    box = PLAYER.split('<div class="feed-video-thumb">')[1].split("</div>")[0]
    note = box.split('<p class="embed-consent-note">')[1].split("</p>")[0]
    assert '{{ _("Pressing play loads %(player)s, which can set cookies, see your address and record what you watch.", player=player_site(p.embed_url)) }}' in note


def test_where_a_box_has_no_room_the_line_sits_beside_the_button_not_over_it():
    # music always, a video on a phone's width, and only while the line shows (scripts/ui/consent.spec.ts measures them)
    for state in ("ask", "no"):
        music = 'html[data-video-consent="%s"] .embed-lg[data-embed-kind="audio"] ' % state
        assert music + ".embed-lg-play" in STYLE and music + ".embed-consent-note" in STYLE
        assert 'html[data-video-consent="%s"] .embed-lg:not([data-embed-kind="vertical"]):not([data-embed-kind="audio"]) .embed-lg-play' % state in STYLE
    assert "left: calc(3rem + 2 * var(--space-3));" in STYLE.split('.embed-consent-note,\nhtml[data-video-consent="no"] .embed-lg[data-embed-kind="audio"] .embed-consent-note {')[1].split("}")[0]
    # a music box's own line is short: the whole sentence is taller than 120px at the largest text
    assert "{% if p.embed_kind == 'audio' %}{{ _(\"Pressing play loads %(player)s.\", player=player_site(p.embed_url)) }}{% else %}" in PLAYER


def test_the_line_shows_only_while_the_answer_holds_players_and_moves_nothing():
    rule = STYLE.split(".embed-consent-note {")[1].split("}")[0]
    assert "display: none" in rule and "position: absolute" in rule
    assert 'html[data-video-consent="ask"] .embed-consent-note' in STYLE and 'html[data-video-consent="no"] .embed-consent-note' in STYLE
    assert "--glass-chip" in rule


def test_a_spaces_replay_waits_for_a_press_where_the_answer_says_so():
    held = SPACE.split("{% if video_consent() in ('ask', 'no') %}")[1].split("{% else %}")[0]
    assert 'data-embed-load="click"' in held and 'data-embed-kind="iframe"' in held and "<iframe" not in held
    assert 'data-embed-src="{{ boot.replayPlayer }}"' in held and "feed-video-thumb" in held and "embed-consent-note" in held
    # the iframe of old only in the other branch
    other = SPACE.split("{% if video_consent() in ('ask', 'no') %}")[1].split("{% else %}")[1].split("{% endif %}")[0]
    assert '<iframe class="space-replay-frame" src="{{ boot.replayPlayer }}"' in other
    assert SPACE.count("<iframe") == 1
    assert "{% if video_consent() in ('ask', 'no') and boot.replayPlayer %}<script src=\"{{ asset_url('js/feed_video_autoplay.js') }}\"" in SPACE


def test_a_storys_video_waits_too():
    draw = STORIES.split("function drawCard(story) {")[1].split("function drawFooter")[0]
    assert "window.VideoPlayback.consentWaits(document.documentElement.dataset.videoConsent)) drawVideoPress(story);" in draw
    assert "else drawVideoFrame(story);" in draw
    press = draw.split("function drawVideoPress(story) {")[1]
    assert "createElement" not in press and "innerHTML" not in press
    assert press.index("press.remove()") < press.index("drawVideoFrame(story)")
    assert "event.stopPropagation()" in press


# --- the banner --------------------------------------------------------------------------------------------------------------

def test_the_banner_says_what_it_says_in_marked_words():
    for text in ("Videos on this site come from YouTube and other sites. Playing one lets that site set cookies, see your address and record what you watch.",
                 "Privacy page", "Not now", "Allow videos"):
        assert '_("%s")' % text in BANNER, text
    visible = re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", "", BANNER, flags=re.S)
    assert not [t for t in re.findall(r">([^<>]*[A-Za-z][^<>]*)<", visible) if t.strip()]


def test_the_two_buttons_weigh_the_same():
    no, yes = re.findall(r'<button type="button" class="([^"]+)" data-consent-choice="(?:no|yes)"', BANNER)
    assert no == yes == "{{ button }}"
    assert not re.search(r'data-consent-choice="(no|yes)"\]?\s*\{', STYLE)        # and no rule tells them apart


def test_it_is_not_a_dialog():
    assert 'role="dialog"' not in BANNER and 'role="alertdialog"' not in BANNER and "aria-modal" not in BANNER and "modal" not in STYLE
    assert '"Escape"' not in SCRIPT and "keydown" not in SCRIPT


def test_it_is_fixed_over_the_page_and_never_animates():
    rule = STYLE.split(".consent-banner {")[1].split("}")[0]
    assert "position: fixed" in rule
    for word in ("transition", "animation", "@keyframes", "backdrop-filter"):
        assert word not in STYLE, word


def test_it_sits_above_the_phones_bar_and_under_a_toast():
    assert "calc(56px + env(safe-area-inset-bottom) + var(--space-3))" in STYLE
    assert re.search(r"z-index: 990;", STYLE)


def test_it_is_tokens_only():
    # one literal white, on the same tint as the chip it copies (a player's thumbnail is dark in both themes)
    colours = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", STYLE)
    assert colours == ["#fff", "#fff"], colours
    assert not re.search(r"font-size:\s*\d+px", STYLE)


def test_both_sheets_import_it():
    for sheet in SHEETS:
        assert '@import "{ruqquspath}/assets/style/_consent";' in read("assets", "style", sheet)
        assert ".consent-banner" in expanded(sheet)


def test_it_is_first_in_every_shell_that_draws_it():
    assert "{{ consent_banner() }}" in DEFAULT and DEFAULT.index("{{ consent_banner() }}") < DEFAULT.index("<nav")
    assert '{{ consent_banner(button="space-btn") }}' in SPACE
    for shell in (HEADER, DOCS):
        assert "{{ consent_banner(auto=False) }}" in shell and "js/consent.js" in shell
    assert "request.args.get('embed')" in BANNER


def test_only_a_page_with_players_asks_by_itself():
    start = SCRIPT.split("function start() {")[1].split("// Cookie choices")[0]
    assert 'banner.getAttribute("data-consent-auto") === "1"' in start and 'page.getAttribute("data-video-consent") === "ask"' in start


def test_it_waits_for_googles_message_when_the_tag_is_on_the_page():
    wait = SCRIPT.split("function afterGoogle(then) {")[1].split("function start")[0]
    assert "script[src*=\"adsbygoogle.js\"]" in wait and "return then()" in wait                # no tag: at once
    assert "callbackQueue.push({ CONSENT_DATA_READY: go })" in wait
    assert "setTimeout(go, GOOGLE_WAIT_MS)" in wait and "GOOGLE_WAIT_MS = 5000" in SCRIPT


def test_the_script_takes_care_with_cookies_and_never_draws_html():
    assert "innerHTML" not in SCRIPT
    assert "try {" in SCRIPT.split("function writeCookie(choice) {")[1].split("function postAnswer")[0]


# --- Cookie choices ------------------------------------------------------------------------------------------------------------

def test_the_link_is_in_every_place_the_plan_names():
    places = [("templates", "footer.html"), ("templates", "footer_article.html"), ("templates", "footer_bottom.html"), ("templates", "submit.html"),
              ("templates", "default.html"), ("templates", "header.html"),
              ("templates", "settings_filters.html"), ("templates", "help", "privacy.html")]
    for place in places:
        text = read(*place)
        assert "data-consent-open" in text, place
        assert 'href="/help/privacy#videos"' in text or 'href="#videos"' in text, place       # without the script it is still somewhere to go
    # the phone menus, not only the footers (hidden on a phone)
    for shell in (DEFAULT, HEADER):
        assert '<a class="nav-link" href="/help/privacy#videos" data-consent-open><i class="fas fa-cookie-bite fa-fw mr-3"></i>{{ _("Cookie choices") }}</a>' in shell
    # a member changes it in Settings: not in the account menu, nor in the phone menus that stand in for it (the owner, 2026-10-31)
    assert "data-consent-open" not in read("templates", "partials", "account_menu.html")
    for shell in (DEFAULT, HEADER):
        before = shell.split('data-consent-open><i class="fas fa-cookie-bite')[0]
        assert before.rstrip().endswith('<a class="nav-link" href="/help/privacy#videos"')
        assert before.rsplit("{% if", 1)[1].startswith(" not v %}")
    settings = read("templates", "settings_filters.html")
    assert settings.index('id="video"') < settings.index("data-consent-open") < settings.index('id="languages"')


def test_the_phone_menu_gets_out_of_the_way():
    assert '.closest(".navbar-collapse.show").collapse("hide")' in SCRIPT


# --- the privacy page ----------------------------------------------------------------------------------------------------------

def test_the_privacy_page_says_what_happens_to_each_reader():
    assert '<h2 id="videos">Videos from other sites</h2>' in PRIVACY and "data-consent-open" in PRIVACY
    asked, elsewhere = PRIVACY.split("{% if video_consent_asked() %}")[1].split("{% endif %}")[0].split("{% else %}")
    assert "load on scroll" not in asked and "loads by itself" not in asked and "It loads only when you press play" in asked
    assert "Video players that load on scroll" in elsewhere and "Where you are" in elsewhere
    assert "`video_consent`" in PRIVACY and "<code>video_consent</code>" in PRIVACY
    assert "we keep no record of it" in PRIVACY
    assert "also sends your answer to this site once" in PRIVACY


def test_the_privacy_page_owns_up_to_what_the_question_does_not_cover():
    section = PRIVACY.split('<h2 id="videos">')[1].split("{% filter markdown %}")[0]
    assert "load without asking" in section
    assert "nothing from a video site loads" not in section                     # a thumbnail does, as the same section says
    assert "no video site's player loads until you press play" in section and "Its thumbnail can still come from that site" in section
    assert "stays until you clear it there" in section
