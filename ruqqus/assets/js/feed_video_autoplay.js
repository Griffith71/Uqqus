// Scroll-triggered feed video playback. Applies to any post card rendered
// with a `[data-embed-kind]` container (see submission_listing.html's
// "embed_kind" branch) - direct video files, YouTube/Bitchute/Rumble, and
// any domain the server's generic oEmbed/og:video detection picked up
// (ruqqus/helpers/embed.py's detect_video_embed). Only one video is ever
// live at a time, and nothing autoplays inside an unrevealed sensitive-
// content wrapper.

(function () {
  if (!('IntersectionObserver' in window)) return;

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

  function buildMedia(container) {
    var existing = container.querySelector('.feed-video-media');
    if (existing) return existing;

    var kind = container.dataset.embedKind;
    var src = container.dataset.embedSrc;
    var media;

    if (kind === 'video') {
      media = document.createElement('video');
      media.muted = true;
      media.autoplay = true;
      media.loop = true;
      media.playsInline = true;
      media.controls = true;
      media.src = src;
    } else {
      media = document.createElement('iframe');
      // Some providers (Twitch, Bilibili) need their own autoplay/mute
      // param names and already bake them into the src server-side -
      // appending YouTube-style autoplay=1&mute=1 on top would just be
      // ignored or conflict, so only add it if nothing's there yet.
      media.src = /[?&]autoplay=/i.test(src)
        ? src
        : src + (src.indexOf('?') === -1 ? '?' : '&') + 'autoplay=1&mute=1';
      media.frameBorder = '0';
      // Ruqqus sends Referrer-Policy: same-origin site-wide, which starves
      // YouTube's embedded player of the cross-origin referrer it needs to
      // initialize (surfaces to viewers as "Error 153: Video player
      // configuration error"). Overriding it per-iframe fixes the embed
      // without loosening the site-wide header for anything else.
      media.referrerPolicy = 'strict-origin-when-cross-origin';
      media.allow = 'accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture';
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
    media.style.aspectRatio = '16 / 9';
    container.appendChild(media);

    var thumb = container.querySelector('.feed-video-thumb');
    if (thumb) thumb.style.display = 'none';

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
    buildMedia(container);
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

  function bestCandidate() {
    var best = null;
    var bestRatio = ACTIVATION_RATIO;
    ratios.forEach(function (ratio, container) {
      if (ratio >= bestRatio && !isBlurred(container)) {
        best = container;
        bestRatio = ratio;
      }
    });
    return best;
  }

  function reconcile() {
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

  function observeAll() {
    document.querySelectorAll('[data-embed-kind]').forEach(function (container) {
      if (!ratios.has(container)) {
        ratios.set(container, 0);
        observer.observe(container);
      }

      var thumb = container.querySelector('.feed-video-thumb');
      if (thumb && !thumb.dataset.clickBound) {
        thumb.dataset.clickBound = '1';
        thumb.addEventListener('click', function () {
          ratios.set(container, 1);
          reconcile();
        });
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', observeAll);
  } else {
    observeAll();
  }

  // re-scan hook for any future dynamically-loaded content (pagination etc)
  window.observeFeedVideos = observeAll;
})();
