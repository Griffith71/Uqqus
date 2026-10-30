"""Word filter engine: how offensive is this text, for the two filter levels.

    severity 0  clean
    severity 1  profanity      - hidden for viewers on the Child filter
    severity 2  extreme terms  - hidden for Standard and Child viewers

It is deliberately not a blacklist of exact strings. Every token is reduced to
a "skeleton" of plain a-z letters before it is compared with the word list, so
the usual ways of dodging a filter are folded away first:

    case, accents, zero-width characters      FuCk, fúck, f\u200buck
    look-alike letters from other alphabets   fuсk (Cyrillic с), fullwidth, maths fonts
    digits and symbols standing in for letters   sh1t, a55, @ss, f*ck
    punctuation inside a word                  f.u.c.k, f-u-c-k
    spelled-out letters                        f u c k
    repeated letters                           fuuuuck
    words split by formatting tags (HTML)      f<b>u</b>ck

Matching is on WHOLE tokens, which is what keeps innocent words that merely
contain a listed word safe ("classic", "Scunthorpe", "cockpit"). Only entries
explicitly marked mode="anywhere" match inside longer tokens.

Other scripts (2026-10-29). A listed word written in another script than
Latin (Cyrillic, Greek, Arabic, Hebrew, Devanagari, Han...) takes a "native"
path: it is compared WITHOUT the look-alike folding above, so a Cyrillic entry
matches Cyrillic text only and is never reached through a Latin skeleton (and
never fires on an English word). Native text is normalised with NFKC and
casefold, format and zero-width characters dropped, Arabic tashkeel and tatweel
and Hebrew niqqud dropped, Greek accents dropped, Arabic letter forms unified
(alef with hamza or madda, alef maksura and Persian yeh, keheh), Russian ё read
as е; invisible marks dropped too (the combining grapheme joiner, variation
selectors, a stress accent on a Cyrillic vowel, the blank Hangul fillers); the
vowel signs and other marks of the scripts that write them on their own letters
(Indic, Thai, Lao, Khmer, Myanmar...) are kept (they are letters). Repeated letters
are handled as on the Latin path (each run at least as long as the listed
word's). An Arabic-script token is also tried without an attached proclitic
(و ف ب ل ك and ال), and only against listed words of 3 letters or more.

Scripts written without spaces (Han, Hiragana, Katakana, Hangul, Thai, Lao,
Khmer, Myanmar) have no tokens to compare, so their entries are found anywhere
in the text (substrings, at least 2 characters; spaces and dots between two such
characters are read through, a line break is not), and an allowed entry in such
a script is masked out of the text first, so an innocent compound protects the
word inside it. They are matched with one compiled pattern per severity.

A word in another language written in Latin letters (German, Spanish...) is an
ordinary Latin entry: accents are folded as for English.

An allowed word excuses only itself: a listed word joined to it by punctuation
("<slur>-<allowed>") is still read, and in the scripts written without spaces
an allowed compound protects only the listed words that lie wholly inside it.

The version (WordFilter.version) is a digest of the list AND of ENGINE_REVISION,
so a change to the matching rules re-rates everything once, whatever the list.

What it cannot do: new coinages, euphemisms, coded terms, reversed or half
split words ("fu ck"), misspellings that are real words ("duck you"), text in
images, look-alike dodges inside a non-Latin script. The word list needs human
upkeep.

Pure standard library and no Flask/database imports, so it is unit-testable.
"""
import hashlib
import re
import unicodedata
from functools import lru_cache
from html.parser import HTMLParser

PROFANITY = 1
EXTREME = 2

# Part of every list's version: bump it whenever the matching rules change, so the stored ratings (made by the older
# rules) are seen as stale and the re-scan rates everything again. 2: other scripts, invisible marks, an allowed word
# excusing only itself (2026-10-29).
ENGINE_REVISION = 2

# Suffixes tried on every entry unless the entry gives its own list.
DEFAULT_SUFFIXES = ("s", "es", "ed", "er", "ers", "ing", "in")

# Characters from other scripts (and a few Latin oddities) that look like a
# plain Latin letter. NFKD already folds fullwidth/maths/circled forms and
# splits accents off, so this only needs the true look-alikes.
CONFUSABLES = {
    # Cyrillic
    "а": "a", "в": "b", "с": "c", "ԁ": "d", "е": "e", "ё": "e", "ғ": "f", "г": "r",
    "һ": "h", "н": "h", "і": "i", "ї": "i", "ј": "j", "к": "k", "ӏ": "l", "м": "m",
    "п": "n", "о": "o", "р": "p", "ԛ": "q", "ѕ": "s", "т": "t", "ц": "u", "у": "y",
    "ѵ": "v", "ԝ": "w", "ш": "w", "х": "x", "з": "e", "и": "n", "я": "r", "ь": "b",
    # Greek
    "α": "a", "β": "b", "ϲ": "c", "ε": "e", "η": "n", "ι": "i", "κ": "k", "μ": "u",
    "ν": "v", "ο": "o", "ρ": "p", "σ": "o", "τ": "t", "υ": "u", "χ": "x", "ω": "w",
    "γ": "y", "ϳ": "j",
    # Latin letters NFKD leaves alone
    "ı": "i", "ł": "l", "ø": "o", "đ": "d", "ħ": "h", "ɑ": "a", "ɡ": "g", "ʟ": "l",
    "ß": "ss", "æ": "ae", "œ": "oe", "þ": "p", "ƒ": "f",
}

