"""Machine translation of the interface's catalogues by the site's own LibreTranslate (docs/i18n.md; the owner,
2026-10-24: the interface in every language LibreTranslate translates, by LibreTranslate).

    docker compose exec ruqqus sh -c 'PYTHONPATH=. venv/bin/python scripts/i18n_catalogs.py translate --lang es,de'
        [--domain messages] [--limit 200] [--workers 3] [--batch 20] [--report /tmp/report.json]
    ... translate --all
    ... translate --all --only mine.json      # only the (msgid, context) pairs in that file, e.g. one piece of work's new strings

Runs where LIBRETRANSLATE_URL reaches the service (inside the compose network) and only into languages it has loaded
(`LT_LOAD_ONLY`: on a small machine, load a few at a time and run again). For each language and domain it brings the
language's catalogue up to date with the template (ruqqus/translations/<domain>.pot), then translates only entries
with no translation yet, writing the catalogue after every few batches, so it can be stopped and run again. An entry
it translated carries the flag `machine`; an entry someone corrected by hand (no flag) is never touched.

How one string is sent (it is text a reader sees, with values, markup and markdown in it):
1. **Whole sentence first**, in text mode: values (`%(name)s`), `%%`, code, addresses, @ + # & names and brand names
   become `{1}` `{2}`..., so LibreTranslate sees one sentence (it reads better than the HTML mode, which translates the
   pieces between tags apart).
2. If a token was lost or mangled, or the string has HTML tags: **HTML mode**, values as empty `<x id="p1"></x>`
   elements (it keeps those reliably), tags stripped to an id and given their attributes back from the English,
   markdown links and bold as tags.
3. Every result is checked as the catalogues' test checks it (scripts/i18n_catalogs.problems: the same values, only
   the English's tags, nothing unsafe). If neither way passes, the entry stays untranslated (the English is shown) and
   the report lists it.

A short word with a context (`pgettext("button", "Post")`) is sent with the context as a hint ("Post (button)") and the
hint is cut from the answer. Plurals: one example number for each of the language's plural forms is translated in the
sentence ("5 comments") and turned back into `%(num)s`. The site's own words come from the glossary instead
(ruqqus/translations/glossary.json, flag `glossary`), before anything is sent.
"""
import gettext
import html
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

from scripts import i18n_catalogs as cat

MACHINE = "machine"
TIMEOUT = 120
BRANDS = ("LibreTranslate", "Google Drive", "Google", "YouTube", "Dailymotion", "OneDrive", "Element X",
          "FluffyChat", "Cinny", "Bluesky", "Mastodon", "Instagram", "TikTok", "Discord", "Reddit", "PayPal",
          "AdSense", "Plausible", "Sentry", "GlitchTip", "Cloudflare", "GitHub", "Twitch", "Spotify", "SoundCloud",
          "Tumblr", "Pinterest", "Telegram", "Truth Social", "Facebook", "Mailgun", "GIPHY", "Tenor", "Markdown",
          "OAuth", "RSS", "JSON")
# names and technical terms a translation keeps as they are (LibreTranslate turned "Premium" into an insurance
# premium, and mangles file types and units)
KEPT = ("Premium", "GIFs", "GIF", "PNG", "JPEG", "JPG", "WebP", "URLs", "URL", "URI", "API", "CSS", "HTML", "HTTPS",
        "HTTP", "SSL", "VPN", "UTC", "MB", "GB", "QR", "FAQ", "2FA", "DMCA", "CSAM")
BRANDS = BRANDS + KEPT
GLOSS = {"button": "button", "verb": "verb", "noun": "noun", "menu": "menu item", "tab": "tab", "label": "label",
         "title": "heading"}

