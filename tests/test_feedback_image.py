"""A feedback screenshot made safe to keep (ruqqus/helpers/feedback_image.py): only real PNG, JPEG and WebP pictures,
shrunk and encoded afresh, with nothing of the upload (camera data, a file's tail) carried over."""
import io
import os

import pytest

Image = pytest.importorskip("PIL.Image")

from ruqqus.helpers import feedback_image as fi  # noqa: E402


def picture(size=(300, 200), fmt="PNG", mode="RGB", **save):
    image = Image.new(mode, size, (200, 30, 30) if mode in ("RGB", "RGBA") else 128)
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def reopened(data):
    return Image.open(io.BytesIO(data))


def test_a_png_a_jpeg_and_a_webp_come_back_as_a_new_picture():
    for fmt in ("PNG", "JPEG", "WEBP"):
        result, error = fi.prepare(picture(fmt=fmt))
        assert error is None, fmt
        mime, data = result
        assert mime in ("image/webp", "image/jpeg") and reopened(data).size == (300, 200)
        assert len(data) <= fi.OUTPUT_MAX_BYTES


def test_a_large_picture_is_fitted_into_the_longest_edge():
    result, _ = fi.prepare(picture(size=(3200, 1200)))
    assert reopened(result[1]).size == (fi.MAX_EDGE, 600)
    result, _ = fi.prepare(picture(size=(900, 4000)))
    assert reopened(result[1]).size == (360, fi.MAX_EDGE)


def test_exif_and_other_metadata_do_not_survive():
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}      # a GPS block
    data = picture(fmt="JPEG", exif=exif.tobytes())
    assert b"SecretCameraMaker" in data                                           # it was in the upload...
    result, error = fi.prepare(data)
    assert error is None
    assert b"SecretCameraMaker" not in result[1] and b"Exif" not in result[1]      # ...and is not in what is kept
    assert len(reopened(result[1]).getexif()) == 0


def test_a_file_tail_hidden_after_the_picture_is_not_kept():
    result, error = fi.prepare(picture() + b"<script>alert(1)</script>PRIVATE-NOTES")
    assert error is None and b"PRIVATE-NOTES" not in result[1] and b"<script>" not in result[1]


def test_sideways_pictures_are_turned_upright():
    exif = Image.Exif()
    exif[0x0112] = 6                                                              # rotate 90 degrees to view
    result, _ = fi.prepare(picture(size=(300, 200), fmt="JPEG", exif=exif.tobytes()))
    assert reopened(result[1]).size == (200, 300)


def test_transparency_is_kept_where_the_encoder_can():
    result, error = fi.prepare(picture(mode="RGBA"))
    assert error is None and reopened(result[1]).mode in ("RGBA", "RGB")


def test_what_is_not_a_picture_is_refused():
    for junk in (b"", b"hello", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                 b"<html><body>hi</body></html>", b"%PDF-1.7 ...", picture()[:60], os.urandom(500)):
        result, error = fi.prepare(junk)
        assert result is None and error, junk[:20]


def test_a_gif_is_not_a_screenshot():
    result, error = fi.prepare(picture(fmt="GIF", mode="P"))
    assert result is None and "PNG, JPEG or WebP" in error


def test_too_big_an_upload_or_too_many_pixels_is_refused():
    result, error = fi.prepare(b"\x89PNG\r\n\x1a\n" + b"0" * (fi.INPUT_MAX_BYTES + 1))
    assert result is None and "too big" in error
    flat = Image.new("1", (7000, 6000))                                           # 42 million pixels, a few KB
    out = io.BytesIO()
    flat.save(out, "PNG")
    assert len(out.getvalue()) < fi.INPUT_MAX_BYTES
    result, error = fi.prepare(out.getvalue())
    assert result is None and "too large" in error


def test_noise_that_cannot_be_squeezed_under_the_limit_is_refused_or_fits():
    noise = Image.frombytes("RGB", (1000, 700), os.urandom(1000 * 700 * 3))        # about 2 MB as PNG, the worst case
    out = io.BytesIO()
    noise.save(out, "PNG")
    assert len(out.getvalue()) < fi.INPUT_MAX_BYTES
    result, error = fi.prepare(out.getvalue())
    assert (result is None) != (error is None)
    if result:
        assert len(result[1]) <= fi.OUTPUT_MAX_BYTES