# Digits and symbols used in place of letters. A value with two letters means
# "could be either" (both are tried).
LEET = {
    "0": "o", "1": "il", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g",
    "@": "a", "$": "s", "!": "i", "|": "il", "+": "t", "€": "e", "£": "l", "¡": "i",
}
WILDCARD = "*"

_BLOCK_TAGS = frozenset(
    "p div br li ul ol h1 h2 h3 h4 h5 h6 blockquote pre table tr td th hr section article".split())


def _fold(text):
    """Lowercase, strip accents/zero-width characters, fold look-alikes."""
    out = []
    for ch in unicodedata.normalize("NFKD", text):
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf"):  # accents, enclosing marks, zero-width/format
            continue
        ch = ch.casefold()
        out.append(CONFUSABLES.get(ch, ch))
    return "".join(out)


# ------------------------------------------------------------ other scripts
# Scripts written without spaces between words: their entries are substrings of the text.
SPACELESS_SCRIPTS = frozenset({"Han", "Hiragana", "Katakana", "Hangul", "Thai", "Lao", "Khmer", "Myanmar"})
MIN_SPACELESS = 2      # a one-character "word" in these scripts would hide half of everything
ARABIC_MIN_BARE = 3    # a token with a proclitic taken off is compared only with listed words this long

# the first word of a character's Unicode name -> its script (the rest are title-cased: "CYRILLIC" -> "Cyrillic")
_SCRIPT_NAMES = {"CJK": "Han", "IDEOGRAPHIC": "Han", "KANGXI": "Han", "HIRAGANA": "Hiragana", "KATAKANA": "Katakana",
                 "KATAKANA-HIRAGANA": "Katakana", "HANGUL": "Hangul", "MODIFIER": "", "COMBINING": ""}

_SPACELESS_CLASS = ("\u0e00-\u0eff\u1000-\u109f\u1100-\u11ff\u1780-\u17ff\u19e0-\u19ff\u3005-\u3007"
                    "\u3040-\u30ff\u3130-\u318f\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f"
                    "\ua9e0-\ua9ff\uaa60-\uaa7f\uac00-\ud7ff\uf900-\ufaff\U00020000-\U0003134f")
# is there anything to scan at all (halfwidth katakana and hangul become the real ones under NFKC)
_SPACELESS_ANY = re.compile("[" + _SPACELESS_CLASS + "\uff66-\uffdc]")
# a space, a dot or a dash or two between two such characters is a dodge ("傻 逼", "傻.逼"); a line break is not
_SPACELESS_JOIN = re.compile("(?<=[" + _SPACELESS_CLASS + "])[ \t.\\-_*\u00b7\u30fb\u3000]{1,3}(?=["
                             + _SPACELESS_CLASS + "])")

# Invisible in native text, so a dodge when put inside a word: the combining grapheme joiner, the Mongolian free
# variation selectors and the Hangul fillers (blank letters: a filler between two syllables reads as nothing)
_INVISIBLE = frozenset({0x034F, 0x180B, 0x180C, 0x180D, 0x180F, 0x115F, 0x1160, 0x3164, 0xFFA0})
# ... and every mark in these blocks: the variation selectors and the generic combining marks (an accent or a stress
# mark over a Cyrillic vowel, an overlay, a strike-through)
_INVISIBLE_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF), (0x0300, 0x036F), (0x1AB0, 0x1AFF), (0x1DC0, 0x1DFF),
                     (0x20D0, 0x20FF), (0xFE20, 0xFE2F))
# scripts that name some of their marks but write no letters with them: a tone mark on Hangul or Han is decoration
_MARKLESS = frozenset({"Han", "Hangul", "Hiragana", "Katakana", "Latin", "Cyrillic", "Greek"})


def _invisible(cp):
    return cp in _INVISIBLE or any(lo <= cp <= hi for lo, hi in _INVISIBLE_RANGES)