# The site's shorthand and its words with an unusual sense, written out in plain English before LibreTranslate sees
# them (only what is sent changes: the catalogue's English is the site's own), so "Rep" reaches Japanese as
# "Reputation" rather than "request", and a flair as a label. Whole words, case as written; never inside a value, a
# tag, an address or code.
PLAIN = (
    (r"\btake a sec\b", "take a moment"),                 # phrases first, then the words in them
    (r"\bRep\b", "Reputation"), (r"\brep\b", "reputation"),
    (r"\bMod Log\b", "Moderation Log"), (r"\bmod log\b", "moderation log"),
    (r"\bMods\b", "Moderators"), (r"\bmods\b", "moderators"), (r"\bMod\b", "Moderator"), (r"\bmod\b", "moderator"),
    (r"\bGuildmasters\b", "Guild moderators"), (r"\bguildmasters\b", "guild moderators"),
    (r"\bGuildmaster\b", "Guild moderator"), (r"\bguildmaster\b", "guild moderator"),
    (r"\bmin read\b", "minute read"), (r"\bmin\b", "minutes"), (r"\bmins\b", "minutes"),
    (r"\bsecs?\b", "seconds"), (r"\bhrs?\b", "hours"),
    (r"/mo\b", " per month"), (r"/yr\b", " per year"),
    (r"\bFlairs\b", "Labels"), (r"\bflairs\b", "labels"), (r"\bFlair\b", "Label"), (r"\bflair\b", "label"),
    (r"\bCurations\b", "Curated feeds"), (r"\bcurations\b", "curated feeds"),
    (r"\bCuration\b", "Curated feed"), (r"\bcuration\b", "curated feed"),
    (r"\bheralded\b", "highlighted"), (r"\bHeralded\b", "Highlighted"), (r"\bHerald\b", "Highlight"),
    (r"\bherald\b", "highlight"),
    (r"\bSiege\b", "Takeover claim"), (r"\bsieges\b", "takeover claims"), (r"\bsiege\b", "takeover claim"),
    (r"\bbarren\b", "empty"), (r"\bEMOJI\b", "emoji"),
)
# Added later (2026-11-12): signing out. LibreTranslate read "signed out" as signed (a document: French "signé", Spanish
# "firmado", Russian "подписано", Turkish "imzalanacaktır"), signed in (German "angemeldet") or subscribed, and "log out"
# as logging in (Italian "Accedi", Russian "Войти") or registering ("registrado"), on security warnings, where the
# opposite of what they do is worst. "Disconnect" came back right in every language tried, so a sentence reaches it so
# written. An entry a machine made before these rules (flag `plain1`) that they change is made again (PLAIN_FLAG).
PLAIN_SIGN_OUT = (
    (r"\bsigns (\w+) out\b", r"disconnects \1"),          # "signs chat out on all your devices"
    (r"\bSigned out of\b", "Disconnected from"), (r"\bsigned out of\b", "disconnected from"),
    (r"\bSign out\b", "Disconnect"), (r"\bsign out\b", "disconnect"),
    (r"\bSigns out\b", "Disconnects"), (r"\bsigns out\b", "disconnects"),
    (r"\bSigned out\b", "Disconnected"), (r"\bsigned out\b", "disconnected"),
    (r"\bSigning out\b", "Disconnecting"), (r"\bsigning out\b", "disconnecting"),
    (r"\bLog out\b", "Disconnect"), (r"\blog out\b", "disconnect"), (r"\bLog Out\b", "Disconnect"),
    (r"\bLogs out\b", "Disconnects"), (r"\blogs out\b", "disconnects"),
    (r"\bLogged out\b", "Disconnected"), (r"\blogged out\b", "disconnected"),
    (r"\bLogging out\b", "Disconnecting"), (r"\blogging out\b", "disconnecting"),
)
_PLAIN = [(re.compile(pattern), plain) for pattern, plain in PLAIN + PLAIN_SIGN_OUT]
_PLAIN_BEFORE_SIGN_OUT = [(re.compile(pattern), plain) for pattern, plain in PLAIN]
_UNTOUCHED = re.compile(r"(%\(\w+\)[sd]|%%|<[^>]+>|&[#\w]+;|`[^`\n]*`|https?://\S+|\]\([^)]*\)|[@+#&][\w-]+)")

