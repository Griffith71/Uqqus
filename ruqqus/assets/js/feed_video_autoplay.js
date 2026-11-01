// Scroll-triggered feed video playback. Applies to any post card rendered
// with a `[data-embed-kind]` container (see submission_listing.html's
// "embed_kind" branch) - direct video files, YouTube/Bitchute/Rumble, and
// any domain the server's generic oEmbed/og:video detection picked up
// (ruqqus/helpers/embed.py's detect_video_embed). Only one video is ever
// live at a time, and nothing autoplays inside an unrevealed sensitive-
// content wrapper. A container with data-embed-load="click" (a social site's
// player, music: helpers/social_embeds.player_layout) is never started by
// scrolling: only pressing its thumbnail builds the player, and it stays.
// While the page is hidden (the phone locked, another app) nothing is started,
// paused or taken down, so a sound the reader is listening to keeps going
// (background_media.js); the feed is settled again when the page is back.
// The post page uses the same players (partials/player.html), so the reader's video choices (Settings > Content >
// Video playback: video_playback.js, read from <html data-video-*>) are applied here and nowhere else: tap to
// play instead of autoplay (also for a device asking for reduced motion or data saving), captions by default, and
// the sound a pressed player starts with. The reader's answer to the consent banner (<html data-video-consent>,
// consent.js) is one more of those rules: until it allows them, every player waits for a press, and a press loads that one.

