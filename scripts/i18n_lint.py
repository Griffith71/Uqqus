"""Finds interface text that isn't marked for translation (docs/i18n.md).

    PYTHONPATH=. python scripts/i18n_lint.py                 # everything
    PYTHONPATH=. python scripts/i18n_lint.py ruqqus/templates/settings_profile.html ruqqus/routes/votes.py
    PYTHONPATH=. python scripts/i18n_lint.py --count          # how many findings per file

What it reads:
* **Templates**: text between tags and the attributes a reader sees or hears (title, alt, placeholder, aria-label,
  data-original-title, value of buttons) outside `_()` / `{% trans %}`. Admin pages and the legal pages stay English
  (`ENGLISH_ONLY`); emails and help pages are read like any page.
* **Python**: an English sentence (a capital letter or a space, and two letters) given to `jsonify({"error": ...})`,
  `{"message": ...}`, `{"notice": ...}`, `render_template(..., error=/msg=/message=)`, `send_notification(...)`,
  `send_to_many(...)`, `abort(code, "...")`, or assigned to a `*_REFUSAL` / `*_NOTICE` / `*_MESSAGE` constant, and
  not wrapped in tr() / tr_lazy() / N_(); and `tr(...)` whose text isn't a literal (it can't be extracted).
* **Scripts**: a string literal with words set as `textContent`, `innerText`, `title`, `placeholder`, `alt`,
  `aria-label`, given to `alert`/`confirm`/`prompt`/a toast, or put in `.textContent = cond ? "a" : "b"`, outside
  `tr(` / `trn(`.

Exceptions are listed, never commented in the code: ruqqus/translations/lint_allow.txt holds `path: text` lines (the
text exactly as reported). Exit code 1 when anything is found.
"""
import ast
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ruqqus"
ALLOW = APP / "translations" / "lint_allow.txt"

# never translated: staff pages, and the legal text whose English is the binding version (a translated note says so)
ENGLISH_ONLY = ("admin/", "legal/", "help/terms.html", "help/privacy.html", "help/rules.html", "help/dmca.html",
                "help/counter_dmca.html", "help/legal.html", "help/canaries.html", "help/csam.html",
                "help/dmca_email.md", "help/dmca_notice.md", "partials/posting_rules.html", "errors/502.html",
                "errors/503.html", "embeds/comment_removed.html")
JS_SKIP = {"chat_bundle.js", "call_bundle.js", "space_bundle.js", "chat_apps_bundle.js", "bootstrap.js",
           "bootstrap.min.js", "i18n.js"}

WORDY = re.compile(r"[A-Za-z]{2,}")
SPOKEN_ATTRIBUTES = {"title", "alt", "placeholder", "aria-label", "data-original-title", "aria-description",
                     "aria-roledescription", "data-title", "data-placeholder"}


def allowed():
    out = set()
    if ALLOW.exists():
        for line in ALLOW.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ": " in line:
                path, text = line.split(": ", 1)
                out.add((path.strip(), text.strip()))
    return out


def looks_like_words(text):
    """Text a reader would read: two letters in a row, not only a code, an address, a number or an entity."""
    text = re.sub(r"&[#\w]+;", " ", text).strip()
    if not text or not WORDY.search(text):
        return False
    if re.fullmatch(r"[\w.\-/:#?=&%+@]+", text) and not re.search(r"[A-Z][a-z]", text) and " " not in text:
        return False                       # an id, a class, an address, a code
    if re.fullmatch(r"(https?://|/|#|\+|@|&)\S*", text):
        return False
    return True


# --- templates ---------------------------------------------------------------------------------------------------------

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.found = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if name in SPOKEN_ATTRIBUTES and value and looks_like_words(value) and "\x00" not in value:
                self.found.append(value.strip())
        if tag in ("input", "button") and any(n == "type" and v in ("submit", "button") for n, v in attrs):
            for name, value in attrs:
                if name == "value" and value and looks_like_words(value) and "\x00" not in value:
                    self.found.append(value.strip())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            return
        for piece in data.split("\x00"):
            piece = " ".join(piece.split())
            if looks_like_words(piece):
                self.found.append(piece)


def _template_text(source):
    """The template with everything Jinja and everything marked replaced by a NUL, so only plain text is left."""
    out, i, n = [], 0, len(source)
    in_trans = 0
    while i < n:
        if source.startswith("{#", i):
            end = source.find("#}", i)
            i = n if end == -1 else end + 2
            out.append("\x00")
            continue
        if source.startswith("{%", i):
            end = source.find("%}", i)
            tag = source[i + 2:end].strip("- \n\t") if end != -1 else ""
            word = tag.split()[0] if tag.split() else ""
            if word in ("trans", "pluralize"):
                in_trans = 1 if word == "trans" else in_trans
            elif word == "endtrans":
                in_trans = 0
            elif word in ("raw",):
                pass
            i = n if end == -1 else end + 2
            out.append("\x00")
            continue
        if source.startswith("{{", i):
            end = source.find("}}", i)
            i = n if end == -1 else end + 2
            out.append("\x00")
            continue
        ch = source[i]
        out.append("\x00" if in_trans and ch not in "<>\"'= \n" else ch)
        i += 1
    return "".join(out)


_ENV = {}


