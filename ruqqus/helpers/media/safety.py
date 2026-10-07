"""Checks run on an upload before it may be shown.

The file is in the member's storage, so the browser sent it there without the server
seeing it. To check it the server fetches it once into a temp file, looks, and deletes
the temp file: nothing is kept.
"""
import os
import tempfile

from . import rules


def fetch_to_temp(provider, account, asset, limit):
    """The asset's bytes in a temp file (the caller deletes it). MediaError if it is
    bigger than `limit`, whatever size was claimed."""
    handle, path = tempfile.mkstemp(prefix="ruqqus_media_check_")
    written = 0
    try:
        with os.fdopen(handle, "wb") as out:
            for block in provider.open(account, asset).chunks:
                written += len(block)
                if written > limit:
                    raise rules.MediaError(f"That file is too big. The limit is {limit // rules.MIB} MB.", 413)
                out.write(block)
    except BaseException:
        discard(path)
        raise
    return path


def discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def image_size(path):
    """(width, height) of a real image, or MediaError. Guards against a small file that
    unpacks to an enormous picture."""
    from PIL import Image

    try:
        with Image.open(path) as probe:
            probe.verify()                      # the whole file parses as the image it claims to be
        with Image.open(path) as image:
            width, height = image.size
    except rules.MediaError:
        raise
    except Exception:
        raise rules.MediaError("That image could not be read. It may be damaged.")
    if width <= 0 or height <= 0 or width * height > rules.IMAGE_PIXELS_MAX:
        raise rules.MediaError("That image is too large in pixels.")
    return width, height
