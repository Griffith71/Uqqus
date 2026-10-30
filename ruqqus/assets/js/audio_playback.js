// The volume of the site's own audio players (a post's or comment's sound, the editor's Preview, a Space's replay),
// remembered on this device as part of Settings > Content > Video and audio playback > Sound. Audio never starts by
// itself, so there is no autoplay or captions choice for it, and a mute is not remembered (pressing play on a song
// and getting silence is never what was meant). The rules are video_playback.js's; this only listens. The listeners
// sit on the document in the capture phase, because media events don't bubble and players arrive after load
// (comments, live updates, the Preview). A call's or a Space's live audio (a stream, no controls) is left alone, and
// so is the one another site's music player plays in its own frame, which this page can't reach.

(function () {
  var VP = window.VideoPlayback;
  if (!VP) return;

  // localStorage can be missing or throw (a private window, blocked site data): the volume is then just not remembered
  function storage() {
    try {
      return window.localStorage;
    } catch (e) {
      return null;
    }
  }

  // a file with controls, never a call's or a Space's live stream
  function ours(el) {
    return !!el && el.tagName === 'AUDIO' && el.controls && !el.srcObject;
  }

  // the volume each player has been given or has been left at by the reader: a change that matches it is our own
  // (setting the volume raises volumechange too) or a mute, and is not the reader's
  var known = new WeakMap();

  // the first play: start at the volume last used, unless the reader already set this player's, or muted it
  document.addEventListener('play', function (event) {
    var el = event.target;
    if (!ours(el) || known.has(el)) return;
    var saved = VP.readAudioVolume(storage());
    if (saved !== null && !el.muted) el.volume = saved;
    known.set(el, el.volume);
  }, true);

  document.addEventListener('volumechange', function (event) {
    var el = event.target;
    if (!ours(el) || el.muted) return;
    if (known.has(el) && known.get(el) === el.volume) return;
    known.set(el, el.volume);
    VP.writeAudioVolume(storage(), el.volume);
  }, true);
})();
