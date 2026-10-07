"""What a media provider is.

A provider puts a member's upload into the member's own linked account and gets it back.
To add one: write a module here with a class that fills in this interface, give it a
`name`, list it in `registry.PROVIDERS`, and map its kinds in `rules.PROVIDERS`. Nothing
about posts, comments or the upload page changes.

Two shapes exist:

* **served** providers (Google Drive): the file is plain storage. Ruqqus fetches it with
  `open()` and serves it at /media/...; `checksum()` proves it is still the file that
  was checked.
* **hosting** providers (YouTube): the site shows the file itself. `link()` is the
  address the post links to; `open()` is never called.

The browser uploads straight to the provider: `begin_upload` returns where to send the
bytes. It must never return a credential the browser could reuse for anything else.
"""


class MediaGone(Exception):
    """The provider no longer has the file, or the account no longer lets us read it."""


class AccountLost(MediaGone):
    """The member took the site's access away at the provider (or it expired). Nothing in
    the account can be reached until they connect it again."""


class ProviderDown(Exception):
    """The provider did not answer properly. Temporary: nothing is concluded about the file."""


class Stream:
    """Bytes coming back from a provider: `chunks` yields them; `size` is the length of
    what is being sent (the range's, when there is one)."""

    def __init__(self, chunks, size, total=None, byte_range=None):
        self.chunks = chunks
        self.size = size
        self.total = total if total is not None else size
        self.byte_range = byte_range


class Provider:
    name = ""
    served = False

    def begin_upload(self, account, asset, origin, details=None):
        """Start an upload of `asset` (kind, ext, size are set) into `account`.
        Returns {"url": ..., "method": "PUT", "headers": {...}} for the browser.
        `origin` is the site's origin (scheme://host), which the browser will send.
        `details` is what a hosting site wants to know (title, visibility, site name)."""
        raise NotImplementedError

    def finish_upload(self, account, asset):
        """The browser says it is done. Confirm with the provider and return
        {"ref": provider id, "size": bytes, "checksum": str}. A hosting provider may add
        "restricted": True when the site will not show the file publicly. Raise MediaError
        if the upload is not there or not complete."""
        raise NotImplementedError

    def open(self, account, asset, byte_range=None):
        """A Stream of the file's bytes (served providers). Raise MediaGone."""
        raise NotImplementedError

    def checksum(self, account, asset):
        """The provider's current checksum of the file (served providers)."""
        raise NotImplementedError

    def link(self, asset):
        """The public address of the file on the hosting site (hosting providers)."""
        raise NotImplementedError

    def revoke(self, account):
        """The member disconnected: give the access back at the provider."""
