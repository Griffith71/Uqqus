"""Video playback choices (Settings > Content > Video playback): the cookies, the <html> attributes the server names before
the page is drawn, the one script that applies them to every player, and the post page drawing its player as the feed does.
The rules themselves are in tests/test_video_playback.py."""
import re
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


SETTINGS = read("templates", "settings_filters.html")
THEME = read("templates", "partials", "theme.html")
DEFAULT = read("templates", "default.html")
PAGE = read("templates", "submission.html")
ALL_JS = read("assets", "js", "all_js.js")
FEED = read("assets", "js", "feed_video_autoplay.js")
RULES = read("assets", "js", "video_playback.js")
AUDIO = read("assets", "js", "audio_playback.js")
SPACE = read("templates", "space.html")

# name on the page -> (cookie, the one value that differs from the default, the <html> attribute)
CHOICES = {"autoplay": ("video_autoplay", "tap", "data-video-autoplay"),
           "captions": ("video_captions", "on", "data-video-captions"),
           "sound": ("video_sound", "muted", "data-video-sound")}


def html_attrs(cookies):
    """The real macro, drawn with the given cookies."""
    macro = THEME.split("{% macro html_attrs() %}")[1].split("{% endmacro %}")[0]
    env = jinja2.Environment()
    template = env.from_string("{% macro html_attrs() %}" + macro + "{% endmacro %}{{ html_attrs() }}")
    return template.render(request=type("Request", (), {"cookies": cookies})(), ui_lang=lambda: "en", ui_dir=lambda: "ltr")


# --- the <html> attributes -----------------------------------------------------------------------------------------------

def test_the_defaults_add_nothing_to_html():
    assert html_attrs({}) == ' lang="en" dir="ltr" data-density="comfortable"'


def test_only_the_value_the_settings_page_writes_is_named():
    for cookie, value, attribute in CHOICES.values():
        assert html_attrs({cookie: value}).endswith(' %s="%s"' % (attribute, value)), cookie
        for other in ("", "off", "auto", "remember", "TAP", "1", "<script>"):
            assert attribute not in html_attrs({cookie: other}), (cookie, other)


def test_all_three_can_be_named_together_after_the_density():
    drawn = html_attrs({"density": "compact", "video_autoplay": "tap", "video_captions": "on", "video_sound": "muted"})
    assert drawn == (' lang="en" dir="ltr" data-density="compact" data-video-autoplay="tap" '
                     'data-video-captions="on" data-video-sound="muted"')


# --- Settings > Content > Video playback --------------------------------------------------------------------------------

def test_the_settings_page_has_the_group_with_its_three_choices():
    assert '<h2 class="h5 mt-5" id="video">{{ _("Video and audio") }}</h2>' in SETTINGS
    # between Layout and My languages, inside Content, so the settings navigation is untouched
    assert SETTINGS.index('id="layout"') < SETTINGS.index('id="video"') < SETTINGS.index('id="languages"')
    for name, (cookie, value, _attribute) in CHOICES.items():
        assert re.search(r'<select class="form-control" id="video-%s" onchange="setVideoPref\(\'%s\', this.value\)">' % (name, name), SETTINGS), name
        # the selected option is read from the cookie, so a value nobody writes shows the default
        assert "request.cookies.get('%s') == '%s'" % (cookie, value) in SETTINGS, name
        assert '<option value="%s"{%% if request.cookies.get(\'%s\') == \'%s\' %%} selected' % (value, cookie, value) in SETTINGS, name


