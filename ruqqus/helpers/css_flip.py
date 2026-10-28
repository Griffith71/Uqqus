"""Right-to-left stylesheets (the interface in Arabic, Persian, Hebrew or Urdu: helpers/i18n.py RTL), made from the
compiled left-to-right ones, the way rtlcss does it, so every rule (Bootstrap's .ml-3, .float-left, .text-left among
them) mirrors without a second copy of the SCSS. Pure: CSS text in, CSS text out (routes/static.py, routes/boards.py
serve the result as main.rtl.css / main_dark.rtl.css and a guild's rtl sheets).

What flips:
* properties named left / right (margin-left, padding-right, left, border-top-left-radius...): the other side;
* left / right in float, clear, text-align, transform-origin, transition, background(-position): swapped;
* four-value margin, padding, inset, border-width / -style / -color, scroll-margin / -padding: 1 2 3 4 -> 1 4 3 2;
* border-radius: its corners mirrored;
* translateX / translate / translate3d: the x offset negated; box-shadow / text-shadow: the x offset negated;
* cursor e-resize <-> w-resize (and the corners), direction ltr <-> rtl;
* env(safe-area-inset-left/right) names, so a padding that moved to the other side keeps the physical inset.

What doesn't: custom properties (`--name: ...`), selectors, at-rule preludes. `/*rtl:ignore*/` before a declaration or
a rule leaves it as it is; `/*rtl:begin:ignore*/ ... /*rtl:end:ignore*/` leaves everything between.
"""
import re

_WORD = re.compile(r"(?<![\w-])(left|right)(?![\w-])")
_SAFE = re.compile(r"safe-area-inset-(left|right)")
_SWAP = {"left": "right", "right": "left"}
_KEYWORD_PROPS = ("float", "clear", "text-align", "text-align-last", "transform-origin", "transition",
                  "transition-property", "background", "background-position", "background-position-x",
                  "perspective-origin", "caption-side", "justify-self", "justify-items", "mask-position")
_FOUR = {"margin", "padding", "inset", "border-width", "border-style", "border-color", "scroll-margin",
         "scroll-padding"}
_CURSORS = {"e-resize": "w-resize", "w-resize": "e-resize", "ne-resize": "nw-resize", "nw-resize": "ne-resize",
            "se-resize": "sw-resize", "sw-resize": "se-resize", "nesw-resize": "nwse-resize",
            "nwse-resize": "nesw-resize"}


def _swap_words(text):
    return _WORD.sub(lambda m: _SWAP[m.group(1)], text)


def _swap_safe(text):
    return _SAFE.sub(lambda m: "safe-area-inset-" + _SWAP[m.group(1)], text)


