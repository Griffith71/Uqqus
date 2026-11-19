"""The interface's catalogues (helpers/i18n_runtime.py; docs/i18n.md).

    PYTHONPATH=. python scripts/i18n_catalogs.py extract       # the marked strings -> ruqqus/translations/*.pot
    PYTHONPATH=. python scripts/i18n_catalogs.py check         # every catalogue is safe to draw (also a test)
    PYTHONPATH=. python scripts/i18n_catalogs.py compile-js    # js.po -> ruqqus/assets/i18n/<key>.js (committed)
    PYTHONPATH=. python scripts/i18n_catalogs.py compile-mo    # *.po -> *.mo (the Dockerfile; gitignored)
    PYTHONPATH=. python scripts/i18n_catalogs.py translate ...  # machine translation by the site's LibreTranslate
    PYTHONPATH=. python scripts/i18n_catalogs.py glossary      # the site's words (glossary.json) into every .po, broken machine entries emptied

Three domains: `messages` (Python and the templates), `help` (the help pages: long text, translated on their own) and
`js` (the site's scripts and the chat client). Each language's catalogues are ruqqus/translations/<key>/LC_MESSAGES/.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
OUT = APP / "translations"
JS_OUT = APP / "assets" / "i18n"

PY_KEYWORDS = {"tr": None, "tr_lazy": None, "N_": None, "trn": (1, 2), "trp": ((1, "c"), 2), "trnp": ((1, "c"), 2, 3)}
JINJA_KEYWORDS = {"_": None, "gettext": None, "ngettext": (1, 2), "pgettext": ((1, "c"), 2),
                  "npgettext": ((1, "c"), 2, 3)}
JS_KEYWORDS = {"tr": None, "trn": (1, 2)}
JINJA_OPTIONS = {"extensions": "jinja2.ext.do,jinja2.ext.i18n", "silent": "false", "newstyle_gettext": "true",
                 "trimmed": "true"}

# scripts that are bundles, minified copies or vendor code: never marked, never read
JS_SKIP = {"chat_bundle.js", "call_bundle.js", "space_bundle.js", "chat_apps_bundle.js", "bootstrap.js",
           "bootstrap.min.js", "i18n.js"}


def _templates(help_pages):
    for path in sorted((APP / "templates").rglob("*.html")):
        rel = path.relative_to(APP / "templates").as_posix()
        if rel.startswith("help/") == help_pages:
            yield path


def sources(domain):
    """(path, method, keywords, options) for every file of a domain."""
    if domain == "messages":
        for path in sorted(list(APP.rglob("*.py")) + list((ROOT / "scripts").glob("*.py"))):
            if "node_modules" not in path.parts:
                yield path, "python", PY_KEYWORDS, {}
        for path in _templates(help_pages=False):
            yield path, "jinja2.ext:babel_extract", JINJA_KEYWORDS, JINJA_OPTIONS
    elif domain == "help":
        for path in _templates(help_pages=True):
            yield path, "jinja2.ext:babel_extract", JINJA_KEYWORDS, JINJA_OPTIONS
    elif domain == "js":
        for path in sorted((APP / "assets" / "js").glob("*.js")):
            if path.name not in JS_SKIP and not path.name.endswith(".min.js"):
                yield path, "javascript", JS_KEYWORDS, {}
        for path in sorted((APP / "assets" / "chat_src" / "src").rglob("*.js")):
            yield path, "javascript", JS_KEYWORDS, {}
    else:
        raise ValueError(domain)


DOMAINS = ("messages", "help", "js")


def extract(domain):
    """The domain's template catalogue: every marked string, with its translator comments."""
    from babel.messages.catalog import Catalog
    from babel.messages.extract import extract_from_file
    catalog = Catalog(project="ruqqus", domain=domain, charset="utf-8")
    for path, method, keywords, options in sources(domain):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, message, comments, context in extract_from_file(
                method, str(path), keywords=keywords, comment_tags=("Translators:",), options=options,
                strip_comment_tags=True):
            if not message or (isinstance(message, tuple) and not message[0]):
                continue
            catalog.add(message, None, [(rel, lineno)], auto_comments=comments, context=context)
    return catalog


def write(catalog, path):
    from babel.messages.pofile import write_po
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        write_po(f, catalog, width=0, no_location=True, sort_output=True, omit_header=False)


def read(path, locale=None, domain=None):
    from babel.messages.pofile import read_po
    with path.open("rb") as f:
        return read_po(f, locale=locale, domain=domain)


def cmd_extract():
    for domain in DOMAINS:
        catalog = extract(domain)
        write(catalog, OUT / f"{domain}.pot")
        print(f"{domain}: {len(catalog)} strings")


