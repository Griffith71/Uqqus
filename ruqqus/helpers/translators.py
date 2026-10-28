"""The translation service (rules in helpers/translation.py): **LibreTranslate**, open source, on our own server
(`libretranslate` in docker-compose.yml, reached only on the compose network): `LIBRETRANSLATE_URL` (and
`LIBRETRANSLATE_API_KEY` if it was given one). Without the address there is no Translate button. It is the site's only
service (the owner, 2026-10-24: Google Cloud Translation was removed).

It is sent the item's sanitized HTML (or its title) and the two languages, nothing about who wrote or reads it; what
comes back is sanitized again by the route before it is kept or drawn.
"""
import time
from os import environ

import requests

from ruqqus.helpers import languages, translation

TIMEOUT = 15                 # a translation (about 0.23 s a short text on LibreTranslate)
LIST_TIMEOUT = 2             # the language list, asked while a page is drawn
LIST_TTL = 600               # LibreTranslate's language list is read again after this long
LIST_KEEP = 3600             # ...and on errors the last good one is kept this long
DETECT_TIMEOUT = (0.3, 0.8)  # connect, read: a second opinion while a post is saved, never a wait
DETECT_PAUSE = 60            # after a failed detect, LibreTranslate isn't asked again for this long
DETECT_CHARS = 500

_languages = {"at": 0, "good_at": 0, "pairs": frozenset(), "codes": {}}
_detect = {"paused_until": 0}


class TranslatorDown(Exception):
    """The service didn't answer, or answered with an error: the reader is told to try again later."""


def libre_url():
    return (environ.get("LIBRETRANSLATE_URL") or "").rstrip("/")


def _libre_auth(payload):
    if environ.get("LIBRETRANSLATE_API_KEY"):
        payload["api_key"] = environ["LIBRETRANSLATE_API_KEY"]
    return payload


def available():
    """Is any translation possible on this site (the button can be drawn at all)?"""
    return bool(libre_url())


def libre_row(code):
    """Our row key for a code LibreTranslate uses ("zh-Hans" -> "zh", "nb" -> "no"), or None."""
    return languages.key_of(languages.LIBRE_ALIASES.get(code, code))


def libre_languages(now=None):
    """(pairs, codes): the (source key, target key) pairs the site's LibreTranslate can translate right now, and the
    code it wants for each key. Read from its /languages, kept LIST_TTL; on an error the last good list is kept for
    LIST_KEEP, then nothing (the button stops showing rather than failing)."""
    if not libre_url():
        return frozenset(), {}
    now = now or time.time()
    if now - _languages["at"] < LIST_TTL:
        return _languages["pairs"], _languages["codes"]
    try:
        r = requests.get(libre_url() + "/languages", timeout=LIST_TIMEOUT)
        r.raise_for_status()
        listed = r.json()
        codes, targets = {}, {}
        for lang in listed:
            key = libre_row(lang["code"])
            if key and key != languages.UNKNOWN:
                codes.setdefault(key, lang["code"])
                targets[key] = {libre_row(t) for t in lang.get("targets", [])} - {None, languages.UNKNOWN, key}
        pairs = frozenset((source, target) for source, ts in targets.items() for target in ts)
        _languages.update(at=now, good_at=now, pairs=pairs, codes=codes)
    except Exception:
        _languages["at"] = now - LIST_TTL + 30             # asked again in 30 s
        if now - _languages["good_at"] > LIST_KEEP:
            _languages.update(pairs=frozenset(), codes={})
    return _languages["pairs"], _languages["codes"]


def libre_pairs(now=None):
    return libre_languages(now)[0]


def libre_keys(now=None):
    """The row keys LibreTranslate has loaded right now."""
    return frozenset(libre_languages(now)[1])


def libre_detect(text, now=None):
    """LibreTranslate's (row key, confidence 0-100) for `text`, None when it has no answer, or languages.UNASKED when
    it couldn't be asked (down, slow, or paused: a code the text already has is then kept): its own detector, asked as
    a second opinion on short or unclear text (helpers/languages.py decide). Short timeouts, and after a failure it
    isn't asked for DETECT_PAUSE, so saving a post never waits on it for long."""
    now = now or time.time()
    text = (text or "").strip()[:DETECT_CHARS]
    if not text or not libre_url():
        return None
    if now < _detect["paused_until"]:
        return languages.UNASKED
    try:
        r = requests.post(libre_url() + "/detect", json=_libre_auth({"q": text}), timeout=DETECT_TIMEOUT)
    except Exception:
        _detect["paused_until"] = now + DETECT_PAUSE      # down or slow: not asked again for a while
        return languages.UNASKED
    if r.status_code >= 500:
        _detect["paused_until"] = now + DETECT_PAUSE
        return languages.UNASKED
    if r.status_code != 200:
        return None                                        # it refused this text, not a sign it is down
    try:
        answers = r.json()
    except ValueError:
        return None
    best = answers[0] if isinstance(answers, list) and answers else None
    if not isinstance(best, dict):
        return None
    key = libre_row(best.get("language"))
    try:
        confidence = float(best.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    return (key, confidence) if key and key != languages.UNKNOWN else None


def translate_libre(texts, source, target, fmt="html"):
    """[translated text] for `texts` (row keys for the languages), by the site's LibreTranslate."""
    codes = libre_languages()[1]
    payload = _libre_auth({"q": texts, "source": codes.get(source, source), "target": codes.get(target, target),
                           "format": fmt})
    try:
        r = requests.post(libre_url() + "/translate", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        out = r.json().get("translatedText")
    except Exception as e:
        raise TranslatorDown(str(e)) from None
    out = out if isinstance(out, list) else [out]
    if len(out) != len(texts) or not all(isinstance(x, str) for x in out):
        raise TranslatorDown("unexpected answer")
    return out, source


def translate(engine, texts, source, target, fmt="html"):
    """([translated text], the source row it was translated from)."""
    if engine == translation.LIBRE:
        return translate_libre(texts, source, target, fmt)
    raise TranslatorDown("no service")
