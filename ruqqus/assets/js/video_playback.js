// The reader's video playback choices (Settings > Content > Video playback): when a player may start by itself,
// whether captions come on, and what sound a pressed player starts with. The choices are cookies kept by this
// browser (video_autoplay, video_captions, video_sound); partials/theme.html html_attrs() puts the ones that
// differ from the default on <html> as data-video-autoplay / -captions / -sound, so every page knows them before
// anything is drawn. These are the rules only, with no page in them, so node can run them
// (tests/test_video_playback.py); feed_video_autoplay.js is the one place that starts a player and uses them.
// Audio (a post's own sound, a Space's replay: audio_playback.js; Spotify and the other music players: the same
// feed_video_autoplay.js) shares only the Sound choice's volume: audio never plays by itself, has no captions to
// turn on, and "always start muted" would make pressing play on a song give silence.

(function (root) {
  var SOUND_KEY = "video_sound";
  var AUDIO_KEY = "audio_volume";
  var AUDIO_MIN = 0.05;                  // a volume the reader dragged to nothing is a mute, and a mute is not remembered

  function hostOf(src) {
    try {
      return new URL(src).hostname.toLowerCase();
    } catch (e) {
      return "";
    }
  }

  function onDomain(host, domain) {
    return host === domain || host.slice(-(domain.length + 1)) === "." + domain;
  }

  // YouTube and Dailymotion take the reader's captions language in the player's address; every other site
  // plays by its own settings
  function captionParams(host, lang) {
    var code = encodeURIComponent(String(lang || "").replace(/[^A-Za-z0-9-]/g, ""));
    if (onDomain(host, "youtube.com") || onDomain(host, "youtube-nocookie.com")) {
      return "cc_load_policy=1" + (code ? "&cc_lang_pref=" + code + "&hl=" + code : "");
    }
    if (onDomain(host, "dailymotion.com")) return code ? "subtitles_default=" + code : "";
    return "";
  }

  // The address a player frame is built with.
  //   chosen: the reader pressed play (it may start with sound); false: scrolling started it, which is always muted
  //   muted:  the reader's "always start muted" choice, which also holds for a pressed player
  //   captions, lang: the captions choice and the page's language
  //   audio:  a music player (Spotify, SoundCloud...): it is only ever pressed, and pressing play on one must give
  //           sound, so "always start muted" is for video only
  // A site that already has its own autoplay in the address (Twitch, Bilibili) keeps it as it is.
  function playerSrc(src, o) {
    o = o || {};
    var params = [];
    if (!/[?&]autoplay=/i.test(src)) {
      params.push("autoplay=1");
      if (!o.chosen || (o.muted && !o.audio)) params.push("mute=1");
    }
    if (o.captions && !/[?&](cc_load_policy|subtitles_default)=/i.test(src)) {
      var more = captionParams(hostOf(src), o.lang);
      if (more) params.push(more);
    }
    if (!params.length) return src;
    return src + (src.indexOf("?") === -1 ? "?" : "&") + params.join("&");
  }

  // Must the reader press play before another site's player loads, whatever else they chose? While they are being asked
  // ("ask") and after "Not now" ("no"): <html data-video-consent>, helpers/consent.py. Missing or "yes": no.
  function consentWaits(consent) {
    return consent === "ask" || consent === "no";
  }

  // May a player start because it scrolled into view? Not before the reader's answer allows it (consent), and not when they
  // chose to press play, asked their device for less motion, or asked it to save data (Chrome and Android only; other
  // browsers don't say).
  function autoplayAllowed(env) {
    env = env || {};
    return !(consentWaits(env.consent) || env.pref === "tap" || env.reducedMotion || env.saveData);
  }

  function clamp(n) {
    n = Number(n);
    if (!isFinite(n)) return 1;
    return n < 0 ? 0 : n > 1 ? 1 : n;
  }

  // What a direct video file starts with. Scrolling starts it muted whatever was remembered (a page never makes
  // sound by itself); a pressed one uses the last volume and mute the reader set, unless they asked for muted.
  function videoStart(saved, o) {
    o = o || {};
    var volume = saved ? clamp(saved.volume) : 1;
    if (!o.chosen || o.muted) return { muted: true, volume: volume };
    return { muted: saved ? saved.muted === true : false, volume: volume };
  }

  function parseSaved(text) {
    try {
      var data = JSON.parse(text);
      if (!data || typeof data !== "object" || typeof data.volume !== "number" || typeof data.muted !== "boolean") return null;
      return { volume: clamp(data.volume), muted: data.muted };
    } catch (e) {
      return null;
    }
  }

  // storage is localStorage or anything with getItem / setItem; it can be missing or throw (a private window)
  function readSaved(storage) {
    try {
      return storage ? parseSaved(storage.getItem(SOUND_KEY)) : null;
    } catch (e) {
      return null;
    }
  }

  function writeSaved(storage, state) {
    try {
      if (storage) storage.setItem(SOUND_KEY, JSON.stringify({ volume: Math.round(clamp(state.volume) * 100) / 100, muted: !!state.muted }));
    } catch (e) {
      // nothing to do: the volume is simply not remembered
    }
  }

  // The volume of the site's own audio players, kept apart from video's: a song and a clip are rarely wanted at the
  // same level, and a mute is not remembered for audio. A number between AUDIO_MIN and 1, or null.
  function parseVolume(text) {
    var n = parseFloat(text);
    return isFinite(n) && n >= AUDIO_MIN ? clamp(n) : null;
  }

  function readAudioVolume(storage) {
    try {
      return storage ? parseVolume(storage.getItem(AUDIO_KEY)) : null;
    } catch (e) {
      return null;
    }
  }

  // false when there was nothing to keep (muted, or no storage)
  function writeAudioVolume(storage, volume) {
    var v = parseVolume(volume);
    if (v === null) return false;
    try {
      if (!storage) return false;
      storage.setItem(AUDIO_KEY, String(Math.round(v * 100) / 100));
      return true;
    } catch (e) {
      return false;
    }
  }

  var api = {
    playerSrc: playerSrc,
    autoplayAllowed: autoplayAllowed,
    consentWaits: consentWaits,
    videoStart: videoStart,
    readSaved: readSaved,
    writeSaved: writeSaved,
    readAudioVolume: readAudioVolume,
    writeAudioVolume: writeAudioVolume,
    SOUND_KEY: SOUND_KEY,
    AUDIO_KEY: AUDIO_KEY,
    AUDIO_MIN: AUDIO_MIN
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.VideoPlayback = api;
})(typeof window !== "undefined" ? window : this);