# A string that is one short word means little alone ("Space": outer space? "Forward": onwards?): it is sent with what
# it means here as a hint, which is cut from the answer
SHORT_HINTS = {
    "Space": "a live audio room", "Spaces": "live audio rooms", "Forward": "share to a group",
    "Tip": "a small payment", "Tips": "small payments", "Post": "publish a message", "Block": "block a user",
    "Mute": "silence a user", "Report": "report a problem", "Follow": "follow a person",
    "Following": "the people you follow", "Save": "keep for later", "Share": "share with others",
    "LIVE": "broadcasting now", "Live": "broadcasting now", "Circle": "a group of friends",
    "Circles": "groups of friends", "Join": "become a member", "Leave": "stop being a member", "Wiki": "a wiki",
    "Repost": "post again on your profile", "Exile": "ban from the group", "Pin": "fix at the top",
    "Unpin": "remove from the top", "Mute guild": "hide a group's posts",
}
PLAIN_FLAG = "plain2"          # made with PLAIN, PLAIN_SIGN_OUT and SHORT_HINTS: an older machine entry that needs them is made again
PLAIN_FLAG_BEFORE_SIGN_OUT = "plain1"     # made with PLAIN and SHORT_HINTS only: made again only if PLAIN_SIGN_OUT changes it

# The site's own words, which a machine gets wrong even written out (LibreTranslate made "Rep" Arabic for "amputation"
# and "Forward" Spanish for "onward"): their translations, made once per language from scripts/i18n_glossary_terms.py
# by Claude with a review pass (not by LibreTranslate), are used as they are wherever an entry is exactly the word with
# that context. They replace a machine translation, never someone's own correction, and are written again whenever the
# glossary changes (`i18n_catalogs.py glossary` does it without LibreTranslate).
GLOSSARY = "glossary.json"
GLOSSARY_FLAG = "glossary"


def plain(text, rules=None):
    """The English as sent: the site's shorthand and odd words written out (PLAIN, PLAIN_SIGN_OUT), outside values and
    markup."""
    pieces = _UNTOUCHED.split(text)
    for i in range(0, len(pieces), 2):
        for pattern, replacement in (_PLAIN if rules is None else rules):
            pieces[i] = pattern.sub(replacement, pieces[i])
    return "".join(pieces)


def needs_plain(message):
    """Would PLAIN, SHORT_HINTS or a kept term change how this entry is sent?"""
    texts = message.id if isinstance(message.id, (list, tuple)) else [message.id]
    for text in texts:
        if text in SHORT_HINTS or plain(text) != text:
            return True
        if any(re.search(r"(?<![\w])%s(?![\w])" % re.escape(b), text) for b in KEPT):
            return True
    return False


def made_before_the_rules(message):
    """A machine entry made before today's plain-English rules, which they would send differently: one with no plain
    flag that any rule changes, or one made with the first rules (`plain1`) that PLAIN_SIGN_OUT changes. Made again."""
    if PLAIN_FLAG in message.flags:
        return False
    if PLAIN_FLAG_BEFORE_SIGN_OUT not in message.flags:
        return needs_plain(message)
    texts = message.id if isinstance(message.id, (list, tuple)) else [message.id]
    return any(plain(text) != plain(text, _PLAIN_BEFORE_SIGN_OUT) for text in texts)


def glossary(key):
    """{(msgid, context): translation} for one language ({} without a glossary)."""
    path = cat.OUT / GLOSSARY
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(msgid, context): text for msgid, by_context in data.get(key, {}).items()
            for context, text in by_context.items()}


def apply_glossary(catalog, key):
    """Writes the glossary's translations into a catalogue; answers the (msgid, context) of each entry it wrote."""
    table = glossary(key)
    applied = set()
    for message in catalog:
        if not message.id or not isinstance(message.id, str):
            continue
        found = (message.id, message.context or "")
        text = table.get(found)
        if text is None or cat.problems(message.id, text):
            if GLOSSARY_FLAG in message.flags and MACHINE in message.flags:     # gone from the glossary: made again
                message.string = ""
                message.flags.difference_update((GLOSSARY_FLAG, MACHINE))
            continue
        if message.string and MACHINE not in message.flags:
            continue                                                            # someone's own correction
        message.string = text
        message.flags.discard(PLAIN_FLAG_BEFORE_SIGN_OUT)
        message.flags.update((MACHINE, PLAIN_FLAG, GLOSSARY_FLAG))
        message.flags.discard("fuzzy")
        applied.add(found)
    return applied