# --- checking ---------------------------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"%\((\w+)\)[sd]")
# a tag starts right after "<" (never "< or >" in a sentence)
_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")
_ENTITY = re.compile(r"&[#\w]+;")
_LETTERS = re.compile(r"[^\W\d_]{2,}")


def _bare(text):
    """A string without its values, tags and entities: the words a reader reads."""
    return _ENTITY.sub(" ", _TAG.sub(" ", _PLACEHOLDER.sub(" ", text or "")))


_REPEATED_UNIT = re.compile(r"(\S.{0,5}?)\1{4,}")     # a short piece five times in a row ("犬・犬・犬・犬・犬・")
_SUBTITLE_CODES = re.compile(r"\{\\[a-z]")           # "{\fn黑体\fs22...}": LibreTranslate learned from subtitles


def _loops(text, short=False):
    """Does a string repeat itself the way a machine loops? A word (or another piece between spaces) three times in a
    row ("After After After"); in a short label also a run of two or three words twice ("Pa used Pa used", "Posting to
    posting to"): in a long sentence that can be a language's own ("navegador a navegador a través")."""
    tokens = _bare(text).casefold().split()
    for size, times in ((1, 3), (2, 2), (3, 2)):
        if size > 1 and not short:
            break
        for start in range(len(tokens) - size):
            count = 1
            while tokens[start + count * size:start + (count + 1) * size] == tokens[start:start + size]:
                count += 1
            if count >= times:
                return True
    return False


def _width(text):
    from ruqqus.helpers.text_length import weighted_length
    return weighted_length(_bare(text))


_UNSAFE = re.compile(r"<\s*script|<\s*style|<\s*iframe|\bon[a-z]+\s*=|javascript:|data:text/html", re.I)


def placeholders(text):
    return sorted(_PLACEHOLDER.findall(text or ""))


def tags(text):
    """The tags of a string as (closing, name, attributes) in order."""
    return [(m.group(1), m.group(2).lower(), m.group(3).strip()) for m in _TAG.finditer(text or "")]


def problems(msgid, msgstr):
    """Why a translation may not be drawn, or [] when it may: it names the same values, has only the English's tags
    (with the English's attributes), nothing unsafe, no stray percent sign, and is not a machine's loop or padding (a
    word repeated, far longer than the English, subtitle codes)."""
    found = []
    if placeholders(msgstr) != placeholders(msgid):
        found.append("placeholders differ")
    if sorted(_ENTITY.findall(msgstr or "")) != sorted(_ENTITY.findall(msgid or "")):
        found.append("entities differ")
    if _LETTERS.search(_bare(msgid)) and not re.search(r"[^\W\d_]", _bare(msgstr)):
        found.append("no words left")                  # (one kana or hanzi is a word: "%(age)s の %(who)s")
    if (msgstr or "").count("```") != (msgid or "").count("```") or (msgstr or "").count("](") != (msgid or "").count("]("):
        found.append("a code fence or a link was lost")          # the fence keeps a person's words from becoming a link (the second review, C62)
    english_tags = tags(msgid)
    allowed = {(name, attrs) for _c, name, attrs in english_tags}
    for _closing, name, attrs in tags(msgstr):
        if (name, attrs) not in allowed and not (_closing and any(n == name for n, _a in allowed)):
            found.append(f"tag <{name} {attrs}> is not the English's")
    if sorted(n for _c, n, _a in tags(msgstr)) != sorted(n for _c, n, _a in english_tags):
        found.append("tags differ")
    if _UNSAFE.search(msgstr or "") and not _UNSAFE.search(msgid or ""):
        found.append("unsafe markup")
    # LibreTranslate sometimes loops ("After After After ..." hundreds of times) or pads: nothing that would break a
    # layout is drawn (the owner, 2026-10-25)
    short = len(_bare(msgid).split()) <= 6
    if _loops(msgstr, short) and not _loops(msgid, short):
        found.append("repeats a word")
    elif _REPEATED_UNIT.search(_bare(msgstr)) and not _REPEATED_UNIT.search(_bare(msgid)):
        found.append("repeats a word")
    if _width(msgstr) > max(3 * _width(msgid), _width(msgid) + 40):
        found.append("much longer than the English")
    if _SUBTITLE_CODES.search(msgstr or "") and not _SUBTITLE_CODES.search(msgid or ""):
        found.append("subtitle codes")
    # a mark written straight before a value ("Anonymous #%(num)s", "post #%(id)s") is kept: machines turned it into "Anonyme%(num)s" and "Anonimo!%(num)s"
    for name in sorted(set(re.findall(r"#%\((\w+)\)[sd]", msgid or ""))):
        if not re.search(r"#\s?%\(" + name + r"\)[sd]", msgstr or ""):
            found.append("a # before a value is dropped")
            break
    stray = re.sub(r"%\(\w+\)[sd]|%%", "", msgstr or "")
    if "%" in stray and "%" not in re.sub(r"%\(\w+\)[sd]|%%", "", msgid or ""):
        found.append("a lone % (write %%)")
    return found