def test_every_label_and_line_of_the_group_is_marked_for_translation():
    group = SETTINGS.split('id="video"')[1].split('id="languages"')[0]
    texts = re.findall(r">([^<>{}]*[A-Za-z][^<>{}]*)<", re.sub(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", "", group, flags=re.S))
    assert not [t for t in texts if t.strip()], texts          # every visible string is inside {{ _("...") }}
    for text in ("Autoplay", "Captions", "Sound", "Always start videos muted"):
        assert '{{ _("%s") }}' % text in group, text


def test_the_setter_writes_one_cookie_a_year_and_the_attribute_with_it():
    setter = ALL_JS.split("function setVideoPref(name, value) {")[1].split("\n};")[0]
    for cookie, value, _attribute in CHOICES.values():
        assert '["%s", "%s"]' % (cookie, value) in setter, cookie
    assert "max-age=" in setter and "samesite=lax" in setter and 'path=/' in setter
    assert "if (!kept) return;" in setter                      # an unknown name writes nothing
    assert 'setAttribute("data-video-" + name' in setter and 'removeAttribute("data-video-" + name)' in setter


def test_the_theme_macro_keeps_what_the_density_test_pins():
    assert "request.cookies.get('density') == 'compact'" in THEME and 'lang="{{ ui_lang() }}" dir="{{ ui_dir() }}"' in THEME


# --- one script applies them, to the feed and the post page ------------------------------------------------------------------

def test_the_rules_load_before_the_script_that_uses_them():
    assert DEFAULT.index("js/video_playback.js") < DEFAULT.index("js/feed_video_autoplay.js")
    assert "window.VideoPlayback" in FEED and "root.VideoPlayback = api" in RULES


def test_the_feed_script_reads_the_choices_from_html_and_asks_the_rules():
    assert "document.documentElement.dataset" in FEED
    for key in ("page.videoAutoplay", "page.videoCaptions", "page.videoSound"):
        assert key in FEED, key
    assert "VP.playerSrc(src, {" in FEED and "VP.autoplayAllowed(environment())" in FEED
    assert "VP.videoStart(VP.readSaved(storage())" in FEED and "VP.writeSaved(storage(), last)" in FEED
    # the old inline rule is gone: one place builds an address
    assert "startParams" not in FEED


def test_a_device_asking_for_less_motion_or_less_data_gets_tap_to_play():
    env = FEED.split("function environment() {")[1].split("function observeAll()")[0]
    assert "(prefers-reduced-motion: reduce)" in env and "connection.saveData" in env
    observe = FEED.split("function observeAll() {")[1].split("// a looping GIF")[0]
    # every player becomes a press-to-play one before the existing click branch looks at it
    assert observe.index("container.dataset.embedLoad = 'click'") < observe.index("if (container.dataset.embedLoad === 'click')")


def test_the_volume_is_remembered_only_for_what_the_reader_sets_and_storage_cannot_break_a_player():
    storage = FEED.split("function storage() {")[1].split("}\n")[0]
    assert "try {" in storage and "catch (e)" in storage and "return null" in storage
    remember = FEED.split("function rememberVolume(media) {")[1].split("function buildMedia")[0]
    assert "'volumechange'" in remember and "return function ours(change)" in remember
    # a muted fallback after the browser refuses sound is ours, not the reader's, so it is not remembered
    assert "ours(function () { media.muted = true; });" in FEED


def test_a_press_counts_once_and_scrolling_never_starts_with_sound():
    assert "container.dataset.pressed = '1';" in FEED
    assert "delete container.dataset.pressed;" in FEED
    assert "buildMedia(container, pressed);" in FEED
    assert "chosen: !!chosen" in FEED


def test_revealing_a_sensitive_player_settles_the_feed_again():
    # the observer reports a change in how much is on screen, and taking the blur off changes none
    assert "event.target.closest('.sensitive-overlay')) setTimeout(reconcile, 0)" in FEED
    assert "function isBlurred(container)" in FEED and "!wrap.classList.contains('revealed')" in FEED


def test_the_post_page_draws_every_player_as_the_feed_does():
    embed = PAGE.split("{% if p.embed_kind and p.embed_kind != 'widget' %}")[1].split("{% elif p.embed_url and p.domain_obj")[0]
    assert "{% set layout = player_of(p) %}" in embed and "{{ player_box(p, layout) }}" in embed
    # no player of its own that plays at page load, behind the sensitive blur or against the reader's choices
    assert "autoplay" not in re.sub(r"\{#.*?#\}", "", embed, flags=re.S)
    assert "<video" not in embed and "<iframe" not in embed
    assert 'sensitive-content-wrap' in embed and "revealSensitive(" in embed


def test_nothing_in_the_script_names_who_wrote_a_post():
    for word in ("author", "username", "shown_name"):
        assert word not in RULES


# --- audio: the Sound choice's volume, nothing else ------------------------------------------------------------------------

def test_the_audio_script_loads_after_the_rules_and_before_the_lock_screen_script_reads_the_volume():
    assert DEFAULT.index("js/video_playback.js") < DEFAULT.index("js/audio_playback.js") < DEFAULT.index("js/background_media.js")
    # a Space's replay is a site-served sound too: it needs the rules and the listener, before the lock screen script
    assert SPACE.index("js/video_playback.js") < SPACE.index("js/audio_playback.js") < SPACE.index("js/background_media.js")
    assert "window.VideoPlayback" in AUDIO


def test_the_audio_script_listens_on_the_document_so_players_that_arrive_later_are_covered():
    assert "document.addEventListener('play'" in AUDIO and "document.addEventListener('volumechange'" in AUDIO
    assert AUDIO.count("}, true);") == 2                       # the capture phase: media events don't bubble


def test_the_audio_script_never_touches_a_live_stream_or_a_mute():
    ours = AUDIO.split("function ours(el) {")[1].split("}\n")[0]
    assert "el.tagName === 'AUDIO'" in ours and "el.controls" in ours and "!el.srcObject" in ours
    changed = AUDIO.split("document.addEventListener('volumechange'")[1]
    assert "el.muted) return;" in changed                       # a mute is never remembered
    assert "known.get(el) === el.volume" in changed             # nor a change the page made itself
    first = AUDIO.split("document.addEventListener('play'")[1].split("}, true);")[0]
    assert "!el.muted" in first and "known.has(el)" in first    # only a player the reader has not already set, and not a muted one


def test_audio_storage_is_in_try_catch_and_names_nobody():
    storage = AUDIO.split("function storage() {")[1].split("}\n")[0]
    assert "try {" in storage and "catch (e)" in storage and "return null" in storage
    for word in ("author", "username", "shown_name", "innerHTML"):
        assert word not in AUDIO


def test_a_music_player_is_built_as_audio_so_it_is_not_muted():
    assert "audio: kind === 'audio'," in FEED
    assert "(o.muted && !o.audio)" in RULES


def test_the_settings_text_says_what_audio_does():
    group = SETTINGS.split('id="video"')[1].split('id="languages"')[0]
    assert "Audio never plays by itself." in group
    assert "Audio players also start at the volume you last used, and are never muted by this." in group
