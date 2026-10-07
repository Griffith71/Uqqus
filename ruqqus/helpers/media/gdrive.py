"""Google Drive as a member's storage for pictures and audio.

The file lives in the member's own Drive, in a folder this site makes, and stays private
there: Ruqqus fetches it with the member's permission and shows it at /media/... . Drive
is the storage, never the public address, because Google offers no supported way to show
a Drive file in an <img> tag, a shared Drive file shows its owner's Google name, and the
Drive API terms do not allow using Drive in place of a CDN.

With the drive.file scope the site only ever sees files it created itself.

Uploading: the server asks Drive for an upload session and hands the browser only that
session's address (it can do nothing but receive this one file), so no Google credential
ever reaches a browser. The file's id is chosen before the upload, so finishing does not
depend on the browser being allowed to read Google's answer.
"""
from . import google_oauth, rules
from .base import MediaGone, Provider, ProviderDown, Stream

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
FOLDER = "application/vnd.google-apps.folder"
SUBFOLDERS = {rules.IMAGE: "Images", rules.AUDIO: "Audio"}
META = "id,size,sha256Checksum,md5Checksum,trashed"
CHUNK = 64 * 1024


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


class DriveProvider(Provider):
    name = "gdrive"
    served = True

    # --- folders ---------------------------------------------------------------------

    def _make_folder(self, account, name, parent=None):
        body = {"name": name, "mimeType": FOLDER}
        if parent:
            body["parents"] = [parent]
        r = google_oauth.api(account, "POST", f"{API}/files", params={"fields": "id"}, json=body)
        folder_id = _json(r).get("id")
        if r.status_code not in (200, 201) or not folder_id:
            raise ProviderDown(f"could not create a Drive folder ({r.status_code})")
        return folder_id

    def _folder(self, account, kind, fresh=False):
        """The id of the member's folder for this kind, made on first use. The ids are
        kept on the account; `fresh` forgets them (the member deleted the folder)."""
        folders = {} if fresh else dict(account.setting("folders") or {})
        if "root" not in folders:
            folders = {"root": self._make_folder(account, _site_name())}
        if kind not in folders:
            folders[kind] = self._make_folder(account, SUBFOLDERS[kind], folders["root"])
        account.set_setting("folders", folders)
        return folders[kind]

    # --- uploading -------------------------------------------------------------------

    def begin_upload(self, account, asset, origin):
        r = google_oauth.api(account, "GET", f"{API}/files/generateIds", params={"count": 1, "space": "drive", "type": "files"})
        ids = _json(r).get("ids") or []
        if r.status_code != 200 or not ids:
            raise ProviderDown(f"could not reserve a Drive file id ({r.status_code})")
        file_id = ids[0]
        name = f"{_site_name().lower()}-{rules.b36(asset.id)}.{asset.ext}"

        session = None
        for fresh in (False, True):
            folder = self._folder(account, asset.kind, fresh=fresh)
            session = google_oauth.api(account, "POST", UPLOAD, headers={
                "X-Upload-Content-Type": rules.content_type(asset.ext),
                "X-Upload-Content-Length": str(int(asset.size)),
                "Content-Type": "application/json; charset=UTF-8",
                "Origin": origin,          # the browser that will send the bytes comes from here
            }, json={"id": file_id, "name": name, "parents": [folder]})
            if session.status_code != 404:      # 404: the folder is gone, make it again once
                break
        location = session.headers.get("Location") if session is not None else None
        if session is None or session.status_code != 200 or not location:
            if session is not None and session.status_code == 403 and "storageQuota" in session.text:
                raise rules.MediaError("Your Google Drive is full. Free some space and try again.", 507)
            raise ProviderDown(f"Drive did not start the upload ({getattr(session, 'status_code', '?')})")

        asset.provider_ref = file_id
        return {"url": location, "method": "PUT", "headers": {}}

    def _meta(self, account, asset):
        r = google_oauth.api(account, "GET", f"{API}/files/{asset.provider_ref}", params={"fields": META})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ProviderDown(f"Drive answered {r.status_code}")
        meta = _json(r)
        return None if meta.get("trashed") else meta

    def finish_upload(self, account, asset):
        meta = self._meta(account, asset) if asset.provider_ref else None
        checksum = (meta or {}).get("sha256Checksum") or (meta or {}).get("md5Checksum")
        if not meta or not checksum or "size" not in meta:
            raise rules.MediaError("The upload did not arrive in your Google Drive. Try again.")
        return {"ref": meta["id"], "size": int(meta["size"]), "checksum": checksum}

    # --- showing ---------------------------------------------------------------------

    def checksum(self, account, asset):
        meta = self._meta(account, asset)
        if not meta:
            raise MediaGone()
        return meta.get("sha256Checksum") or meta.get("md5Checksum") or ""

    def open(self, account, asset, byte_range=None):
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        r = google_oauth.api(account, "GET", f"{API}/files/{asset.provider_ref}", params={"alt": "media"},
                             headers=headers, stream=True)
        if r.status_code in (403, 404, 410):
            raise MediaGone()          # deleted, trashed for good, or flagged by Google
        if r.status_code not in (200, 206):
            raise ProviderDown(f"Drive answered {r.status_code}")
        if byte_range and r.status_code == 206:
            size = byte_range[1] - byte_range[0] + 1
        else:
            byte_range = None
            size = int(r.headers.get("Content-Length") or asset.size)
        return Stream(r.iter_content(CHUNK), size=size, total=int(asset.size), byte_range=byte_range)

    def revoke(self, account):
        if account.refresh_token_encrypted:
            from ruqqus.helpers.secret_box import decrypt_secret
            google_oauth.revoke(decrypt_secret(account.refresh_token_encrypted))
        google_oauth.forget(account)
