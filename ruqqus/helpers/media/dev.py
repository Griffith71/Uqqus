"""A stand-in provider for development and tests.

It behaves like a member's storage account but keeps the bytes in a temp folder on
this machine, so the whole flow (enable, upload from the browser, attach, show, delete)
can be run without a Google project. It only exists when MEDIA_DEV_PROVIDER=1; never
set that on a real site, where Ruqqus must not hold uploads.
"""
import hashlib
import os
import tempfile

from . import rules
from .base import MediaGone, Provider, Stream

CHUNK = 64 * 1024


def enabled():
    return os.environ.get("MEDIA_DEV_PROVIDER", "").strip() == "1"


def folder():
    # MEDIA_DEV_DIR lets a dev setup keep the files across restarts (docker-compose.yml mounts a volume there)
    path = os.environ.get("MEDIA_DEV_DIR", "").strip() or os.path.join(tempfile.gettempdir(), "ruqqus_media_dev")
    os.makedirs(path, exist_ok=True)
    return path


def file_path(asset):
    # ids and tokens are ours (digits and url-safe characters): nothing a member typed
    return os.path.join(folder(), f"{int(asset.id)}-{asset.token}")


class DevProvider(Provider):
    name = "dev"
    served = True

    def begin_upload(self, account, asset, origin):
        asset.provider_ref = f"{int(asset.id)}-{asset.token}"
        return {"url": f"/api/media/dev_upload/{rules.b36(asset.id)}/{asset.token}", "method": "PUT", "headers": {}}

    def finish_upload(self, account, asset):
        path = file_path(asset)
        if not os.path.isfile(path):
            raise rules.MediaError("The upload did not arrive. Try again.")
        return {"ref": asset.provider_ref, "size": os.path.getsize(path), "checksum": self.checksum(account, asset)}

    def checksum(self, account, asset):
        path = file_path(asset)
        if not os.path.isfile(path):
            raise MediaGone()
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                digest.update(block)
        return digest.hexdigest()

    def open(self, account, asset, byte_range=None):
        path = file_path(asset)
        if not os.path.isfile(path):
            raise MediaGone()
        total = os.path.getsize(path)
        start, end = byte_range if byte_range else (0, total - 1)

        def chunks():
            left = end - start + 1
            with open(path, "rb") as f:
                f.seek(start)
                while left > 0:
                    block = f.read(min(CHUNK, left))
                    if not block:
                        return
                    left -= len(block)
                    yield block

        return Stream(chunks(), size=max(end - start + 1, 0), total=total, byte_range=byte_range)

    def revoke(self, account):
        return None