def glossary_catalogs(keys=None):
    """The glossary written into every committed catalogue, its machine entries tidied and the ones today's checks
    refuse emptied (no LibreTranslate needed)."""
    from ruqqus.helpers import i18n
    count = tidied = refused = 0
    for folder in cat.language_folders():
        if keys and folder.name not in keys:
            continue
        for po in sorted((folder / "LC_MESSAGES").glob("*.po")):
            catalog = cat.read(po, locale=i18n.babel_code(folder.name), domain=po.stem)
            count += len(apply_glossary(catalog, folder.name))
            tidied += tidy_catalog(catalog)
            refused += refuse_broken(catalog)
            cat.write(catalog, po)
    print(f"glossary entries written: {count}; machine entries tidied: {tidied}, refused: {refused}")
    return 0


# what a sentence must keep as it is, as text-mode tokens
_KEEP = re.compile(
    r"%\(\w+\)[sd]|%%|&[#\w]+;|`[^`\n]+`|https?://\S+|(?<![\w/])[@+#&][\w-]+|"
    + r"(?<![\w])(?:" + "|".join(re.escape(b) for b in sorted(BRANDS, key=len, reverse=True)) + r")(?![\w])")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")


def lt_url():
    return (os.environ.get("LIBRETRANSLATE_URL") or "").rstrip("/")


def lt_codes():
    """Our row key -> LibreTranslate's code, for the languages it has loaded."""
    from ruqqus.helpers import translators
    pairs, codes = translators.libre_languages()
    loaded = {target for _src, target in pairs} | {src for src, _tgt in pairs}
    return {key: codes.get(key, key) for key in loaded if key != "en"}


def call(texts, target, fmt):
    """LibreTranslate's answers for `texts` (English) into `target`, or None for each when it failed."""
    if not texts:
        return []
    payload = {"q": texts, "source": "en", "target": target, "format": fmt}
    if os.environ.get("LIBRETRANSLATE_API_KEY"):
        payload["api_key"] = os.environ["LIBRETRANSLATE_API_KEY"]
    for attempt in range(3):
        try:
            r = requests.post(lt_url() + "/translate", json=payload, timeout=TIMEOUT)
            if r.status_code == 200:
                out = r.json().get("translatedText")
                out = out if isinstance(out, list) else [out]
                if len(out) == len(texts):
                    return out
            elif r.status_code < 500:
                break
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    if len(texts) > 1:                                  # one bad string can fail a batch: try them one by one
        return [(call([t], target, fmt) or [None])[0] for t in texts]
    return [None]


# --- text mode -----------------------------------------------------------------------------------------------------------

def text_encode(english):
    """(the sentence with {n} tokens, the token -> original map), or None when it has HTML tags (HTML mode)."""
    if _TAG.search(english):
        return None
    tokens = {}

    def keep(match):
        token = "{%d}" % (len(tokens) + 1)
        tokens[token] = match.group(0)
        return token

    def link(match):
        token = "{%d}" % (len(tokens) + 1)
        tokens[token] = match.group(2)
        return f"[{match.group(1)}]({token})"

    masked = _MD_LINK.sub(link, english)
    masked = _KEEP.sub(keep, masked)
    return masked, tokens


def text_decode(answer, tokens):
    if answer is None:
        return None
    for token, original in tokens.items():
        if answer.count(token) != 1:
            return None
        answer = answer.replace(token, original)
    if re.search(r"\{\d+\}", answer):
        return None
    return answer


# --- HTML mode -----------------------------------------------------------------------------------------------------------

