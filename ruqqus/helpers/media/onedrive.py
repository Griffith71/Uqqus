"""OneDrive as a member's storage for pictures and audio (Microsoft Graph).

The file lives in the member's own OneDrive, in this site's app folder ("Apps/<app name>"),
and stays private there: Ruqqus fetches it with the member's permission and shows it at
/media/... . As with Google Drive, the storage is never the public address: a shared OneDrive
link shows its owner, and the Cloudflare CSAM scan only covers this site's own addresses.

Uploading: the server opens a Graph upload session and hands the browser only that session's
address. The address takes nothing but this one file (no Microsoft credential reaches a
browser). Graph wants each piece sent with a Content-Range header, at most 60 MiB per piece
and in multiples of 320 KiB, so the answer carries `chunk` and the browser sends the file in
pieces of that size. The file's name and folder are chosen here, so finishing finds the file
by path and does not depend on the browser being allowed to read Microsoft's answer.
"""
from urllib.parse import quote

from . import microsoft_oauth, rules
from .base import MediaGone, Provider, ProviderDown, Stream

GRAPH = "https://graph.microsoft.com/v1.0"
APPROOT = f"{GRAPH}/me/drive/special/approot"
SUBFOLDERS = {rules.IMAGE: "Images", rules.AUDIO: "Audio"}
META = "id,size,file,deleted"
CHUNK = 32 * 320 * 1024          # 10 MiB: a multiple of 320 KiB, well under Graph's 60 MiB a piece
READ_CHUNK = 64 * 1024
PATH_PREFIX = "path:"            # provider_ref while the upload is under way: where the file will be


def _site_name():
    try:
        from ruqqus.__main__ import app
        return app.config.get("SITE_NAME") or "Ruqqus"
    except Exception:
        return "Ruqqus"


def _json(response):
    try:
        return response.json() or {}
    except ValueError:
        return {}


def _checksum(meta):
    hashes = (meta.get("file") or {}).get("hashes") or {}
    # quickXorHash on every kind of OneDrive; sha1Hash only on personal ones
    return hashes.get("quickXorHash") or hashes.get("sha1Hash") or hashes.get("sha256Hash") or ""


class OneDriveProvider(Provider):
    name = "onedrive"
    label = "OneDrive"
    served = True

    def _api(self, account, method, url, **kwargs):
        return microsoft_oauth.SERVICE.api(account, method, url, **kwargs)

    # --- folders ---------------------------------------------------------------------

    def _folder(self, account, kind):
        """Make the kind's folder in the app folder once ("already there" is fine)."""
        made = dict(account.setting("folders") or {})
        if made.get(kind):
            return SUBFOLDERS[kind]
        r = self._api(account, "POST", f"{APPROOT}/children",
                      json={"name": SUBFOLDERS[kind], "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
        if r.status_code not in (200, 201, 409):
            if r.status_code == 507:
                raise rules.MediaError("Your OneDrive is full. Free some space and try again.", 507)
            raise ProviderDown(f"could not create a OneDrive folder ({r.status_code})")
        made[kind] = True
        account.set_setting("folders", made)
        return SUBFOLDERS[kind]

    # --- uploading -------------------------------------------------------------------

    def begin_upload(self, account, asset, origin, details=None):
        folder = self._folder(account, asset.kind)
        name = f"{_site_name().lower()}-{rules.b36(asset.id)}.{asset.ext}"
        path = f"{folder}/{name}"
        r = self._api(account, "POST", f"{APPROOT}:/{quote(path)}:/createUploadSession",
                      json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}})
        if r.status_code == 404:
            # the folder was deleted since: make it again once
            account.set_setting("folders", {})
            self._folder(account, asset.kind)
            r = self._api(account, "POST", f"{APPROOT}:/{quote(path)}:/createUploadSession",
                          json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": name}})
        url = _json(r).get("uploadUrl")
        if r.status_code != 200 or not url:
            if r.status_code == 507 or "quotaLimitReached" in getattr(r, "text", ""):
                raise rules.MediaError("Your OneDrive is full. Free some space and try again.", 507)
            raise ProviderDown(f"OneDrive did not start the upload ({r.status_code})")
        asset.provider_ref = PATH_PREFIX + path
        return {"url": url, "method": "PUT", "headers": {}, "chunk": CHUNK}

    def _meta_by_path(self, account, path):
        r = self._api(account, "GET", f"{APPROOT}:/{quote(path)}", params={"select": META})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderDown(f"OneDrive answered {r.status_code}")
        meta = _json(r)
        return None if meta.get("deleted") else meta

    def _meta(self, account, asset):
        r = self._api(account, "GET", f"{GRAPH}/me/drive/items/{asset.provider_ref}", params={"select": META})
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise ProviderDown(f"OneDrive answered {r.status_code}")
        meta = _json(r)
        return None if meta.get("deleted") else meta

    def finish_upload(self, account, asset):
        ref = asset.provider_ref or ""
        meta = self._meta_by_path(account, ref[len(PATH_PREFIX):]) if ref.startswith(PATH_PREFIX) else None
        checksum = _checksum(meta or {})
        if not meta or not meta.get("id") or "size" not in meta or not checksum:
            raise rules.MediaError("The upload did not arrive in your OneDrive. Try again.")
        return {"ref": meta["id"], "size": int(meta["size"]), "checksum": checksum}

    # --- showing ---------------------------------------------------------------------

    def checksum(self, account, asset):
        meta = self._meta(account, asset)
        if not meta:
            raise MediaGone()
        return _checksum(meta)

    def open(self, account, asset, byte_range=None):
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        # Graph answers with a redirect to a short-lived download address; requests follows it
        # and does not carry the Authorization header to the other host
        r = self._api(account, "GET", f"{GRAPH}/me/drive/items/{asset.provider_ref}/content",
                      headers=headers, stream=True)
        if r.status_code in (403, 404, 410):
            raise MediaGone()
        if r.status_code not in (200, 206):
            raise ProviderDown(f"OneDrive answered {r.status_code}")
        if byte_range and r.status_code == 206:
            size = byte_range[1] - byte_range[0] + 1
        else:
            byte_range = None
            size = int(r.headers.get("Content-Length") or asset.size)
        return Stream(r.iter_content(READ_CHUNK), size=size, total=int(asset.size), byte_range=byte_range)

    def space_left(self, account):
        """Bytes free in the member's OneDrive, or None when OneDrive does not say."""
        r = self._api(account, "GET", f"{GRAPH}/me/drive", params={"select": "quota"})
        remaining = (_json(r).get("quota") or {}).get("remaining") if r.status_code == 200 else None
        return int(remaining) if isinstance(remaining, (int, float)) else None

    def revoke(self, account):
        # Microsoft has no address for giving a refresh token back: the member removes the app
        # at account.live.com/consent/Manage. Here it is forgotten.
        microsoft_oauth.SERVICE.revoke(account)
