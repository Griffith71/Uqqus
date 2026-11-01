"""The reader's video choices (Settings > Content > Video playback): the rules in assets/js/video_playback.js, run under
node. They are pure, so these are the cases the settings page promises: what a player's address gets, when scrolling may
start a player, and what a direct video file starts with."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ruqqus" / "assets" / "js" / "video_playback.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

YT = "https://youtube.com/embed/dQw4w9WgXcQ"


def run(expression):
    """Evaluate a JS expression with the rules loaded as `vp`, and give back its JSON."""
    code = "const vp = require(%s); console.log(JSON.stringify(%s));" % (json.dumps(str(SCRIPT)), expression)
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def src(url, **options):
    return run("vp.playerSrc(%s, %s)" % (json.dumps(url), json.dumps(options)))


# --- the address a player is built with --------------------------------------------------------------------------------

def test_a_player_started_by_scrolling_is_muted_and_a_pressed_one_is_not():
    assert src(YT) == YT + "?autoplay=1&mute=1"
    assert src(YT, chosen=True) == YT + "?autoplay=1"
    assert src(YT + "?start=30", chosen=True) == YT + "?start=30&autoplay=1"


def test_always_start_muted_holds_for_a_pressed_player_too():
    assert src(YT, chosen=True, muted=True) == YT + "?autoplay=1&mute=1"


def test_a_site_with_its_own_autoplay_keeps_it():
    twitch = "https://player.twitch.tv/?channel=x&parent=example.com&muted=true&autoplay=true"
    assert src(twitch) == twitch
    assert src(twitch, chosen=True, muted=True) == twitch


def test_captions_are_asked_of_youtube_and_dailymotion_in_the_pages_language():
    assert src(YT, chosen=True, captions=True, lang="fr") == YT + "?autoplay=1&cc_load_policy=1&cc_lang_pref=fr&hl=fr"
    assert src("https://www.youtube-nocookie.com/embed/x", chosen=True, captions=True, lang="zh-Hans") == (
        "https://www.youtube-nocookie.com/embed/x?autoplay=1&cc_load_policy=1&cc_lang_pref=zh-Hans&hl=zh-Hans")
    assert src("https://www.dailymotion.com/embed/video/x1", chosen=True, captions=True, lang="de") == (
        "https://www.dailymotion.com/embed/video/x1?autoplay=1&subtitles_default=de")
    assert src(YT, captions=True) == YT + "?autoplay=1&mute=1&cc_load_policy=1"          # no language known: captions on, nothing more


def test_captions_off_or_another_site_adds_nothing():
    assert src(YT, chosen=True, captions=False, lang="fr") == YT + "?autoplay=1"
    other = "https://player.vimeo.com/video/1"
    assert src(other, chosen=True, captions=True, lang="fr") == other + "?autoplay=1"


def test_only_the_real_hosts_count_and_the_language_is_cleaned():
    for host in ("https://notyoutube.com/embed/x", "https://youtube.com.evil.example/embed/x", "https://evil.example/youtube.com/embed/x"):
        assert src(host, chosen=True, captions=True, lang="fr") == host + "?autoplay=1", host
    assert src(YT, chosen=True, captions=True, lang="fr&evil=1") == YT + "?autoplay=1&cc_load_policy=1&cc_lang_pref=frevil1&hl=frevil1"
    assert src("https://www.youtube.com/embed/x?cc_load_policy=1", chosen=True, captions=True, lang="fr") == (
        "https://www.youtube.com/embed/x?cc_load_policy=1&autoplay=1")                   # already asked for: not asked twice


# --- when scrolling may start a player ---------------------------------------------------------------------------------

def test_scrolling_starts_a_player_unless_the_reader_or_their_device_says_not_to():
    cases = [({}, True), ({"pref": ""}, True), ({"pref": "tap"}, False), ({"reducedMotion": True}, False),
             ({"saveData": True}, False), ({"pref": "auto", "reducedMotion": False, "saveData": False}, True)]
    for env, expected in cases:
        assert run("vp.autoplayAllowed(%s)" % json.dumps(env)) is expected, env
    assert run("vp.autoplayAllowed()") is True


def test_until_the_reader_allows_it_scrolling_never_starts_a_player():
    # <html data-video-consent>: being asked and "Not now" hold every player; "yes" and a reader who isn't asked don't
    for consent in ("ask", "no"):
        assert run("vp.autoplayAllowed(%s)" % json.dumps({"consent": consent})) is False, consent
        assert run("vp.consentWaits(%s)" % json.dumps(consent)) is True, consent
    for consent in ("yes", "", None):
        assert run("vp.autoplayAllowed(%s)" % json.dumps({"consent": consent})) is True, consent
        assert run("vp.consentWaits(%s)" % json.dumps(consent)) is False, consent
    # "yes" answers the question about consent and nothing else: Tap to play and the device's own requests still hold
    for env in ({"pref": "tap"}, {"reducedMotion": True}, {"saveData": True}):
        assert run("vp.autoplayAllowed(%s)" % json.dumps({"consent": "yes", **env})) is False, env


# --- what a direct video file starts with ------------------------------------------------------------------------------

def test_a_video_started_by_scrolling_never_has_sound_whatever_was_remembered():
    saved = {"volume": 0.4, "muted": False}
    assert run("vp.videoStart(%s, {})" % json.dumps(saved)) == {"muted": True, "volume": 0.4}
    assert run("vp.videoStart(null, {})") == {"muted": True, "volume": 1}


def test_a_pressed_video_uses_the_remembered_volume_and_mute():
    assert run("vp.videoStart(null, {chosen: true})") == {"muted": False, "volume": 1}
    assert run("vp.videoStart({volume: 0.4, muted: false}, {chosen: true})") == {"muted": False, "volume": 0.4}
    assert run("vp.videoStart({volume: 0.4, muted: true}, {chosen: true})") == {"muted": True, "volume": 0.4}


def test_always_start_muted_wins_over_what_was_remembered():
    assert run("vp.videoStart({volume: 0.4, muted: false}, {chosen: true, muted: true})") == {"muted": True, "volume": 0.4}


# --- what is remembered ------------------------------------------------------------------------------------------------

def test_the_volume_is_stored_checked_and_rounded():
    memory = ("(() => { const s = {}; const storage = {getItem: k => (k in s ? s[k] : null), setItem: (k, v) => { s[k] = v; }};"
              "vp.writeSaved(storage, {volume: 0.123456, muted: false}); const a = vp.readSaved(storage);"
              "vp.writeSaved(storage, {volume: 7, muted: true}); const b = vp.readSaved(storage); return [a, b, s]; })()")
    first, second, stored = run(memory)
    assert first == {"volume": 0.12, "muted": False}
    assert second == {"volume": 1, "muted": True}                                           # never above full volume
    assert json.loads(stored["video_sound"]) == {"volume": 1, "muted": True}


def test_a_broken_or_missing_store_means_nothing_is_remembered_and_nothing_throws():
    assert run("vp.readSaved(null)") is None
    for text in ("null", "'not json'", "'[]'", "'{}'", "'{\"volume\": \"loud\", \"muted\": false}'", "'{\"volume\": 0.5}'"):
        assert run("vp.readSaved({getItem: () => %s})" % text) is None, text
    assert run("vp.readSaved({getItem: () => { throw new Error('blocked'); }})") is None
    assert run("(() => { vp.writeSaved({setItem: () => { throw new Error('full'); }}, {volume: 1, muted: false}); return 'ok'; })()") == "ok"
    assert run("(() => { vp.writeSaved(null, {volume: 1, muted: false}); return 'ok'; })()") == "ok"


# --- audio -------------------------------------------------------------------------------------------------------------

SPOTIFY = "https://open.spotify.com/embed/track/1"


def test_a_music_player_is_never_muted_by_always_start_muted():
    # pressing play on a song and getting silence is never what was meant; a video of the same setting is still muted
    assert src(SPOTIFY, chosen=True, muted=True, audio=True) == SPOTIFY + "?autoplay=1"
    assert src(SPOTIFY, chosen=True, audio=True) == SPOTIFY + "?autoplay=1"
    assert src(YT, chosen=True, muted=True) == YT + "?autoplay=1&mute=1"


def test_the_audio_volume_is_kept_apart_from_videos_and_never_a_mute():
    def memory(steps):
        script = ("(() => { const s = {}; const storage = {getItem: k => (k in s ? s[k] : null), setItem: (k, v) => { s[k] = v; }};"
                  "const out = []; " + steps + "; out.push(s); return out; })()")
        return run(script)

    got = memory("out.push(vp.readAudioVolume(storage));"
                 "out.push(vp.writeAudioVolume(storage, 0.456)); out.push(vp.readAudioVolume(storage));"
                 "out.push(vp.writeAudioVolume(storage, 0)); out.push(vp.readAudioVolume(storage));"
                 "out.push(vp.writeAudioVolume(storage, 0.01)); out.push(vp.writeAudioVolume(storage, 7)); out.push(vp.readAudioVolume(storage))")
    assert got[0] is None                         # nothing yet
    assert got[1] is True and got[2] == 0.46      # stored rounded
    assert got[3] is False and got[4] == 0.46     # a volume dragged to nothing is a mute: not kept, the last one stays
    assert got[5] is False                        # under the floor
    assert got[6] is True and got[7] == 1         # never above full volume
    assert list(got[8]) == ["audio_volume"]       # its own key: the video's {volume, muted} is untouched
    assert run("vp.AUDIO_KEY") == "audio_volume" and run("vp.SOUND_KEY") == "video_sound"


def test_a_broken_or_hostile_audio_store_remembers_nothing_and_throws_nothing():
    assert run("vp.readAudioVolume(null)") is None
    for text in ("null", "'loud'", "'-1'", "'0'", "'0.01'", "'NaN'", "'Infinity'", "'{}'"):
        got = run("vp.readAudioVolume({getItem: () => %s})" % text)
        assert got is None or got == 1, (text, got)    # Infinity is clamped to full volume, nothing else gets through
    assert run("vp.readAudioVolume({getItem: () => { throw new Error('blocked'); }})") is None
    assert run("vp.writeAudioVolume({setItem: () => { throw new Error('full'); }}, 0.5)") is False
    assert run("vp.writeAudioVolume(null, 0.5)") is False
