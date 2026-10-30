"""The colour a QR code is drawn in (the two-factor set-up code): the site's colour, darkened until a phone's camera
reads it from a white page. Pure, stdlib only."""


def _luminance(rgb):
    lin = [c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _rgb(colour):
    h = str(colour).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        if len(h) != 6:
            raise ValueError(h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def on_white(colour, minimum=4.5):
    """`colour` (hex, with or without #) as "#rrggbb", darkened in small steps until it holds `minimum`:1 against
    white. A colour that can't be read as one is black."""
    rgb = _rgb(colour)
    while (1.05) / (_luminance(rgb) + 0.05) < minimum and max(rgb) > 0:
        rgb = tuple(int(c * 0.96) for c in rgb)
    return "#%02x%02x%02x" % rgb