# Arabic letters written several ways
_NATIVE_FOLD = {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ی": "ي", "ک": "ك",
    "ё": "е",
}
# an Arabic token may start with a conjunction or preposition written onto the word, with or without the article
_ARABIC_PROCLITICS = tuple(sorted(
    {p for p in ("ال", "و", "ف", "ب", "ل", "ك", "بال", "كال", "لل", "وال", "فال")}
    | {c + p for c in "وف" for p in ("ال", "ب", "ل", "ك", "بال", "كال", "لل")},
    key=len, reverse=True))


@lru_cache(maxsize=8192)
def char_script(ch):
    """The script of one character: "Latin", "Cyrillic", "Han", "Hangul"... or "" when it has none of its own."""
    ch = unicodedata.normalize("NFKC", ch)[:1]
    if not ch:
        return ""
    first = unicodedata.name(ch, "").split(" ", 1)[0]
    if first in _SCRIPT_NAMES:
        return _SCRIPT_NAMES[first]
    return first.title()


def word_script(word):
    """The script a listed word is written in: None (no letters), "Latin", or another script's name (a word mixing
    scripts is the non-Latin one, a spaceless one first)."""
    scripts = {char_script(c) for c in unicodedata.normalize("NFKC", word or "") if unicodedata.category(c)[0] == "L"}
    scripts.discard("")
    if not scripts:
        return None
    if scripts == {"Latin"}:
        return "Latin"
    others = sorted(scripts - {"Latin"})
    spaceless = [s for s in others if s in SPACELESS_SCRIPTS]
    return (spaceless or others)[0]


def _exact(word):
    """A word as written, for an allowed word with accents: NFC, casefolded, without the punctuation around it."""
    word = unicodedata.normalize("NFC", word).casefold()
    start, end = 0, len(word)
    while start < end and unicodedata.category(word[start])[0] not in "LM":
        start += 1
    while end > start and unicodedata.category(word[end - 1])[0] not in "LM":
        end -= 1
    return word[start:end]


@lru_cache(maxsize=8192)
def _is_spaced_foreign(ch):
    """A letter or mark (after folding) of a script other than Latin that is written with spaces between words."""
    if unicodedata.category(ch)[0] not in "LM":
        return False
    script = char_script(ch)
    return script not in ("", "Latin") and script not in SPACELESS_SCRIPTS


def _has_foreign_letter(token):
    return any(unicodedata.category(c)[0] == "L" and char_script(c) not in ("Latin", "") for c in token)


