"""Word filter engine: how offensive is this text, for the two filter levels.

    severity 0  clean
    severity 1  profanity      - hidden for viewers on the Child filter
    severity 2  extreme terms  - hidden for Standard and Child viewers

It is deliberately not a blacklist of exact strings. Every token is reduced to
a "skeleton" of plain a-z letters before it is compared with the word list, so
the usual ways of dodging a filter are folded away first:

    case, accents, zero-width characters      FuCk, fúck, f​uck
    look-alike letters from other alphabets   fuсk (Cyrillic с), fullwidth, maths fonts
    digits and symbols standing in for letters   sh1t, a55, @ss, f*ck
    punctuation inside a word                  f.u.c.k, f-u-c-k
    spelled-out letters                        f u c k
    repeated letters                           fuuuuck
    words split by formatting tags (HTML)      f<b>u</b>ck

Matching is on WHOLE tokens, which is what keeps innocent words that merely
contain a listed word safe ("classic", "Scunthorpe", "cockpit"). Only entries
explicitly marked mode="anywhere" match inside longer tokens.

What it cannot do: new coinages, euphemisms, coded terms, reversed or half
split words ("fu ck"), misspellings that are real words ("duck you"), text in
images, other languages. The word list needs human upkeep.

Pure standard library and no Flask/database imports, so it is unit-testable.
"""
import hashlib
import unicodedata
from html.parser import HTMLParser

PROFANITY = 1
EXTREME = 2

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
        word      the base word, plain letters
        severity  PROFANITY (1) or EXTREME (2); 0 makes it an allowed word
        mode      "word" (whole token, default) or "anywhere" (inside tokens too)
        variants  other spellings of the same word, optional
        suffixes  endings to accept; None means DEFAULT_SUFFIXES
    allow_phrases: iterable of phrases whose words are never counted
        ("maine coon"), each a space separated string
    """

    def __init__(self, entries, allow_phrases=()):
        self._forms = {}        # run key -> [(min run counts, severity, base word)]
        self._by_length = {}    # form length -> [(form, severity, base word)]  (wildcards)
        self._anywhere = []     # (letter runs of the word, severity, base word)
        self._allowed = set()
        self._allow_phrases = {}
        digest = hashlib.sha256()

        for entry in sorted(entries, key=lambda e: (e["word"], e.get("severity", 0))):
            base = self._letters(entry["word"])
            if not base:
                continue
            severity = int(entry.get("severity", 0))
            mode = entry.get("mode") or "word"
            variants = [self._letters(v) for v in (entry.get("variants") or [])]
            suffixes = entry.get("suffixes")
            if suffixes is None:
                suffixes = DEFAULT_SUFFIXES
            digest.update(repr((base, severity, mode, variants, tuple(suffixes))).encode())

            if severity <= 0:
                self._allowed.add(base)
                self._allowed.update(v for v in variants if v)
                continue

            for stem in [base] + [v for v in variants if v]:
                for suffix in ("",) + tuple(suffixes):
                    form = stem + suffix
                    key, counts = _runs(form)
                    self._forms.setdefault(key, []).append((counts, severity, base))
                    self._by_length.setdefault(len(form), []).append((form, severity, base))
                if mode == "anywhere":
                    self._anywhere.append((_runs(stem), severity, base))

        for phrase in allow_phrases:
            words = tuple(self._letters(w) for w in phrase.split())
            if len(words) > 1:
                self._allow_phrases.setdefault(words[0], []).append(words)
                digest.update(repr(words).encode())

        self.version = digest.hexdigest()[:12]

    @staticmethod
    def _letters(word):
        return "".join(c for c in _fold(word) if _is_letter(c))

    # ------------------------------------------------------------ matching
    def _match_word(self, word):
        """(severity, base word) for one skeleton of letters (+ wildcards)."""
        if not word or word in self._allowed:
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

    def _token_candidates(self, token):
        """Skeleton words a whitespace-delimited token could be read as."""
        if token.isascii() and token.isalpha():
            return [token.lower()]   # the overwhelmingly common case
        folded = _fold(token)
        stripped = folded.strip("".join(sorted({c for c in folded if not (_is_letter(c) or c.isdigit())})))
        words = []
        for text in {folded, stripped}:
            chunks, current = [], []
            for ch in text:
                if _is_wordchar(ch):
                    current.append(ch)
                elif current:
                    chunks.append("".join(current))
                    current = []
            if current:
                chunks.append("".join(current))
            # the pieces on their own ("half-assed") and joined ("f.u.c.k")
            for chunk in chunks + (["".join(chunks)] if len(chunks) > 1 else []):
                words.extend(_leet_variants(chunk))
        return words

    def _token_severity(self, token):
        best = (0, None)
        candidates = self._token_candidates(token)
        if any(word in self._allowed for word in candidates):
            return best
        for word in candidates:
            found = self._match_word(word)
            if found[0] > best[0]:
                best = found
                if best[0] >= EXTREME:
                    break
        return best

    def _tokens(self, text):
        """Whitespace tokens, with runs of 3+ single characters ("f u c k")
        merged into one token."""
        raw = text.split()
        out, i = [], 0
        while i < len(raw):
            j = i
            while j < len(raw) and len(_fold(raw[j])) == 1 and _is_wordchar(_fold(raw[j])):
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
        found = []
        for i, token in enumerate(tokens):
            if i in skip:
                continue
            severity, base = self._token_severity(token)
            if severity:
                found.append((token, base, severity))
        return found

    def severity(self, text):
        """Highest severity of any word in plain text (0, 1 or 2)."""
        return max((s for _, _, s in self.explain(text)), default=0)

    def severity_html(self, html):
        """Same, for stored HTML: only the visible text is read."""
        return self.severity(visible_text(html))


class _TextExtractor(HTMLParser):
    """Visible text of stored post/comment HTML.

    Inline tags are transparent, so a word split by formatting is rejoined.
    Skipped: @user / +guild / &curation mention links and links whose text is
    just their own address (names and URLs are not prose)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0
        self._link_href = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")
        if self._skip_depth:
            if tag == "a":
                self._skip_depth += 1
            return
        if tag == "a":
            attrs = dict(attrs)
            href = attrs.get("href") or ""
            if "data-original-name" in attrs or href.startswith(("/@", "/+", "/&")):
                self._skip_depth = 1
            else:
                self._link_href = href
                self._link_text = []

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")
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
