"""The two-factor QR code is drawn in the site's colour, darkened to be readable on its white page
(helpers/qr_colour.py): VS Code's #0085d0 is 4.0:1 on white, a little pale for a camera."""
from ruqqus.helpers import qr_colour
from ruqqus.helpers.qr_colour import _luminance, _rgb, on_white


def ratio(colour):
    return 1.05 / (_luminance(_rgb(colour)) + 0.05)


def test_a_pale_colour_is_darkened_until_it_reads_on_white():
    out = on_white("0085d0")
    assert out != "#0085d0" and ratio(out) >= 4.5
    # still blue: the hue is kept
    r, g, b = _rgb(out)
    assert b > g > r
    assert ratio(on_white("23a9f2")) >= 4.5


def test_a_dark_enough_colour_is_left_alone():
    assert on_white("0c73a7") == "#0c73a7"
    assert on_white("#000000") == "#000000"


def test_short_hex_and_a_hash_are_read():
    assert on_white("#000") == "#000000"
    assert on_white("fff") != "#ffffff" and ratio(on_white("fff")) >= 4.5


def test_junk_is_black():
    assert on_white("") == "#000000" and on_white("zzzzzz") == "#000000" and on_white("12345") == "#000000"


def test_the_route_draws_it_this_way():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "ruqqus" / "routes" / "users.py").read_text(encoding="utf-8")
    assert 'qr_colour.on_white(app.config["SITE_COLOR"])' in src
    assert 'fill_color="#"+app.config["SITE_COLOR"]' not in src
    assert qr_colour