def html_encode(english):
    """(HTML for LibreTranslate, what to put back): values and kept words as empty <x> elements, tags reduced to an
    id, markdown links and bold as tags."""
    restore = {}
    counter = [0]

    def next_id(prefix):
        counter[0] += 1
        return f"{prefix}{counter[0]}"

    def keep(match):
        key = next_id("p")
        restore[key] = ("keep", match.group(0))
        return f'<x id="{key}"></x>'

    def tag(match):
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3)
        if closing:
            return f"</{name}>"
        key = next_id("t")
        restore[key] = ("tag", name, attrs)
        self_closing = attrs.rstrip().endswith("/") or name in ("br", "img", "hr", "input")
        return f'<{name} id="{key}">' if not self_closing else f'<{name} id="{key}"/>'

    def link(match):
        key = next_id("m")
        restore[key] = ("link", match.group(2))
        return f'<a id="{key}">{match.group(1)}</a>'

    text = _MD_LINK.sub(link, english)
    text = re.sub(r"\*\*([^*\n]+)\*\*", lambda m: f'<b id="{next_id("b")}">{m.group(1)}</b>', text)
    text = _TAG.sub(tag, text)
    # values and kept words outside the tags we just made
    pieces = re.split(r"(<[^>]+>)", text)
    text = "".join(p if p.startswith("<") else _KEEP.sub(keep, p) for p in pieces)
    return text, restore


def html_decode(answer, restore, english):
    if answer is None:
        return None
    for key, item in restore.items():
        if item[0] == "keep":
            pattern = re.compile(r'<x id="%s"\s*/?>(?:</x>)?' % re.escape(key))
            if len(pattern.findall(answer)) != 1:
                return None
            answer = pattern.sub(lambda m, v=item[1]: v, answer)
    for key, item in restore.items():
        if item[0] == "tag":
            name, attrs = item[1], item[2]
            pattern = re.compile(r'<%s id="%s"\s*(/?)>' % (re.escape(name), re.escape(key)))
            if len(pattern.findall(answer)) != 1:
                return None
            answer = pattern.sub(lambda m, n=name, a=attrs: f"<{n}{a}>", answer)
        elif item[0] == "link":
            pattern = re.compile(r'<a id="%s">(.*?)</a>' % re.escape(key), re.S)
            if len(pattern.findall(answer)) != 1:
                return None
            answer = pattern.sub(lambda m, t=item[1]: f"[{m.group(1).strip()}]({t})", answer)
    answer = re.sub(r'<b id="b\d+">(.*?)</b>', lambda m: f"**{m.group(1).strip()}**", answer, flags=re.S)
    if re.search(r'<x id=|<b id=|<a id="m', answer):
        return None
    if "&" not in english:
        answer = html.unescape(answer)
    return answer


# --- one entry -----------------------------------------------------------------------------------------------------------

def _only_values(text):
    return not re.search(r"[A-Za-z]{2,}", _KEEP.sub("", _MD_LINK.sub(r"\1", text)))


def _cut_gloss(answer, english):
    """The answer without the hint's parenthesis (any kind of bracket) at the end, unless the English had one."""
    if answer is None or english.rstrip().endswith(")"):
        return answer
    return re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", answer).strip() or answer


def _same_space(answer, english):
    lead = english[:len(english) - len(english.lstrip())]
    tail = english[len(english.rstrip()):]
    return lead + answer.strip() + tail


def plural_examples(catalog):
    """For each plural form of the catalogue's language, a number that takes it (Arabic: 0, 1, 2, 3, 11, 100)."""
    choose = gettext.c2py(catalog.plural_expr)
    found = {}
    for n in list(range(0, 201)) + [1000, 1001, 1002, 1005, 1011, 1021]:
        found.setdefault(choose(n), n)
        if len(found) == catalog.num_plurals:
            break
    return [found.get(i) for i in range(catalog.num_plurals)]


def ascii_digits(text):
    """A translation's digits as 0-9, whatever script LibreTranslate wrote them in (Arabic ٥, Persian ۵, Bengali ৫,
    Devanagari ५, Thai ๕...): a plural's example number is found by them. Bengali's plurals all failed until 2026-10-25
    because only the Arabic and Persian ones were known."""
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() and not c.isascii() else c for c in text)


