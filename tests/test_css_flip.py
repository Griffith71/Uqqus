"""Right-to-left stylesheets (helpers/css_flip.py): the compiled left-to-right CSS mirrored the way rtlcss does it, for
the interface in Arabic, Persian, Hebrew and Urdu."""
import re
from pathlib import Path

import pytest

from ruqqus.helpers.css_flip import flip, flip_declaration as f

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("prop, value, expected", [
    ("margin-left", " 2px", ("margin-right", " 2px")),
    ("padding-right", " 1rem !important", ("padding-left", " 1rem !important")),
    ("left", " 0", ("right", " 0")),
    ("border-left", " 1px solid red", ("border-right", " 1px solid red")),
    ("border-top-left-radius", " 3px", ("border-top-right-radius", " 3px")),
    ("border-bottom-right-radius", " 3px", ("border-bottom-left-radius", " 3px")),
    ("float", " left", ("float", " right")),
    ("clear", " right", ("clear", " left")),
    ("text-align", " right !important", ("text-align", " left !important")),
    ("text-align", " center", ("text-align", " center")),
    ("padding", " 1px 2px 3px 4px", ("padding", " 1px 4px 3px 2px")),
    ("margin", " 0 auto", ("margin", " 0 auto")),
    ("margin", " 1px 2px 3px", ("margin", " 1px 2px 3px")),
    ("border-width", " 1px 2px 3px 4px", ("border-width", " 1px 4px 3px 2px")),
    ("border-radius", " 1px 2px 3px 4px", ("border-radius", " 2px 1px 4px 3px")),
    ("border-radius", " 4px 0", ("border-radius", " 0 4px")),
    ("border-radius", " 1px 2px 3px", ("border-radius", " 2px 1px 2px 3px")),
    ("border-radius", " 1px 2px / 3px 4px", ("border-radius", " 2px 1px / 4px 3px")),
    ("transform", " translateX(1.25rem)", ("transform", " translateX(-1.25rem)")),
    ("transform", " translateX(-100%)", ("transform", " translateX(100%)")),
    ("transform", " translate(-50%, -50%)", ("transform", " translate(50%, -50%)")),
    ("transform", " translateX(0) scale(0.6)", ("transform", " translateX(0) scale(0.6)")),
    ("transform", " translateX(var(--x))", ("transform", " translateX(calc(-1 * var(--x)))")),
    ("transform", " rotate(45deg)", ("transform", " rotate(45deg)")),
    ("box-shadow", " 2px 0 3px red, inset -1px 0 0 blue", ("box-shadow", " -2px 0 3px red, inset 1px 0 0 blue")),
    ("box-shadow", " 0 0 0 0.2rem rgba(1, 2, 3, 0.25)", ("box-shadow", " 0 0 0 0.2rem rgba(1, 2, 3, 0.25)")),
    ("box-shadow", " var(--shadow-md)", ("box-shadow", " var(--shadow-md)")),
    ("cursor", " e-resize", ("cursor", " w-resize")),
    ("direction", " ltr", ("direction", " rtl")),
    ("transform-origin", " top left", ("transform-origin", " top right")),
    ("transition", " right ease 0.25s", ("transition", " left ease 0.25s")),
    ("background-position", " right calc(0.375em + 0.25rem) center",
     ("background-position", " left calc(0.375em + 0.25rem) center")),
    ("background-position-x", " 30%", ("background-position-x", " 70%")),
    ("--inset-left", " left", ("--inset-left", " left")),              # custom properties are left alone
])
def test_a_declaration_mirrors(prop, value, expected):
    assert f(prop, value) == expected


def test_addresses_and_names_inside_values_never_change():
    _prop, value = f("background", ' url("data:image/svg+xml,left right") no-repeat right 1rem center')
    assert 'url("data:image/svg+xml,left right")' in value and value.endswith("left 1rem center")
    # a padding that moved to the other side keeps the physical safe-area inset
    assert f("padding-left", " env(safe-area-inset-left)") == ("padding-right", " env(safe-area-inset-right)")
    assert f("padding", " 0 env(safe-area-inset-right) 0 env(safe-area-inset-left)") == \
        ("padding", " 0 env(safe-area-inset-right) 0 env(safe-area-inset-left)")
    # a variable's name isn't a keyword
    assert f("float", " var(--side-left)") == ("float", " var(--side-left)")


def test_a_stylesheet_mirrors_and_keeps_its_selectors():
    css = (".ml-3 {\n  margin-left: 1rem !important; }\n\n@media (min-width: 992px) {\n  .text-lg-left {\n"
           "    text-align: left !important; } }\n.fa-quote-right:before {\n  content: \"\\f10e\"; }\n"
           "/* a note */\n.x { /* why */ padding-left: 2px; left: 0 }\n")
    out = flip(css)
    assert ".ml-3 {\n  margin-right: 1rem !important; }" in out
    assert ".text-lg-left {\n    text-align: right !important; } }" in out
    assert '.fa-quote-right:before {\n  content: "\\f10e"; }' in out
    assert "/* why */ padding-right: 2px; right: 0 }" in out


def test_ignore_directives():
    css = (".a { /*rtl:ignore*/ margin-left: 1px; margin-left: 2px; }\n/*rtl:ignore*/\n.b { left: 0; float: left; }\n"
           "/*rtl:begin:ignore*/\n.c { left: 0; }\n.d { right: 0; }\n/*rtl:end:ignore*/\n.e { left: 0; }")
    out = flip(css)
    assert "/*rtl:ignore*/ margin-left: 1px; margin-right: 2px;" in out
    assert ".b { left: 0; float: left; }" in out
    assert ".c { left: 0; }\n.d { right: 0; }" in out
    assert ".e { right: 0; }" in out


def test_the_sites_own_stylesheet_mirrors_completely():
    sass = pytest.importorskip("sass")
    root = ROOT / "ruqqus"
    raw = (root / "assets" / "style" / "main.scss").read_text(encoding="utf-8")
    scss = raw.replace("{boardcolor}", "805ad5").replace("{maincolor}", "805ad5").replace("{ruqquspath}", str(root))
    css = sass.compile(string=scss)
    out = flip(css)
    assert re.findall(r"\{[^{}]*", css).__len__() == re.findall(r"\{[^{}]*", out).__len__()   # nothing lost
    for side in ("margin", "padding"):
        assert css.count(f"{side}-left:") == out.count(f"{side}-right:"), side
        assert css.count(f"{side}-right:") == out.count(f"{side}-left:"), side
    assert out.count("{") == css.count("{") and out.count("}") == css.count("}")
    assert out.count(";") == css.count(";")                               # every declaration kept
    assert flip(out).replace(" ", "") == css.replace(" ", "")              # flipping twice gives the original