def _native_fold(text):
    """NFKC, casefold, no format characters, no Arabic tashkeel or tatweel, no Hebrew niqqud, no Greek accents, no
    invisible marks (see _INVISIBLE); Arabic letter forms and ё unified. A mark is kept only where it is a letter: a
    vowel sign or another mark of a script that writes them (Indic, Thai, Lao, Khmer, Myanmar...) on a letter of that
    same script. Everything else is kept."""
    out = []
    base = ""        # the script of the letter the next mark would sit on
    for ch in unicodedata.normalize("NFKC", text).casefold():
        cp = ord(ch)
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Me") or cp == 0x0640 or _invisible(cp):
            continue
        if cat == "Mn":
            if 0x0591 <= cp <= 0x05C7 or 0x0610 <= cp <= 0x06FF or 0x08D3 <= cp <= 0x08FF:
                continue
            script = char_script(ch)
            if script and script == base and script not in _MARKLESS:
                out.append(ch)
            continue
        if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
            out.extend(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn")
            base = "Greek"
            continue
        out.append(_NATIVE_FOLD.get(ch, ch))
        if cat[0] == "L":
            base = char_script(ch)
        elif cat[0] != "M":
            base = ""
    return "".join(out)


def _native_chunks(folded):
    """Runs of letters and marks."""
    chunks, current = [], []
    for ch in folded:
        if unicodedata.category(ch)[0] in "LM":
            current.append(ch)
        elif current:
            chunks.append("".join(current))
            current = []
    if current:
        chunks.append("".join(current))
    return chunks


def _native_letters(word):
    return "".join(_native_chunks(_native_fold(word)))


def _arabic_bare(word):
    """The word with an attached proclitic taken off, each way it could be read."""
    for prefix in _ARABIC_PROCLITICS:
        if word.startswith(prefix) and len(word) - len(prefix) >= ARABIC_MIN_BARE:
            yield word[len(prefix):]


def _alternation(keys):
    """One pattern for many literal run keys, the longest first."""
    return "|".join(re.escape(k) for k in sorted(set(keys), key=lambda k: (-len(k), k)))


def _counts_ok(counts, start, min_counts):
    return all(counts[start + j] >= m for j, m in enumerate(min_counts))


def _runs(word):
    """'fuuck' -> ('fuck', (1, 2, 1, 1))"""
    key, counts = [], []
    for ch in word:
        if key and key[-1] == ch:
            counts[-1] += 1
        else:
            key.append(ch)
            counts.append(1)
    return "".join(key), tuple(counts)


def _contains_runs(key, counts, stem_key, stem_counts):
    """Does a word (as letter runs) contain a listed stem, with every run at
    least as long as the stem's? ("xcoonx" contains "coon"; "condition" does not)"""
    n = len(stem_key)
    for i in range(len(key) - n + 1):
        if key[i:i + n] == stem_key and all(counts[i + j] >= stem_counts[j] for j in range(n)):
            return True
    return False


def _is_letter(ch):
    return "a" <= ch <= "z"


def _is_wordchar(ch):
    return _is_letter(ch) or ch in LEET or ch == WILDCARD


def _single_letter(token):
    """One character read as a letter: a-z after folding, a stand-in for one, or a letter of another script."""
    folded = _fold(token)
    if len(folded) == 1 and _is_wordchar(folded):
        return True
    return len(token) == 1 and unicodedata.category(token)[0] == "L" and char_script(token) not in ("", "Latin")


def _leet_variants(word):
    """Letters for a chunk made of a-z, leet characters and wildcards.
    Returns [] unless there is at least one real letter (so '55' or '!!!' are
    never read as words)."""
    if not any(_is_letter(c) for c in word):
        return []
    variants = [""]
    for ch in word:
        options = LEET.get(ch, ch)
        variants = [v + o for v in variants for o in options][:8]
    return variants


class WordFilter:
    """Compiled word list.

    entries: iterable of dicts
        word      the base word: plain letters, in any script
        severity  PROFANITY (1) or EXTREME (2); 0 makes it an allowed word
        mode      "word" (whole token, default) or "anywhere" (inside tokens too; always so for a script written
                  without spaces)
        variants  other spellings of the same word, optional
        suffixes  endings to accept; None means DEFAULT_SUFFIXES for a Latin word and none for another script
        language  a language code, optional: says where the word comes from, never how it is matched
    allow_phrases: iterable of phrases whose words are never counted
        ("maine coon"), each a space separated string
    """

    def __init__(self, entries, allow_phrases=()):
        self._forms = {}        # run key -> [(min run counts, severity, base word)]
        self._by_length = {}    # form length -> [(form, severity, base word)]  (wildcards)
        self._anywhere = []     # (letter runs of the word, severity, base word)
        self._allowed = set()
        self._allowed_exact = {}      # allowed words with accents, compared as written -> skeleton (see _add_latin)
        self._allow_phrases = {}
        # other scripts (see the module's docstring)
        self._native_forms = {}       # run key -> [(min run counts, severity, base word)]
        self._native_anywhere = []    # (letter runs of the word, severity, base word)
        self._native_allowed = set()
        self._native_phrases = {}     # first word -> [words]
        self._spaceless_forms = {}    # severity -> {run key: [(min run counts, base word)]}
        self._spaceless_allowed = set()
        digest = hashlib.sha256()
        digest.update(repr(("engine", ENGINE_REVISION)).encode())

        for entry in sorted(entries, key=lambda e: (e["word"], e.get("severity", 0))):
            script = word_script(entry["word"])
            if script in (None, "Latin"):
                self._add_latin(entry, digest)
            else:
                self._add_native(entry, script, digest)

        for phrase in allow_phrases:
            script = word_script(phrase)
            if script in (None, "Latin"):
                words = tuple(self._letters(w) for w in phrase.split())
                if len(words) > 1:
                    self._allow_phrases.setdefault(words[0], []).append(words)
                    digest.update(repr(words).encode())
            elif script in SPACELESS_SCRIPTS:
                joined = _native_letters(phrase)
                if len(joined) >= MIN_SPACELESS:
                    self._spaceless_allowed.add(joined)
                    digest.update(repr(("spaceless phrase", joined)).encode())
            else:
                words = tuple(w for w in (_native_letters(w) for w in phrase.split()) if w)
                if len(words) > 1:
                    self._native_phrases.setdefault(words[0], []).append(words)
                elif words:
                    self._native_allowed.add(words[0])
                digest.update(repr(("native phrase", words)).encode())

        # an accented allowed word never excuses an extreme listed word written with an accent ("fág" is never the
        # way round the slur its skeleton spells)
        for exact, skeleton in list(self._allowed_exact.items()):
            if self._match_word(skeleton, allowed=False)[0] >= EXTREME:
                del self._allowed_exact[exact]

        self._has_native = bool(self._native_forms or self._native_anywhere or self._native_allowed)
        # the patterns read the text with its repeated letters collapsed ("笨笨蛋" -> "笨蛋" with counts 2, 1), so
        # they are plain alternations of run keys and a long run of one character costs nothing; counts are
        # checked on each match
        self._spaceless_res = [
            (severity, re.compile("(?=(" + _alternation(self._spaceless_forms[severity]) + "))"))
            for severity in sorted(self._spaceless_forms, reverse=True)]
        self._spaceless_allowed_runs = {}
        for form in self._spaceless_allowed:
            key, counts = _runs(form)
            self._spaceless_allowed_runs.setdefault(key, []).append(counts)
        self._spaceless_mask = (re.compile("(?=(" + _alternation(self._spaceless_allowed_runs) + "))")
                                if self._spaceless_allowed_runs else None)
        self._spaceless_allowed_longest = max((len(k) for k in self._spaceless_allowed_runs), default=0)

        self.version = digest.hexdigest()[:12]

    def _add_latin(self, entry, digest):
        base = self._letters(entry["word"])
        if not base:
            return
        severity = int(entry.get("severity", 0))
        mode = entry.get("mode") or "word"
        variants = [self._letters(v) for v in (entry.get("variants") or [])]
        suffixes = entry.get("suffixes")
        if suffixes is None:
            suffixes = DEFAULT_SUFFIXES
        digest.update(repr((base, severity, mode, variants, tuple(suffixes))).encode())
        if entry.get("language"):
            digest.update(repr(("language", base, entry["language"])).encode())

        if severity <= 0:
            # an allowed word written with letters beyond a-z ("fág", Irish "leave") is allowed exactly as written:
            # its skeleton ("fag") is a listed word of its own
            for raw, skeleton in zip([entry["word"]] + list(entry.get("variants") or []), [base] + variants):
                exact = _exact(raw)
                if exact and exact != skeleton:
                    self._allowed_exact[exact] = skeleton
                    digest.update(repr(("exact", exact)).encode())
                elif skeleton:
                    self._allowed.add(skeleton)
            return

        for stem in [base] + [v for v in variants if v]:
            for suffix in ("",) + tuple(suffixes):
                form = stem + suffix
                key, counts = _runs(form)
                self._forms.setdefault(key, []).append((counts, severity, base))
                self._by_length.setdefault(len(form), []).append((form, severity, base))
            if mode == "anywhere":
                self._anywhere.append((_runs(stem), severity, base))

    def _add_native(self, entry, script, digest):
        base = _native_letters(entry["word"])
        if not base:
            return
        severity = int(entry.get("severity", 0))
        mode = entry.get("mode") or "word"
        spaceless = script in SPACELESS_SCRIPTS
        raw_variants = list(entry.get("variants") or [])
        suffixes = [_native_letters(x) for x in (entry.get("suffixes") or [])]
        native_variants, latin_variants = [], []
        for variant in raw_variants:
            if word_script(variant) in (None, "Latin"):
                latin_variants.append(self._letters(variant))
            else:
                native_variants.append(_native_letters(variant))
        digest.update(repr(("native", script, base, severity, "anywhere" if spaceless else mode, native_variants,
                            latin_variants, tuple(suffixes), entry.get("language") or "")).encode())

        stems = [base] + [v for v in native_variants if v]
        if severity <= 0:
            for stem in stems:
                if spaceless:
                    if len(stem) >= MIN_SPACELESS:
                        self._spaceless_allowed.add(stem)
                else:
                    self._native_allowed.add(stem)
            self._allowed.update(v for v in latin_variants if v)
            return

        for stem in stems:
            for suffix in ("",) + tuple(x for x in suffixes if x):
                form = stem + suffix
                key, counts = _runs(form)
                if spaceless:
                    if len(form) >= MIN_SPACELESS:
                        self._spaceless_forms.setdefault(severity, {}).setdefault(key, []).append((counts, base))
                else:
                    self._native_forms.setdefault(key, []).append((counts, severity, base))
            if mode == "anywhere" and not spaceless:
                self._native_anywhere.append((_runs(stem), severity, base))
        # a Latin spelling of a word from another script ("blyat"): an ordinary whole word, no endings
        for stem in latin_variants:
            if stem:
                key, counts = _runs(stem)
                self._forms.setdefault(key, []).append((counts, severity, base))
                self._by_length.setdefault(len(stem), []).append((stem, severity, base))

    @staticmethod
    def clean_word(word):
        """A word as it is stored and matched: plain a-z for a Latin word, the letters (and vowel signs) of another
        script, "" for anything without letters."""
        script = word_script(word)
        if script is None:
            return ""
        if script == "Latin":
            return WordFilter._letters(word)
        return _native_letters(word)

    @staticmethod
    def allowed_form(word):
        """An allowed word as it is stored and matched: a Latin word with letters beyond a-z ("kút") as written (NFC,
        casefolded), since its skeleton ("kut") may be a listed word of its own; anything else as clean_word."""
        if word_script(word) == "Latin":
            exact = _exact(word)
            if exact and exact != WordFilter._letters(word):
                return exact
        return WordFilter.clean_word(word)

    def listed_severity(self, word):
        """How a word reads as a listed word once folded to its skeleton, whatever the allowed words say (0, 1, 2)."""
        if word_script(word) in (None, "Latin"):
            return self._match_word(self._letters(word), allowed=False)[0]
        return self._match_native(_native_letters(word))[0]

    @staticmethod
    def _letters(word):
        return "".join(c for c in _fold(word) if _is_letter(c))

    # ------------------------------------------------------------ matching
    def _match_word(self, word, allowed=True):
        """(severity, base word) for one skeleton of letters (+ wildcards); `allowed=False` reads an allowed word as
        the listed word it spells."""
        if not word or (allowed and word in self._allowed):
            return 0, None

        if WILDCARD in word:
            letters = sum(1 for c in word if c != WILDCARD)
            if letters < 2 or letters * 2 < len(word):
                return 0, None   # "****" or "a***" could be anything
            best = (0, None)
            for form, severity, base in self._by_length.get(len(word), ()):
                if severity > best[0] and all(w == WILDCARD or w == f for w, f in zip(word, form)):
                    best = (severity, base)
            return best

        best = (0, None)
        key, counts = _runs(word)
        for min_counts, severity, base in self._forms.get(key, ()):
            # same letters in the same order, each run at least as long as the
            # listed word's ("fuuuck" yes; "as" is not "ass")
            if severity > best[0] and all(c >= m for c, m in zip(counts, min_counts)):
                best = (severity, base)

        if best[0] < EXTREME:
            for stem_runs, severity, base in self._anywhere:
                if severity > best[0] and _contains_runs(key, counts, *stem_runs):
                    best = (severity, base)
        return best

    @staticmethod
    def _latin_pieces(token):
        """The token folded for the Latin path, as (character, kind): "latin" (a-z, a digit or symbol standing in for a
        letter), "lookalike" (a Latin look-alike from another alphabet: Cyrillic с, Greek ο), "foreign" (a letter of
        another script written with spaces, no look-alike) or "break". Tatweel and the modifier letters of other
        scripts (Armenian ՙ, Georgian ჼ, N'Ko ߺ) are drawn as decoration and dropped, as format characters are."""
        out = []
        for ch in unicodedata.normalize("NFKD", token):
            cat = unicodedata.category(ch)
            if cat in ("Mn", "Me", "Cf"):
                continue
            if cat == "Lm" and char_script(ch) not in ("", "Latin"):
                continue
            for c in ch.casefold():
                mapped = CONFUSABLES.get(c)
                if mapped is not None:
                    kind = "latin" if char_script(c) == "Latin" else "lookalike"
                    out.extend((m, kind) for m in mapped)
                elif _is_wordchar(c):
                    out.append((c, "latin"))
                elif _is_spaced_foreign(c):
                    out.append((c, "foreign"))
                else:
                    out.append((c, "break"))
        return out

    def _token_candidates(self, token):
        """Skeleton words a whitespace-delimited token could be read as, in groups: one group per piece of the token
        (its readings with digits and symbols as letters), and one for the pieces joined. An allowed reading excuses
        its own group only."""
        if token.isascii() and token.isalpha():
            return [[token.lower()]]   # the overwhelmingly common case
        pieces = self._latin_pieces(token)
        start, end = 0, len(pieces)
        while start < end and not (_is_letter(pieces[start][0]) or pieces[start][0].isdigit()):
            start += 1
        while end > start and not (_is_letter(pieces[end - 1][0]) or pieces[end - 1][0].isdigit()):
            end -= 1
        groups, seen = [], set()
        for text in (pieces, pieces[start:end]):
            # a letter of another spaced script that is no Latin look-alike (Greek λ, Cyrillic б, Arabic...) is part
            # of a word in that script, never a break inside a Latin one: "κύκλος" is not "kuk" + "os", and
            # "сообщили" is not "coo" + "n" + "n". A run holding one is read as Latin only with that letter left out,
            # and only when the rest of it is real Latin letters (no look-alikes) and the foreign letters are fewer
            # than them ("fuбck"). Letters of the scripts written without spaces still break (English words sit right
            # against Chinese ones).
            chunks, current, refused = [], [], False
            for ch, kind in text + [("", "break")]:
                if kind != "break":
                    current.append((ch, kind))
                    continue
                if current:
                    kinds = [k for _, k in current]
                    foreign = kinds.count("foreign")
                    if not foreign:
                        chunks.append("".join(c for c, _ in current))
                    elif "lookalike" in kinds or foreign >= sum(1 for c, k in current if k == "latin" and _is_letter(c)):
                        refused = True
                    else:
                        chunks.append("".join(c for c, k in current if k != "foreign"))
                current = []
            # the pieces on their own ("half-assed") and joined ("f.u.c.k"), never joined across a foreign word
            joined = ["".join(chunks)] if len(chunks) > 1 and not refused else []
            for chunk in chunks + joined:
                group = _leet_variants(chunk)
                if group and tuple(group) not in seen:
                    seen.add(tuple(group))
                    groups.append(group)
        return groups

    def _latin_severity(self, token):
        best = (0, None)
        if self._allowed_exact and not token.isascii() and _exact(token) in self._allowed_exact:
            return best
        for group in self._token_candidates(token):
            # an allowed word excuses itself, never a listed word beside it ("<slur>-<allowed>")
            if any(word in self._allowed for word in group):
                continue
            for word in group:
                found = self._match_word(word)
                if found[0] > best[0]:
                    best = found
                    if best[0] >= EXTREME:
                        return best
        return best

    def _native_candidates(self, token):
        """(word, proclitic taken off) for each way a token in another script could be read."""
        chunks = _native_chunks(_native_fold(token))
        words = chunks + (["".join(chunks)] if len(chunks) > 1 else [])
        out = []
        for word in words:
            if word in self._native_allowed:
                continue     # an allowed word, and every way of reading it with a proclitic taken off
            out.append((word, False))
            if word and char_script(word[0]) == "Arabic":
                out.extend((bare, True) for bare in _arabic_bare(word))
        return out

    def _match_native(self, word, bare=False):
        if not word or word in self._native_allowed:
            return 0, None
        best = (0, None)
        key, counts = _runs(word)
        for min_counts, severity, base in self._native_forms.get(key, ()):
            if bare and len(base) < ARABIC_MIN_BARE:
                continue
            if severity > best[0] and all(c >= m for c, m in zip(counts, min_counts)):
                best = (severity, base)
        if best[0] < EXTREME and not bare:
            for stem_runs, severity, base in self._native_anywhere:
                if severity > best[0] and stem_runs[0] in key and _contains_runs(key, counts, *stem_runs):
                    best = (severity, base)
        return best

    def _without_allowed_native(self, token):
        """The token with each word allowed in its own script blanked out ("nigger-فينيك" -> "nigger- "), and whether
        there was one. A word is a run of letters and marks (and what the native fold reads through)."""
        out, run, cut = [], [], False

        def flush():
            nonlocal cut
            if run:
                word = "".join(run)
                if _native_letters(word) in self._native_allowed:
                    out.append(" ")
                    cut = True
                else:
                    out.append(word)
                run.clear()

        for ch in token:
            if unicodedata.category(ch)[0] in "LM" or unicodedata.category(ch) == "Cf":   # tatweel is Lm
                run.append(ch)
            else:
                flush()
                out.append(ch)
        flush()
        return "".join(out), cut

    def _token_severity(self, token):
        native = []
        if self._has_native and not token.isascii() and _has_foreign_letter(token):
            # a word allowed in its own script is never hidden, whatever its Latin skeleton reads as; the rest of the
            # token is read without it
            if self._native_allowed:
                rest, cut = self._without_allowed_native(token)
                if cut:
                    best = (0, None)
                    for piece in rest.split():
                        found = self._token_severity(piece)
                        if found[0] > best[0]:
                            best = found
                    return best
            native = self._native_candidates(token)
        best = self._latin_severity(token)
        for word, bare in native:
            if best[0] >= EXTREME:
                break
            found = self._match_native(word, bare)
            if found[0] > best[0]:
                best = found
        return best

    def _spaceless_matches(self, text):
        """[(matched text, listed word, severity)] in the scripts written without spaces."""
        if not self._spaceless_res or not _SPACELESS_ANY.search(text):
            return []
        scan = _SPACELESS_JOIN.sub("", _native_fold(text))
        key, counts = _runs(scan)
        # the allowed compounds in the text, by the run they start at: (end, their run counts)
        allowed = {}
        if self._spaceless_mask is not None:
            for match in self._spaceless_mask.finditer(key):
                start = match.start(1)
                found = next((m for m in self._spaceless_allowed_runs[match.group(1)] if _counts_ok(counts, start, m)),
                             None)
                if found:
                    allowed[start] = (match.end(1), found)
        offsets = [0]
        for n in counts:
            offsets.append(offsets[-1] + n)
        found = []
        for severity, pattern in self._spaceless_res:
            forms = self._spaceless_forms[severity]
            last_end = -1
            for match in pattern.finditer(key):
                start, end = match.start(1), match.end(1)
                if end <= last_end:
                    continue
                hit = next(((b, m) for m, b in forms.get(match.group(1), ()) if _counts_ok(counts, start, m)), None)
                if hit and not (allowed and self._excused(allowed, counts, start, end, hit[1])):
                    last_end = end
                    found.append((scan[offsets[start]:offsets[end]], hit[0], severity))
        return found

    def _excused(self, allowed, counts, start, end, min_counts):
        """Is the listed word on runs [start, end) (with `min_counts`) part of an allowed compound rather than a word of
        its own? It is when it lies wholly inside one ("笨蛋" in "笨蛋树"), or when it reaches into one only by reading
        a doubled character there as one of its own: in "他妈妈的" ("his mother's") the listed 他妈的 needs one 妈 of the
        two the allowed 妈妈 is made of. A listed word that merely shares a character with an allowed one ("씨발" in
        "씨발명", where 발명 is allowed) is still a word of its own."""
        for a_start in range(max(0, start - self._spaceless_allowed_longest + 1), end):
            if a_start not in allowed:
                continue
            a_end, a_counts = allowed[a_start]
            if a_end <= start:
                continue
            if a_start <= start and a_end >= end:
                return True
            for j in range(max(start, a_start), min(end, a_end)):
                need, theirs = min_counts[j - start], a_counts[j - a_start]
                if need < counts[j] < need + theirs:
                    return True
        return False

    def _tokens(self, text):
        """Whitespace tokens, with runs of 3+ single characters ("f u c k", "б л я д ь")
        merged into one token."""
        raw = text.split()
        out, i = [], 0
        while i < len(raw):
            j = i
            while j < len(raw) and _single_letter(raw[j]):
                j += 1
            if j - i >= 3:
                out.append("".join(raw[i:j]))
                i = j
            else:
                out.append(raw[i])
                i += 1
        return out

    def explain(self, text):
        """[(token, listed word, severity)] for everything that matches."""
        tokens = self._tokens(text or "")
        skeletons = [self._letters(t) for t in tokens]
        skip = set()
        for i, first in enumerate(skeletons):
            for phrase in self._allow_phrases.get(first, ()):
                if tuple(skeletons[i:i + len(phrase)]) == phrase:
                    skip.update(range(i, i + len(phrase)))
        if self._native_phrases:
            keys = [_native_letters(t) for t in tokens]
            for i, first in enumerate(keys):
                for phrase in self._native_phrases.get(first, ()):
                    if tuple(keys[i:i + len(phrase)]) == phrase:
                        skip.update(range(i, i + len(phrase)))
        found = []
        for i, token in enumerate(tokens):
            if i in skip:
                continue
            severity, base = self._token_severity(token)
            if severity:
                found.append((token, base, severity))
        found.extend(self._spaceless_matches(text or ""))
        return found

    def severity(self, text):
        """Highest severity of any word in plain text (0, 1 or 2)."""
        return max((s for _, _, s in self.explain(text)), default=0)

    def severity_html(self, html):
        """Same, for stored HTML: only the visible text is read."""
        return self.severity(visible_text(html))


# a mention link (/@user, /+guild, /&curation, /topic/<slug>) and nothing longer: a link to a post or comment
# (/@user/post/<id>, /+guild/@user/post/<id>) has words of its own, which are read
_NAME_LINK = re.compile(r"^/(?:[@+&][A-Za-z0-9_]+|topic/[A-Za-z0-9_-]+)/?$")


class _TextExtractor(HTMLParser):
    """Visible text of stored post/comment HTML.

    Inline tags are transparent, so a word split by formatting is rejoined.
    Skipped: @user / +guild / &curation / #topic mention links and links whose text is
    just their own address (names and URLs are not prose)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0
        self._link_href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if self._skip_depth:
            if tag == "a":
                self._skip_depth += 1
            return
        if tag == "a":
            attrs = dict(attrs)
            href = attrs.get("href") or ""
            if "data-original-name" in attrs or _NAME_LINK.match(href):
                self._skip_depth = 1
            else:
                self._link_href = href
                self._link_text = []

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag != "a":
            return
        if self._skip_depth:
            self._skip_depth -= 1
        elif self._link_href is not None:
            text = "".join(self._link_text)
            bare = text.strip().lower()
            if not (bare.startswith(("http://", "https://", "www.")) or bare == self._link_href.strip().lower()):
                self.parts.append(text)
            self._link_href = None
            self._link_text = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._link_href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)


def visible_text(html):
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    if parser._link_href is not None:   # unclosed <a>
        parser.parts.append("".join(parser._link_text))
    return "".join(parser.parts)


def is_hidden(level, severity, sensitive=False):
    """The one visibility rule.

    level: the viewer's filter (0 Off, 1 Standard, 2 Child).
    Standard hides extreme content; Child also hides profanity and anything
    marked sensitive."""
    if level <= 0:
        return False
    if level >= 2 and sensitive:
        return True
    return (severity or 0) >= 3 - min(level, 2)
