// Keeps a post's sound going when the phone is locked or the reader switches apps, and puts it on the lock screen.
// An <audio> keeps playing in the background by itself; Safari pauses a <video> when the page is hidden, and what
// it offers instead is the lock screen's Now Playing controls, which follow the Media Session set here. Only the
// site's own players count (a post's sound, a Space's sound replay, a linked video file) and only while they play
// with sound: a muted feed video or a GIF never takes the lock screen. A player inside another site's frame
// (YouTube, Spotify...) is that site's to handle; a page can't reach into it. feed_video_autoplay.js leaves its
// players alone while the page is hidden. The lock screen shows the post's title and the site's name, never who
// wrote it (an anonymous post stays anonymous).

(function () {
  var session = navigator.mediaSession;
  var SKIP = 10;                         // seconds, for the lock screen's back and forward buttons
  var active = null;                     // the player the lock screen controls
  var pausedByHand = false;              // the reader paused it (on the page or the lock screen)
  var resumed = false;                   // one automatic resume per trip into the background
  var lastPosition = 0;

  function withSound(el) {
    return !el.muted && el.volume > 0;
  }

  // a file of the site's own or a linked one; never a call's live stream (srcObject), never a GIF
  function counts(el) {
    if (!el || (el.tagName !== 'AUDIO' && el.tagName !== 'VIDEO')) return false;
    if (el.srcObject || !el.currentSrc) return false;
    if (el.closest('.embed-image')) return false;
    return withSound(el);
  }

  function text(node) {
    return node ? (node.textContent || '').replace(/\s+/g, ' ').trim() : '';
  }

  function titleFor(el) {
    var card = el.closest('.card, [id^="post-"]');
    var title = text(card && card.querySelector('.post-title'))
      || text(document.querySelector('h1.post-title'))
      || text(document.getElementById('space-title'));
    return title || document.title;
  }

  function siteName() {
    var meta = document.querySelector('meta[name="application-name"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function safeImage(url) {
    if (!url) return null;
    try {
      var u = new URL(url, location.href);
      return (u.protocol === 'https:' || u.origin === location.origin) ? u.href : null;
    } catch (e) {
      return null;
    }
  }

  function artworkFor(el) {
    var card = el.closest('.card, [id^="post-"], [data-embed-kind]');
    var img = card && card.querySelector('.feed-video-thumb img, .post-img img, img.post-img');
    var src = safeImage(el.poster) || safeImage(img && img.getAttribute('src'));
    return src ? [{ src: src }] : [];
  }

  function setAction(name, handler) {
    try { session.setActionHandler(name, handler); } catch (e) { /* an action this browser doesn't know */ }
  }

  function updatePosition(el) {
    if (!session || !session.setPositionState || !isFinite(el.duration) || el.duration <= 0) return;
    try {
      session.setPositionState({
        duration: el.duration,
        playbackRate: el.playbackRate || 1,
        position: Math.min(Math.max(el.currentTime, 0), el.duration)
      });
    } catch (e) { /* a position the browser refuses */ }
  }

  function seekBy(seconds) {
    if (!active) return;
    var end = isFinite(active.duration) ? active.duration : Infinity;
    active.currentTime = Math.min(Math.max(active.currentTime + seconds, 0), end);
    updatePosition(active);
  }

  function takeOver(el) {
    active = el;
    pausedByHand = false;
    resumed = false;
    if (navigator.audioSession) {
      // media playback: plays with the silent switch on, like any music app
      try { navigator.audioSession.type = 'playback'; } catch (e) { /* read-only here */ }
    }
    if (!session) return;
    try {
      session.metadata = new window.MediaMetadata({ title: titleFor(el), artist: siteName(), artwork: artworkFor(el) });
    } catch (e) { /* no MediaMetadata */ }
    setAction('play', function () {
      pausedByHand = false;
      if (active) active.play().catch(function () {});
    });
    setAction('pause', function () {
      pausedByHand = true;
      if (active) active.pause();
    });
    setAction('seekbackward', function (details) { seekBy(-((details && details.seekOffset) || SKIP)); });
    setAction('seekforward', function (details) { seekBy((details && details.seekOffset) || SKIP); });
    setAction('seekto', function (details) {
      if (!active || !details || typeof details.seekTime !== 'number') return;
      if (details.fastSeek && active.fastSeek) active.fastSeek(details.seekTime);
      else active.currentTime = details.seekTime;
      updatePosition(active);
    });
    updatePosition(el);
  }

  function release(el) {
    if (el !== active) return;
    active = null;
    if (!session) return;
    session.playbackState = 'none';
    try { session.metadata = null; } catch (e) { /* nothing to clear */ }
  }

  document.addEventListener('play', function (event) {
    var el = event.target;
    if (!counts(el)) return;
    if (active && active !== el && !active.paused) active.pause();     // one sound at a time
    if (el !== active) takeOver(el);
    pausedByHand = false;
    if (session) session.playbackState = 'playing';
  }, true);

  document.addEventListener('pause', function (event) {
    var el = event.target;
    if (el !== active) return;
    // Safari pauses a video when the page is hidden; the reader didn't, so try once to carry on (if Safari says
    // no, the lock screen's play button still resumes it)
    if (document.visibilityState === 'hidden' && !pausedByHand && !resumed && !el.ended && el.tagName === 'VIDEO') {
      resumed = true;
      el.play().catch(function () {
        if (session) session.playbackState = 'paused';
      });
      return;
    }
    // a pause while the page is shown is the reader's own (the player's controls)
    if (document.visibilityState === 'visible') pausedByHand = true;
    if (session) session.playbackState = 'paused';
  }, true);

  document.addEventListener('ended', function (event) { release(event.target); }, true);

  // muting a player gives the lock screen back; turning the sound on (a muted feed video) takes it
  document.addEventListener('volumechange', function (event) {
    var el = event.target;
    if (el === active && !withSound(el)) release(el);
    else if (el !== active && !el.paused && counts(el)) {
      if (active && !active.paused) active.pause();
      takeOver(el);
      if (session) session.playbackState = 'playing';
    }
  }, true);

  document.addEventListener('timeupdate', function (event) {
    var el = event.target;
    if (el !== active) return;
    var now = Date.now();
    if (now - lastPosition < 5000) return;
    lastPosition = now;
    updatePosition(el);
  }, true);

  document.addEventListener('loadedmetadata', function (event) {
    if (event.target === active) updatePosition(active);
  }, true);

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') resumed = false;
  });
})();
