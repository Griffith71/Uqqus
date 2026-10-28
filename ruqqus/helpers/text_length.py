"""How long a piece of text is, fairly across scripts (the owner's choice, 2026-10-23: weighted like X).

A Chinese, Japanese or Korean character says roughly what two or three Latin letters say, so a limit counted in plain
characters let those scripts say twice as much. As X does (twitter-text's v3 configuration), a character counts 1 in the
ranges that hold Latin, Greek, Cyrillic, Armenian, Hebrew, Arabic, the Indic scripts, Thai and the like (U+0000-U+10FF)
and in the general punctuation X keeps at 1, and 2 everywhere else: CJK, kana, Hangul, full-width forms, emoji. A title
of 280 holds 280 English letters or 140 CJK characters.

An emoji counts once however it is built: the pieces that join onto a character (a zero-width joiner and the emoji
after it, variation selectors, skin tones, flag tags, the keycap mark, the second half of a flag) count 0, and text is
cut only between whole characters (`clip`), never inside one.

assets/js/text_length.js is the same rule for the browser's counters; tests/data/text_length_vectors.json is run
against both (tests/test_text_length.py), so the two can't drift apart. Text is normalised to NFC first, as both sides
see it.
"""
import unicodedata

from ruqqus.helpers.i18n_runtime import number_text, trn

# (first, last) code points that count 1; everything else counts 2 (twitter-text v3 "ranges" at weight 100)
LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))

ZWJ = 0x200D


def _joins(cp):
    """A code point that belongs to the character before it."""
    return (0xFE00 <= cp <= 0xFE0F            # variation selectors
            or 0x1F3FB <= cp <= 0x1F3FF       # skin tones
            or 0xE0020 <= cp <= 0xE007F       # flag tags
            or cp == 0x20E3)                  # keycap


def _flag_half(cp):
    return 0x1F1E6 <= cp <= 0x1F1FF           # regional indicators: two make a flag


def weight(cp):
    for first, last in LIGHT:
        if first <= cp <= last:
            return 1
    return 2


def clusters(text):
    """The text as whole characters: [(the character's text, its weight)]."""
    out = []
    joining = False
    for ch in unicodedata.normalize("NFC", text or ""):
        cp = ord(ch)
        if out and (joining or _joins(cp) or cp == ZWJ
                    or (_flag_half(cp) and _flag_half(ord(out[-1][0][-1])) and len(out[-1][0]) == 1)):
            piece, w = out[-1]
            # a digit or symbol drawn as an emoji (VS16, a keycap, a skin tone) counts as the emoji it is
            out[-1] = (piece + ch, max(w, 2) if cp in (0xFE0F, 0x20E3) or 0x1F3FB <= cp <= 0x1F3FF else w)
            joining = cp == ZWJ
            continue
        out.append((ch, weight(cp)))
        joining = False
    return out


def weighted_length(text):
    """How long `text` is against a limit."""
    return sum(w for _, w in clusters(text))


def clip(text, limit):
    """The longest start of `text` whose weighted length is within `limit`, cut between whole characters."""
    kept, total = [], 0
    for piece, w in clusters(text):
        if total + w > limit:
            break
        kept.append(piece)
        total += w
    return "".join(kept)


def too_long(text, limit):
    return weighted_length(text) > limit


def over_limit(text, limit, stored=None, stored_max=None):
    """Past `limit` as members count it, or past what its column holds: `stored` is the text as it is saved (a title
    escaped by bleach: "&" becomes "&amp;"), `stored_max` the column's size in characters. Joiners count 0 here but
    take room there, so a wall of family emoji or ampersands is refused rather than failing on save."""
    if too_long(text, limit):
        return True
    return stored_max is not None and len(text if stored is None else stored) > stored_max


# One sentence for every refusal, so the rule is explained the same way everywhere
NOTE = "Chinese, Japanese and Korean characters and emoji count as 2"


def _sentence(what, limit):
    """The whole sentence for each thing the site limits (a sentence glued from `what` can't be translated)."""
    count = number_text(limit)
    if what == "Titles":
        return trn("Titles can be %(count)s character (Chinese, Japanese and Korean characters and emoji count as 2).",
                   "Titles can be %(count)s characters (Chinese, Japanese and Korean characters and emoji count as 2).",
                   limit, count=count)
    if what == "A post's text":
        return trn("A post's text can be %(count)s character (Chinese, Japanese and Korean characters and emoji count "
                   "as 2).",
                   "A post's text can be %(count)s characters (Chinese, Japanese and Korean characters and emoji count "
                   "as 2).", limit, count=count)
    if what == "A bio":
        return trn("A bio can be %(count)s character (Chinese, Japanese and Korean characters and emoji count as 2).",
                   "A bio can be %(count)s characters (Chinese, Japanese and Korean characters and emoji count as 2).",
                   limit, count=count)
    if what == "Comments":
        return trn("Comments can be %(count)s character (Chinese, Japanese and Korean characters and emoji count as "
                   "2).",
                   "Comments can be %(count)s characters (Chinese, Japanese and Korean characters and emoji count as "
                   "2).", limit, count=count)
    return None


def limit_message(what, limit):
    """ "Titles can be 280 characters (Chinese, Japanese and Korean characters and emoji count as 2)." `what` is one
    of "Titles", "A post's text", "A bio", "Comments" (each a whole sentence for translation); anything else is
    written in English."""
    return _sentence(what, limit) or f"{what} can be {limit:,} characters ({NOTE})."