def _outside_urls(value, fn):
    """fn applied to the value outside url(...) and quoted strings (a data: address must never change)."""
    out, i, n, start = [], 0, len(value), 0
    while i < n:
        if value.startswith("url(", i):
            out.append(fn(value[start:i]))
            depth, j = 0, i
            while j < n:
                if value[j] == "(":
                    depth += 1
                elif value[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif value[j] in "\"'":
                    quote, j = value[j], j + 1
                    while j < n and value[j] != quote:
                        j += 1
                j += 1
            out.append(value[i:j + 1])
            i = start = j + 1
            continue
        if value[i] in "\"'":
            out.append(fn(value[start:i]))
            quote, j = value[i], i + 1
            while j < n and value[j] != quote:
                j += 2 if value[j] == "\\" else 1
            out.append(value[i:j + 1])
            i = start = j + 1
            continue
        i += 1
    out.append(fn(value[start:]))
    return "".join(out)


def _split_top(value, sep=None):
    """Split at whitespace (sep None) or at `sep`, outside parentheses and strings."""
    parts, depth, current, quote = [], 0, [], None
    for ch in value:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and ((sep is None and ch.isspace()) or ch == sep):
            if sep is None:
                if current:
                    parts.append("".join(current))
                current = []
                continue
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current or sep is not None:
        parts.append("".join(current))
    return parts


def _negate(length):
    length = length.strip()
    if not length:
        return length
    if length.startswith("-"):
        return length[1:]
    if length.startswith("+"):
        length = length[1:]
    if re.fullmatch(r"0+(\.0+)?[a-z%]*", length):
        return length
    if length.startswith(("calc(", "var(", "env(", "min(", "max(", "clamp(")):
        return f"calc(-1 * {length})"
    return "-" + length


def _flip_transform(value):
    def x_first(match):
        name, args = match.group(1), _split_top(match.group(2), ",")
        if args:
            args[0] = (" " if args[0].startswith(" ") else "") + _negate(args[0])
        return f"{name}({','.join(args)})"
    return re.sub(r"\b(translateX|translate|translate3d)\(((?:[^()]|\([^()]*\))*)\)", x_first, value)


def _flip_shadow(value):
    shadows = []
    for shadow in _split_top(value, ","):
        tokens = _split_top(shadow)
        for i, token in enumerate(tokens):
            if token == "inset":
                continue
            # only a length: a whole shadow held in a variable (var(--shadow-md)) is left as it is
            if re.match(r"^[-+]?(\d|\.\d|calc\()", token):
                tokens[i] = _negate(token)
            break
        lead = " " if shadow.startswith(" ") else ""
        shadows.append(lead + " ".join(tokens))
    return ",".join(shadows)


def _flip_radius(value):
    sides = []
    for side in _split_top(value, "/"):
        tokens = _split_top(side)
        if len(tokens) == 2:
            tokens = [tokens[1], tokens[0]]
        elif len(tokens) == 3:
            tokens = [tokens[1], tokens[0], tokens[1], tokens[2]]
        elif len(tokens) == 4:
            tokens = [tokens[1], tokens[0], tokens[3], tokens[2]]
        sides.append(" ".join(tokens))
    return " / ".join(s.strip() for s in sides)


def flip_declaration(prop, value):
    """(property, value) for right to left."""
    name = prop.strip()
    if name.startswith("--"):
        return prop, value
    lower = name.lower()
    important = ""
    body = value
    found = re.search(r"\s*!important\s*$", body, re.I)
    if found:
        important, body = body[found.start():], body[:found.start()]
    new_prop = "-".join(_SWAP.get(part, part) for part in lower.split("-")) if ("left" in lower or "right" in lower) \
        else lower
    if new_prop != lower:
        new_prop = prop.replace(name, new_prop)
    else:
        new_prop = prop
    base = re.sub(r"^-(webkit|moz|ms|o)-", "", lower)
    if base in _FOUR:
        tokens = _split_top(body.strip())
        if len(tokens) == 4:
            lead = body[:len(body) - len(body.lstrip())]
            body = lead + " ".join([tokens[0], tokens[3], tokens[2], tokens[1]])
    elif base == "border-radius":
        lead = body[:len(body) - len(body.lstrip())]
        body = lead + _flip_radius(body.strip())
    elif base in ("transform", "translate"):
        body = _flip_transform(body) if base == "transform" else _flip_translate_property(body)
    elif base in ("box-shadow", "text-shadow"):
        body = _flip_shadow(body)
    elif base == "cursor":
        body = re.sub(r"\b[a-z]+-resize\b", lambda m: _CURSORS.get(m.group(0), m.group(0)), body)
    elif base == "direction":
        body = re.sub(r"\b(ltr|rtl)\b", lambda m: "rtl" if m.group(1) == "ltr" else "ltr", body)
    if base in _KEYWORD_PROPS or base.startswith("transition"):
        body = _outside_urls(body, _swap_words)
    if base == "background-position-x":
        body = re.sub(r"(?<![\w.-])(\d+(?:\.\d+)?)%", lambda m: f"{100 - float(m.group(1)):g}%", body)
    body = _outside_urls(body, _swap_safe)
    return new_prop, body + important


def _flip_translate_property(value):
    tokens = _split_top(value.strip())
    if tokens:
        tokens[0] = _negate(tokens[0])
    return " ".join(tokens)


def flip(css):
    """The right-to-left version of a stylesheet."""
    out = []
    i, n = 0, len(css)
    statement = []              # the text since the last ; { or }
    ignore_next = False
    ignoring_block = 0          # depth of a rule /*rtl:ignore*/ named, while inside it
    ignore_range = False
    depth = 0
    paren = 0
    quote = None

    def flush_declaration(text):
        nonlocal ignore_next
        if not text.strip():
            out.append(text)
            return
        lead = re.match(r"(?:\s|/\*.*?\*/)*", text, re.S).group(0)        # comments before the declaration
        body = text[len(lead):]
        if ignore_next or ignoring_block or ignore_range or ":" not in body:
            ignore_next = False
            out.append(text)
            return
        prop, _sep, value = body.partition(":")
        if prop.strip().startswith("@") or not re.fullmatch(r"\s*-?-?[a-zA-Z][\w-]*\s*", prop):
            out.append(text)
            return
        new_prop, new_value = flip_declaration(prop, value)
        out.append(f"{lead}{new_prop}:{new_value}")

    while i < n:
        ch = css[i]
        if quote:
            statement.append(ch)
            if ch == "\\" and i + 1 < n:
                statement.append(css[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = n if end == -1 else end + 2
            comment = css[i:end]
            directive = comment[2:-2].strip().replace(" ", "").lower()
            if directive == "rtl:ignore":
                ignore_next = True
            elif directive == "rtl:begin:ignore":
                ignore_range = True
            elif directive == "rtl:end:ignore":
                ignore_range = False
            statement.append(comment)
            i = end
            continue
        if ch in "\"'":
            quote = ch
            statement.append(ch)
            i += 1
            continue
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        if paren == 0 and ch == ";":
            flush_declaration("".join(statement))
            statement = []
            out.append(";")
            i += 1
            continue
        if paren == 0 and ch == "{":
            prelude = "".join(statement)
            out.append(prelude)
            statement = []
            out.append("{")
            depth += 1
            if ignore_next and not ignoring_block:
                ignoring_block = depth
                ignore_next = False
            i += 1
            continue
        if paren == 0 and ch == "}":
            flush_declaration("".join(statement))
            statement = []
            out.append("}")
            if ignoring_block and depth == ignoring_block:
                ignoring_block = 0
            depth -= 1
            i += 1
            continue
        statement.append(ch)
        i += 1
    out.append("".join(statement))
    return "".join(out)