def _parses(path):
    """A template must still compile with the site's extensions (helpers/i18n_jinja.py), or it is reported."""
    if "env" not in _ENV:
        from ruqqus.helpers.i18n_jinja import environment
        _ENV["env"] = environment()
    try:
        _ENV["env"].parse(path.read_text(encoding="utf-8"))
        return None
    except Exception as e:                      # any syntax error is a finding
        return f"TEMPLATE DOES NOT PARSE: {type(e).__name__}: {e}"


def check_template(path):
    rel = path.relative_to(APP / "templates").as_posix()
    broken = _parses(path)
    if broken:
        return [broken]
    if rel.startswith(ENGLISH_ONLY) or rel in ENGLISH_ONLY:
        return []
    parser = _Text()
    parser.feed(_template_text(path.read_text(encoding="utf-8")))
    parser.close()
    return parser.found


# --- Python ------------------------------------------------------------------------------------------------------------

MARKERS = {"tr", "tr_lazy", "N_", "trn", "trp", "trnp", "msg"}
SENDERS = {"send_notification", "send_to_many", "notify", "notify_many"}
MESSAGE_KEYS = {"error", "message", "notice"}
TEMPLATE_KWARGS = {"error", "msg", "message"}
CONSTANT = re.compile(r"[A-Z0-9_]*(REFUSAL|NOTICE|MESSAGE|_TEXT|_TITLE)$")


def _sentence(node):
    """The English of a literal or f-string that reads as a sentence, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    else:
        return None
    text = text.strip()
    if not text or not looks_like_words(text) or not (" " in text or text[:1].isupper()):
        return None
    return text


def _call_name(node):
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def check_python(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found = []

    def unmarked(node):
        text = _sentence(node)
        if text:
            found.append(text)
        elif isinstance(node, ast.IfExp):
            unmarked(node.body)
            unmarked(node.orelse)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in MESSAGE_KEYS:
                    unmarked(value)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in MARKERS and name != "msg":
                first = node.args[1] if name in ("trp", "trnp") and len(node.args) > 1 else (
                    node.args[0] if node.args else None)
                if first is not None and not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    found.append(f"{name}() of something that isn't a literal")
            elif name in SENDERS and len(node.args) >= 2:
                unmarked(node.args[1])
            elif name == "abort" and len(node.args) >= 2:
                unmarked(node.args[1])
            elif name == "render_template":
                for kw in node.keywords:
                    if kw.arg in TEMPLATE_KWARGS:
                        unmarked(kw.value)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if CONSTANT.search(node.targets[0].id):
                unmarked(node.value)
    return found


# --- scripts -----------------------------------------------------------------------------------------------------------

_STRING = r"(?:\"((?:[^\"\\\n]|\\.)*)\"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`)"
JS_SINKS = [
    re.compile(r"\.(?:textContent|innerText|title|placeholder|alt|value)\s*=\s*" + _STRING),
    re.compile(r"setAttribute\(\s*['\"](?:aria-label|title|placeholder|alt|aria-description)['\"]\s*,\s*" + _STRING),
    re.compile(r"\b(?:alert|confirm|prompt)\(\s*" + _STRING),
    re.compile(r"\b(?:toast|showToast|post_toast_error|failed|say|setStatus|status)\(\s*" + _STRING),
    re.compile(r"\.(?:textContent|innerText)\s*=\s*[^;\n?]*\?\s*" + _STRING + r"\s*:\s*" + _STRING),
]


def check_script(path):
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("//")[0] if "://" not in line else line
        for pattern in JS_SINKS:
            for match in pattern.finditer(code):
                for text in match.groups():
                    if text and looks_like_words(text) and not re.fullmatch(r"[\w\-]+", text):
                        found.append(text)
    return found


# --- running -----------------------------------------------------------------------------------------------------------

def files(args):
    if args:
        for arg in args:
            path = (ROOT / arg).resolve() if not Path(arg).is_absolute() else Path(arg)
            if path.is_dir():
                yield from sorted(p for p in path.rglob("*") if p.suffix in (".html", ".py", ".js"))
            else:
                yield path
        return
    yield from sorted((APP / "templates").rglob("*.html"))
    yield from sorted(p for p in APP.rglob("*.py") if "node_modules" not in p.parts)
    yield from sorted((ROOT / "scripts").glob("*.py"))
    yield from sorted(p for p in (APP / "assets" / "js").glob("*.js") if p.name not in JS_SKIP)
    yield from sorted((APP / "assets" / "chat_src" / "src").rglob("*.js"))


def check(path):
    if path.suffix == ".html" and (APP / "templates") in path.parents:
        return check_template(path)
    if path.suffix == ".py":
        if path.name in ("i18n_lint.py", "i18n_catalogs.py", "i18n_translate.py"):
            return []
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as e:
            return [f"PYTHON DOES NOT COMPILE: {e}"]
        return check_python(path)
    if path.suffix == ".js" and path.name not in JS_SKIP and "node_modules" not in path.parts:
        return check_script(path)
    return []


def run(args, count=False):
    allow = allowed()
    total = 0
    for path in files(args):
        rel = path.relative_to(ROOT).as_posix()
        found = [t for t in (" ".join(x.split()) for x in check(path)) if (rel, t) not in allow]
        if not found:
            continue
        total += len(found)
        if count:
            print(f"{len(found):5d}  {rel}")
        else:
            for text in found:
                print(f"{rel}: {text}")
    print(f"unmarked: {total}", file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    counting = "--count" in argv
    sys.exit(run([a for a in argv if a != "--count"], count=counting))
