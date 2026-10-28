"""A feedback screenshot made safe to keep (helpers/site_feedback.py stores it in `site_feedback_images`).

The page already redraws the picture in the browser (assets/js/site_feedback.js: smaller, no EXIF), but the server never
trusts that. `prepare` opens what arrived with Pillow, refuses anything that is not a real PNG, JPEG or WebP picture
(no SVG, no HTML, no GIF, nothing truncated, nothing with an absurd number of pixels), turns it upright, fits it into
MAX_EDGE, and encodes it afresh as WebP (JPEG where Pillow has no WebP) at the best quality that stays under
OUTPUT_MAX_BYTES. What is stored is always the new picture, never the upload, so camera data and anything hidden
in a file's tail are gone. Pillow is imported inside the function: the module loads where it isn't installed.
Tests: tests/test_feedback_image.py.
"""
import io

from ruqqus.helpers.i18n_runtime import tr

INPUT_MAX_BYTES = 3 * 1024 * 1024
OUTPUT_MAX_BYTES = 600 * 1024
MAX_EDGE = 1600
MAX_PIXELS = 40_000_000
FORMATS = ("PNG", "JPEG", "WEBP")
# (quality, scale) tried in turn until the picture fits
ATTEMPTS = ((82, 1.0), (68, 1.0), (52, 0.8), (40, 0.6))


def prepare(raw):
    """((mime, bytes), error): the new picture, or why this one can't be kept."""
    from PIL import Image, ImageOps, features

    if len(raw) > INPUT_MAX_BYTES:
        return None, tr("That picture is too big. The limit is %(size)s MB.", size=INPUT_MAX_BYTES // (1024 * 1024))
    try:
        probe = Image.open(io.BytesIO(raw))
        kind = probe.format
        width, height = probe.size
        probe.verify()
    except Exception:
        return None, tr("That file is not a picture.")
    if kind not in FORMATS:
        return None, tr("A screenshot must be a PNG, JPEG or WebP picture.")
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        return None, tr("That picture is too large.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
        image = ImageOps.exif_transpose(image)
    except Exception:
        return None, tr("That file is not a picture.")

    webp = features.check("webp")
    transparent = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    if webp:
        base = image.convert("RGBA" if transparent else "RGB")
    else:
        base = image.convert("RGBA")
        flat = Image.new("RGB", base.size, (255, 255, 255))
        flat.paste(base, mask=base.getchannel("A"))
        base = flat
    base.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

    for quality, scale in ATTEMPTS:
        work = base if scale == 1.0 else base.resize(
            (max(1, int(base.width * scale)), max(1, int(base.height * scale))), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        if webp:
            work.save(out, "WEBP", quality=quality, method=4)
        else:
            work.save(out, "JPEG", quality=quality, optimize=True)
        if out.tell() <= OUTPUT_MAX_BYTES:
            return ("image/webp" if webp else "image/jpeg", out.getvalue()), None
    return None, tr("That picture is too detailed to keep. Try a smaller part of the screen.")
