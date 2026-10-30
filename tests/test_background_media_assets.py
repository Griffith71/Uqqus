"""A post's sound keeps going with the phone locked (assets/js/background_media.js, feed_video_autoplay.js)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "ruqqus" / "assets" / "js"
TEMPLATES = ROOT / "ruqqus" / "templates"


def _script():
    return (JS / "background_media.js").read_text()


def test_every_page_with_a_player_loads_it():
    tag = "asset_url('js/background_media.js')"
    assert tag in (TEMPLATES / "default.html").read_text()
    space = (TEMPLATES / "space.html").read_text()
    assert tag in space                                  # its sound replay
    assert 'name="application-name"' in space            # the lock screen's second line


def test_it_drives_the_lock_screen_when_the_browser_can():
    script = _script()
    assert "navigator.mediaSession" in script and "if (!session) return;" in script
    for action in ("'play'", "'pause'", "'seekbackward'", "'seekforward'", "'seekto'"):
        assert f"setAction({action}" in script
    assert "setPositionState" in script
    assert "if (navigator.audioSession)" in script and "'playback'" in script


def test_only_the_sites_own_players_with_sound_count():
    script = _script()
    counts = script.split("function counts(")[1].split("\n  }\n")[0]
    assert "el.srcObject" in counts                     # never a call's live stream
    assert ".embed-image" in counts                     # never a GIF
    assert "withSound(el)" in counts
    assert "!el.muted && el.volume > 0" in script


def test_a_pause_the_reader_made_is_never_undone():
    script = _script()
    pause = script.split("document.addEventListener('pause'")[1].split("}, true);")[0]
    assert "document.visibilityState === 'hidden'" in pause
    assert "!pausedByHand" in pause and "!resumed" in pause
    assert "pausedByHand = true" in script.split("setAction('pause'")[1].split("});")[0]


def test_it_never_names_who_wrote_it():
    script = _script()
    for word in ("author", "username", "shown_name", ".user-name"):
        assert word not in script
    assert "innerHTML" not in script


def test_the_feed_leaves_its_players_alone_while_the_page_is_hidden():
    script = (JS / "feed_video_autoplay.js").read_text()
    reconcile = script.split("function reconcile() {")[1]
    assert reconcile.lstrip().startswith("if (document.hidden) return;")
    timer = script.split("deactivateTimer = setTimeout(function () {")[1].split("}, 400);")[0]
    assert "if (document.hidden) return;" in timer
    assert "if (!document.hidden) reconcile();" in script