# LibreTranslate repeats a mark it should write once ("已转发给 +%(guild)s。 。 。", "%(title)s - - - %(site)s")
_MARK_RUN = re.compile(r"([。．.！？!?:：;；,，、|·\-–—])((?:[ \u3000]*\1)+)")
# ...or ends a sentence with its own script's mark and then the English one as well (Hindi "।.", Arabic, Persian and
# Urdu "؟?", Chinese and Japanese "？?" and "。."): the first is kept
_NATIVE_THEN_LATIN = (("।.", "।"), ("؟?", "؟"), ("？?", "？"), ("。.", "。"), ("！!", "！"))


# A digit after an Arabic-script letter is an "Arabic number" to the bidirectional algorithm, and a "#" before it no longer joins it, so "مجهول #3"
# is drawn "مجهول 3#" (Arabic, Persian, Urdu; Hebrew is fine). A left-to-right mark before the "#" keeps "#3" together (measured in Chromium, 2026-11-14).
_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06ff\u0750-\u077f]")
_HASH_BEFORE_VALUE = re.compile(r"(?<!\u200e)#(?=%\(\w+\)[sd])")
# an "@" before a handle: a neutral between the right-to-left words and the Latin name takes the right-to-left direction, so "@name" was drawn "name@" (Arabic, Persian,
# Urdu and Hebrew alike); the same mark keeps it in front (the review of 2026-11-19)
_RTL_SCRIPT = re.compile(r"[\u0590-\u05ff\u0600-\u06ff\u0750-\u077f]")
_AT_BEFORE_VALUE = re.compile(r"(?<!\u200e)@(?=%\(\w+\)[sd])")


def tidy(english, text):
    """A translation with the marks LibreTranslate doubled written once (unless the English doubles them too: "..."),
    and an ellipsis where the English ends with one ("Loading…" came back "Cargando..." and is "Cargando…")."""
    if "…" in english:
        text = re.sub(r"\.{2,}|。{2,}", "…", text)
        if english.rstrip().endswith("…") and not text.rstrip().endswith("…"):
            text = re.sub(r"[.。]?\s*$", "…", text.rstrip(), count=1)
    def once(match):
        mark = match.group(1)
        if re.search(re.escape(mark) + r"[ \u3000]*" + re.escape(mark), english):
            return match.group(0)
        return mark
    text = _MARK_RUN.sub(once, text)
    for pair, mark in _NATIVE_THEN_LATIN:
        if pair not in english:
            text = text.replace(pair, mark)
    if re.search(r"#%\(", english) and _ARABIC_SCRIPT.search(text):
        text = _HASH_BEFORE_VALUE.sub("\u200e#", text)
    if re.search(r"@%\(", english) and _RTL_SCRIPT.search(text):
        text = _AT_BEFORE_VALUE.sub("\u200e@", text)
    return text


def tidy_catalog(catalog):
    """tidy() over a catalogue's machine entries (never a glossary one or someone's own); answers how many changed."""
    changed = 0
    for message in catalog:
        if not message.id or not message.string or MACHINE not in message.flags or GLOSSARY_FLAG in message.flags:
            continue
        if isinstance(message.id, (list, tuple)):
            english = message.id[1]
            new = tuple(tidy(english, form) for form in message.string)
        else:
            new = tidy(message.id, message.string)
        if new != message.string:
            message.string = new
            changed += 1
    return changed


def refuse_broken(catalog):
    """Machine entries today's checks refuse (a loop, padding, a lost value) are emptied, so the English shows until a
    run makes them again; someone's own entry is never touched. Answers how many."""
    refused = 0
    for message in catalog:
        if message.id and message.string and MACHINE in message.flags and cat.entry_problems(message):
            message.string = tuple("" for _f in message.string) if isinstance(message.id, (list, tuple)) else ""
            message.flags.difference_update((MACHINE, PLAIN_FLAG, PLAIN_FLAG_BEFORE_SIGN_OUT, GLOSSARY_FLAG))
            refused += 1
    return refused