def entry_problems(message):
    """problems() for every form of a catalogue entry (plurals: each form against the English plural)."""
    if not message.string:
        return []
    if isinstance(message.id, (list, tuple)):
        singular, plural = message.id[0], message.id[1]
        forms = message.string if isinstance(message.string, (list, tuple)) else [message.string]
        out = []
        for i, form in enumerate(forms):
            if form:
                # a form before the last ("one", Arabic's "two") may say the number with a word and drop %(num)s;
                # the last ("other") keeps it; no form drops another value
                english = plural
                if "%(num)s" not in form and i < len(forms) - 1:
                    english = english.replace("%(num)s", "")
                out += problems(english, form)
        return out
    return problems(message.id, message.string)


def language_folders():
    if not OUT.exists():
        return []
    return sorted(p for p in OUT.iterdir() if p.is_dir() and (p / "LC_MESSAGES").exists())


def cmd_check():
    bad = 0
    for folder in language_folders():
        for po in sorted((folder / "LC_MESSAGES").glob("*.po")):
            catalog = read(po)
            for message in catalog:
                if not message.id or message.fuzzy:
                    continue
                found = entry_problems(message)
                if found:
                    bad += 1
                    print(f"{po.relative_to(ROOT)}: {message.id!r}: {', '.join(found)}")
    print("catalogues: ok" if not bad else f"catalogues: {bad} entries to fix")
    return 1 if bad else 0


# --- compiling --------------------------------------------------------------------------------------------------------

def js_table(catalog):
    """(messages, plural expression) for the scripts: a singular's translation, or "singular\\0plural" -> its forms."""
    table = {}
    for message in catalog:
        if not message.id or message.fuzzy or not message.string or entry_problems(message):
            continue
        if isinstance(message.id, (list, tuple)):
            forms = list(message.string)
            if all(forms):
                table[f"{message.id[0]}\u0000{message.id[1]}"] = forms
        else:
            table[message.id] = message.string
    return table, catalog.plural_expr


def js_source(key, catalog):
    table, plural = js_table(catalog)
    return ("// Made by scripts/i18n_catalogs.py compile-js from ruqqus/translations/%s/LC_MESSAGES/js.po: never edit by "
            "hand.\nwindow.I18N = window.I18N || {};\nwindow.I18N.messages = %s;\nwindow.I18N.plural = function (n) "
            "{ return Number(%s); };\n" % (key, json.dumps(table, ensure_ascii=False, sort_keys=True), plural))


def cmd_compile_js():
    from ruqqus.helpers import i18n
    JS_OUT.mkdir(parents=True, exist_ok=True)
    made = set()
    for folder in language_folders():
        po = folder / "LC_MESSAGES" / "js.po"
        if not po.exists():
            continue
        catalog = read(po, locale=i18n.babel_code(folder.name), domain="js")
        (JS_OUT / f"{folder.name}.js").write_text(js_source(folder.name, catalog), encoding="utf-8")
        made.add(f"{folder.name}.js")
    for old in JS_OUT.glob("*.js"):
        if old.name not in made:
            old.unlink()
    print(f"scripts' tables: {len(made)}")


def cmd_compile_mo():
    from babel.messages.mofile import write_mo
    from ruqqus.helpers import i18n
    count = 0
    for folder in language_folders():
        for po in (folder / "LC_MESSAGES").glob("*.po"):
            if po.stem == "js":
                continue
            catalog = read(po, locale=i18n.babel_code(folder.name), domain=po.stem)
            with po.with_suffix(".mo").open("wb") as f:
                write_mo(f, catalog, use_fuzzy=False)
            count += 1
    print(f"compiled: {count}")


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    command = argv[0]
    if command == "extract":
        cmd_extract()
        return 0
    if command == "check":
        return cmd_check()
    if command == "compile-js":
        cmd_compile_js()
        return 0
    if command == "compile-mo":
        cmd_compile_mo()
        return 0
    if command == "translate":
        from scripts import i18n_translate
        return i18n_translate.main(argv[1:])
    if command == "glossary":                     # [--lang es,fr]
        from scripts import i18n_translate
        keys = argv[2].split(",") if len(argv) > 2 and argv[1] == "--lang" else None
        return i18n_translate.glossary_catalogs(keys)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