(function () {
  if (!('IntersectionObserver' in window) || !window.VideoPlayback || !window.VideoConsent) return;

  var VP = window.VideoPlayback;
  var page = document.documentElement.dataset;
  var ACTIVATION_RATIO = 0.6;
  var currentlyPlaying = null;
  var deactivateTimer = null;
  // Last known ratio per container. Recomputing the single best candidate
  // from this on every observer callback (rather than reactively switching
  // to whichever element's event happens to arrive last) is what makes
  // this correct during page load: thumbnail images loading in resize
  // their containers and can make two containers transiently both qualify
  // at once, and the one that "wins" that race may never fire another
  // event again once its true resting ratio is reached (IntersectionObserver
  // only calls back on threshold crossings, not continuously) - always
  // re-deriving the best candidate self-heals from that instead of getting
  // stuck on whichever one happened to be processed last in a batch.
  var ratios = new Map();

  function isBlurred(container) {
    var wrap = container.closest('.sensitive-content-wrap');
    return !!(wrap && !wrap.classList.contains('revealed'));
  }

  // localStorage can be missing or throw (a private window, blocked site data): the volume is then just not remembered
  function storage() {
    try {
      return window.localStorage;
    } catch (e) {
      return null;
    }
  }

  // Remember the volume and mute the reader sets on a direct video file (an embedded player is its site's to
  // handle: reaching into it would mean loading that site's scripts). What the page sets itself is not the
  // reader's choice, so the returned function wraps such a change and takes it out of what is remembered.
  function rememberVolume(media) {
    var last = { volume: media.volume, muted: media.muted };
    media.addEventListener('volumechange', function () {
      if (media.volume === last.volume && media.muted === last.muted) return;
      last = { volume: media.volume, muted: media.muted };
      VP.writeSaved(storage(), last);
    });
    return function ours(change) {
      change();
      last = { volume: media.volume, muted: media.muted };
    };
  }

  function buildMedia(container, chosen) {
    var existing = container.querySelector('.feed-video-media');
    if (existing) return existing;

    var kind = container.dataset.embedKind;
    var src = container.dataset.embedSrc;
    var media;
    var ours = null;
    // pressed by the reader: it may play with sound (unless they chose to always start muted); started by scrolling: muted
    var muted = page.videoSound === 'muted';

    if (kind === 'video') {
      media = document.createElement('video');
      var start = VP.videoStart(VP.readSaved(storage()), { chosen: !!chosen, muted: muted });
      media.muted = start.muted;
      media.volume = start.volume;
      media.autoplay = true;
      media.loop = true;
      media.playsInline = true;
      media.controls = true;
      media.src = src;
      ours = rememberVolume(media);
    } else {
      media = document.createElement('iframe');
      // Some providers (Twitch, Bilibili) need their own autoplay/mute
      // param names and already bake them into the src server-side -
      // appending YouTube-style autoplay=1&mute=1 on top would just be
      // ignored or conflict, so video_playback.js only adds it if nothing's there yet.
      media.src = VP.playerSrc(src, {
        chosen: !!chosen,
        muted: muted,
        audio: kind === 'audio',
        captions: page.videoCaptions === 'on',
        lang: document.documentElement.lang
      });
      media.frameBorder = '0';
      // Ruqqus sends Referrer-Policy: same-origin site-wide, which starves
      // YouTube's embedded player of the cross-origin referrer it needs to
      // initialize (surfaces to viewers as "Error 153: Video player
      // configuration error"). Overriding it per-iframe fixes the embed
      // without loosening the site-wide header for anything else.
      media.referrerPolicy = 'strict-origin-when-cross-origin';
      media.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen';
      // Translators: the name of a video player frame when the post gives it no title
      media.title = container.dataset.embedTitle || tr('Video');
      media.allowFullscreen = true;
      // iframe_untrusted = extracted from an arbitrary page's own
      // og:video tag, not a vetted oEmbed provider - no allow-same-origin/
      // allow-top-navigation/allow-popups, so it can't do anything beyond
      // play a video even if hostile.
      if (kind === 'iframe_untrusted') media.setAttribute('sandbox', 'allow-scripts');
    }

    media.className = 'feed-video-media';
    // An <iframe> has no intrinsic size (unlike <img>/<video>, which size
    // from their actual content), so without an explicit size it falls
    // back to the browser default of 300x150 regardless of its container -
    // this is what was making feed videos render far smaller than the
    // space available to them. Setting this directly (rather than through
    // the stylesheet) makes it correct from the first frame for both kinds.
    media.style.width = '100%';
    if (container.dataset.embedHeight) {
      // a music player is its provider's height (the box already has it, so nothing moves)
      media.style.height = container.dataset.embedHeight + 'px';
      media.style.maxHeight = 'none';
    } else if (container.dataset.embedAspect) {
      media.style.aspectRatio = container.dataset.embedAspect;          // 9 / 16 for a vertical video
    } else {
      media.style.aspectRatio = '16 / 9';
    }
    container.appendChild(media);

    var thumb = container.querySelector('.feed-video-thumb');
    if (thumb) thumb.style.display = 'none';

    // A pressed video with sound is started inside the press (Safari only allows that), and falls back to muted
    // if the browser still refuses, which is not something the reader chose and so is not remembered.
    if (ours && !media.muted) {
      var started = media.play();
      if (started && started.catch) {
        started.catch(function () {
          ours(function () { media.muted = true; });
          media.play().catch(function () {});
        });
      }
    }

    return media;
  }

  function activateMedia(container) {
    if (container.dataset.embedKind === 'video') {
      var existing = container.querySelector('.feed-video-media');
      if (existing) {
        existing.play().catch(function () {});
        return;
      }
    }
    // a press on the thumbnail of a player that would have started by itself still counts as pressing play,
    // once: a later start by scrolling (after it was taken down) is muted again
    var pressed = container.dataset.pressed === '1';
    delete container.dataset.pressed;
    buildMedia(container, pressed);
  }

  function deactivateNow(container) {
    var media = container.querySelector('.feed-video-media');
    if (!media) return;

    if (container.dataset.embedKind === 'video') {
      // keep the element so playback position survives scrolling back
      media.pause();
    } else {
      // no generic postMessage pause convention across iframe providers -
      // destroying and rebuilding is the only way to reliably guarantee
      // playback actually stops
      media.remove();
      var thumb = container.querySelector('.feed-video-thumb');
      if (thumb) thumb.style.display = '';
    }
  }

  // The most visible video wins; between equally visible ones (two fully in view, common on a phone) the
  // higher one does: the map is in page order, so only a strictly better ratio replaces an earlier video.
  // (It used to be >=, so the lower video won and the top one was skipped.)
  function bestCandidate() {
    var best = null;
    var bestRatio = ACTIVATION_RATIO;
    ratios.forEach(function (ratio, container) {
      if ((best ? ratio > bestRatio : ratio >= bestRatio) && !isBlurred(container) && container.dataset.embedLoad !== 'click') {
        best = container;
        bestRatio = ratio;
      }
    });
    return best;
  }

  function reconcile() {
    if (document.hidden) return;
    var candidate = bestCandidate();

    if (candidate === currentlyPlaying) {
      // still the right (or still no) candidate - cancel any pending
      // debounced teardown from a previous transient dip
      if (deactivateTimer) {
        clearTimeout(deactivateTimer);
        deactivateTimer = null;
      }
      return;
    }

    if (candidate) {
      if (deactivateTimer) {
        clearTimeout(deactivateTimer);
        deactivateTimer = null;
      }
      if (currentlyPlaying) deactivateNow(currentlyPlaying);
      currentlyPlaying = candidate;
      activateMedia(candidate);
    } else if (currentlyPlaying && !deactivateTimer) {
      // nothing currently qualifies - debounce before tearing down, in
      // case this is a transient layout-reflow dip rather than a real
      // scroll-away
      var toDeactivate = currentlyPlaying;
      deactivateTimer = setTimeout(function () {
        deactivateTimer = null;
        if (document.hidden) return;
        if (!bestCandidate()) {
          deactivateNow(toDeactivate);
          if (currentlyPlaying === toDeactivate) currentlyPlaying = null;
        }
      }, 400);
    }
  }

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      ratios.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0);
    });
    reconcile();
  }, { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1] });

  function playChosen(container) {
    buildMedia(container, true);
  }

  function observeAll() {
    // when scrolling may not start a player, every player is a press-to-play one, like a social site's. (The reader's answer to
    // the consent banner applies at once; their Tap to play and a device's own requests from the next page opened.)
    var pressOnly = !VP.autoplayAllowed(window.VideoConsent.environment());
    document.querySelectorAll('[data-embed-kind]').forEach(function (container) {
      // (kept apart from a player that is press-to-play by nature, so Allow videos can give the others back to scrolling)
      if (pressOnly && container.dataset.embedLoad !== 'click') container.dataset.embedHeld = '1';
      if (!pressOnly && container.dataset.embedHeld) {
        delete container.dataset.embedHeld;
        container.dataset.embedLoad = 'auto';
      }
      if (pressOnly) container.dataset.embedLoad = 'click';
      if (container.dataset.embedLoad === 'click') {
        var button = container.querySelector('.feed-video-thumb');
        if (button && !button.dataset.clickBound) {
          button.dataset.clickBound = '1';
          // the thumbnail and its play button (a real <button>: Enter and Space click it, and the click comes here)
          button.addEventListener('click', function () { playChosen(container); });
        }
        return;
      }
      if (!ratios.has(container)) {
        ratios.set(container, 0);
        observer.observe(container);
      }

      var thumb = container.querySelector('.feed-video-thumb');
      if (thumb && !thumb.dataset.clickBound) {
        thumb.dataset.clickBound = '1';
        thumb.addEventListener('click', function () {
          if (container.dataset.embedLoad === 'click') return playChosen(container);     // held since: "Not now" on this page
          container.dataset.pressed = '1';
          ratios.set(container, 1);
          reconcile();
        });
      }
    });
  }

  // a looping GIF stands still for readers who asked for less motion
  function calmGifs() {
    if (!window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    document.querySelectorAll('.embed-image video').forEach(function (video) {
      video.removeAttribute('autoplay');
      video.pause();
      video.controls = true;
    });
  }

  function start() {
    observeAll();
    calmGifs();
    reconcile();
  }

  // the reader took their consent back: what is loaded goes now, pressed or not, every kind (the next page draws none of it).
  // Not deactivateNow, which keeps a direct video file so scrolling back resumes it: a <video> left in the page still holds
  // its address on the other site, and only losing that address and loading again stops it fetching.
  function takeDown() {
    document.querySelectorAll('[data-embed-kind]').forEach(function (container) {
      var media = container.querySelector('.feed-video-media');
      if (!media) return;
      if (media.tagName === 'VIDEO') {
        media.pause();
        media.removeAttribute('src');
        media.load();
      }
      media.remove();
      var thumb = container.querySelector('.feed-video-thumb');
      if (thumb) thumb.style.display = '';
    });
    currentlyPlaying = null;
  }

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) reconcile();
  });

  // "View anyway" on a sensitive post: the observer only reports a change in how much of a player is on screen,
  // and revealing it changes none, so settle the feed again once the blur is off
  document.addEventListener('click', function (event) {
    if (event.target.closest && event.target.closest('.sensitive-overlay')) setTimeout(reconcile, 0);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  // re-scan hook for any future dynamically-loaded content (pagination etc)
  window.observeFeedVideos = start;
  window.takeDownFeedVideos = takeDown;
})();