class Job:
    """One string to translate: the English, its context hint, and where the answer goes."""

    def __init__(self, english, hint=None, number=None, optional=False):
        self.english = english
        self.hint = hint
        self.number = number                        # a plural example: %(num)s written as this number
        self.optional = optional                    # a form before the last may say the number in words ("un día")
        self.result = None

    def sent(self):
        text = plain(self.english)
        if self.number is not None:
            text = text.replace("%(num)s", str(self.number))
        if self.hint:
            text = f"{text} ({self.hint})"
        return text

    def finish(self, answer):
        if answer is None:
            return None
        if self.hint:
            answer = _cut_gloss(answer, self.english)
        if self.number is not None:
            normal = ascii_digits(answer)
            spot = [m for m in re.finditer(r"(?<!\d)%d(?!\d)" % self.number, normal)]
            if len(spot) == 1:
                answer = normal[:spot[0].start()] + "%(num)s" + normal[spot[0].end():]
            elif not (self.optional and not spot and not re.search(r"\d", normal.replace("%(", ""))):
                return None
        answer = tidy(self.english, _same_space(answer, self.english))
        english = self.english
        if self.optional and "%(num)s" not in answer:
            english = english.replace("%(num)s", "")         # the checks then ask for every other value
        return answer if not cat.problems(english, answer) else None


def run_jobs(jobs, target, batch, workers):
    """Translate jobs: whole sentences first, then HTML mode for those that failed."""
    def chunks(items):
        return [items[i:i + batch] for i in range(0, len(items), batch)]

    def text_round(group):
        encoded = [text_encode(j.sent()) for j in group]
        todo = [(j, e) for j, e in zip(group, encoded) if e is not None]
        answers = call([e[0] for _j, e in todo], target, "text")
        for (job, (_masked, tokens)), answer in zip(todo, answers):
            job.result = job.finish(text_decode(answer, tokens))

    def html_round(group):
        encoded = [html_encode(j.sent()) for j in group]
        answers = call([e[0] for e in encoded], target, "html")
        for job, (_text, restore), answer in zip(group, encoded, answers):
            job.result = job.finish(html_decode(answer, restore, job.english))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(text_round, chunks(jobs)))
        left = [j for j in jobs if j.result is None]
        list(pool.map(html_round, chunks(left)))


# --- a catalogue ---------------------------------------------------------------------------------------------------------

def update_catalog(key, domain):
    """The language's catalogue, brought up to date with the template (or None when there is no template)."""
    from babel.messages.catalog import Catalog
    from ruqqus.helpers import i18n
    template_path = cat.OUT / f"{domain}.pot"
    if not template_path.exists():
        return None, None
    template = cat.read(template_path, domain=domain)
    path = cat.OUT / key / "LC_MESSAGES" / f"{domain}.po"
    if path.exists():
        catalog = cat.read(path, locale=i18n.babel_code(key), domain=domain)
    else:
        catalog = Catalog(locale=i18n.babel_code(key), domain=domain, project="ruqqus")
    catalog.update(template, no_fuzzy_matching=True)
    catalog.obsolete.clear()
    return catalog, path


def translate_catalog(key, domain, target, limit=None, batch=20, workers=3, flush_every=200, only=None):
    catalog, path = update_catalog(key, domain)
    if catalog is None:
        return {"language": key, "domain": domain, "skipped": "no template"}
    applied = apply_glossary(catalog, key)
    tidy_catalog(catalog)
    todo = []
    for message in catalog:
        if not message.id or (isinstance(message.id, str) and (message.id, message.context or "") in applied):
            continue
        if isinstance(message.id, (list, tuple)):
            done = isinstance(message.string, (list, tuple)) and all(message.string)
        else:
            done = bool(message.string)
        # one it made that today's checks refuse is made again, and one made before the plain-English rules that
        # they would change (a human's own entry, without the flag, is never touched)
        if done and MACHINE in message.flags and (cat.entry_problems(message) or made_before_the_rules(message)):
            done = False
        if not done:
            todo.append(message)
    if only is not None:                       # just these (msgid, context) pairs: the strings of one piece of work, not every old failure
        todo = [m for m in todo if ((m.id if isinstance(m.id, str) else m.id[0]), m.context or None) in only]
    if limit:
        todo = todo[:limit]
    failed = []
    translated = 0
    examples = plural_examples(catalog)
    for start in range(0, len(todo), flush_every):
        group = todo[start:start + flush_every]
        jobs = []
        for message in group:
            single = message.id if isinstance(message.id, str) else None
            hint = SHORT_HINTS.get(single) or GLOSS.get((message.context or "").split(":")[0].strip().lower())
            if isinstance(message.id, (list, tuple)):
                singular, plural = message.id[0], message.id[1]
                forms = []
                for i, n in enumerate(examples):
                    english = singular if n == 1 else plural
                    if "%(num)s" in english and n is not None and catalog.num_plurals > 1:
                        forms.append(Job(english, hint, number=n, optional=i < catalog.num_plurals - 1))
                    else:
                        forms.append(Job(plural if catalog.num_plurals == 1 else english, hint))
                jobs.append((message, forms))
            else:
                jobs.append((message, [Job(message.id, hint)]))
        flat = [j for _m, forms in jobs for j in forms if not _only_values(j.english)]
        run_jobs(flat, target, batch, workers)
        for message, forms in jobs:
            results = [j.english if _only_values(j.english) else j.result for j in forms]
            if any(r is None for r in results):
                failed.append(message.id if isinstance(message.id, str) else message.id[0])
                if MACHINE in message.flags:              # its old machine translation is refused now: gone
                    message.string = tuple("" for _f in forms) if isinstance(message.id, (list, tuple)) else ""
                    message.flags.discard(MACHINE)
                continue
            message.string = tuple(results) if isinstance(message.id, (list, tuple)) else results[0]
            message.flags.add(MACHINE)
            message.flags.discard(PLAIN_FLAG_BEFORE_SIGN_OUT)
            message.flags.add(PLAIN_FLAG)
            message.flags.discard("fuzzy")
            translated += 1
        cat.write(catalog, path)
    cat.write(catalog, path)
    return {"language": key, "domain": domain, "to_do": len(todo), "translated": translated,
            "glossary": len(applied), "failed": len(failed), "failed_strings": failed[:200]}


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog="i18n_catalogs.py translate")
    parser.add_argument("--lang", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--domain", default=",".join(cat.DOMAINS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--report", default="")
    parser.add_argument("--only", default="", help="a JSON file of [msgid, context] pairs: translate only those (the rest of the "
                                                 "to-do list, old failures included, is left for a full run)")
    args = parser.parse_args(argv)
    only = None
    if args.only:
        with open(args.only, encoding="utf-8") as f:
            only = {(m, c or None) for m, c in json.load(f)}
    if not lt_url():
        print("LIBRETRANSLATE_URL is not set: run this inside the compose network")
        return 1
    codes = lt_codes()
    from ruqqus.helpers import i18n
    wanted = sorted(k for k in i18n.interface_keys() if k != "en") if args.all else \
        [k.strip() for k in args.lang.split(",") if k.strip()]
    report = []
    for key in wanted:
        if key not in codes:
            print(f"{key}: LibreTranslate hasn't loaded it (LT_LOAD_ONLY): skipped")
            report.append({"language": key, "skipped": "not loaded"})
            continue
        for domain in [d.strip() for d in args.domain.split(",") if d.strip()]:
            started = time.time()
            result = translate_catalog(key, domain, codes[key], limit=args.limit or None, batch=args.batch,
                                       workers=args.workers, only=only)
            result["seconds"] = round(time.time() - started)
            report.append(result)
            print(f"{key} {domain}: {result.get('translated', 0)} translated, {result.get('failed', 0)} failed "
                  f"of {result.get('to_do', 0)} ({result['seconds']} s)", flush=True)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0
