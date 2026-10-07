import json

from sqlalchemy import *

from ruqqus.__main__ import Base
from ruqqus.helpers.media import rules


class MediaAccount(Base):
    """An account on a hosting site that a member linked so their uploads go there
    (helpers/media). One per member and provider. The refresh token is the member's
    permission; it is stored encrypted (helpers/secret_box.py) and never leaves the server."""

    __tablename__ = "media_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String(16), nullable=False)
    # the provider's own id for the account, to tell a reconnect from a different account
    external_id = Column(String(128), nullable=False, default="")
    scopes = Column(Text, nullable=False, default="")
    refresh_token_encrypted = Column(Text, nullable=False, default="")
    # active | revoked | error
    status = Column(String(12), nullable=False, default="active")
    # JSON object: folder ids, the member's defaults (e.g. YouTube visibility)
    settings = Column(Text, nullable=False, default="{}")
    created_utc = Column(Integer, nullable=False, default=0)
    updated_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<MediaAccount(id={self.id}, user_id={self.user_id}, provider={self.provider}, status={self.status})>"

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def setting_dict(self):
        try:
            return dict(json.loads(self.settings or "{}"))
        except ValueError:
            return {}

    def setting(self, name, default=None):
        return self.setting_dict.get(name, default)

    def set_setting(self, name, value):
        data = self.setting_dict
        data[name] = value
        self.settings = json.dumps(data)


class MediaAsset(Base):
    """One uploaded file. The bytes are in the member's linked account, never here:
    this row is the reference a post or comment points at (helpers/media/rules.py)."""

    __tablename__ = "media_assets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("media_accounts.id"), nullable=False)
    provider = Column(String(16), nullable=False)
    kind = Column(String(8), nullable=False)
    # the file's id at the provider (a Drive file id, a YouTube video id)
    provider_ref = Column(String(255), nullable=False, default="")
    # random, part of the address, so an address cannot be guessed from an id
    token = Column(String(64), nullable=False)
    ext = Column(String(8), nullable=False, default="")
    size = Column(BigInteger, nullable=False, default=0)
    # the provider's checksum when the file was checked; a different one later means it was swapped
    checksum = Column(String(128), nullable=False, default="")
    width = Column(Integer, default=None)
    height = Column(Integer, default=None)
    status = Column(String(12), nullable=False, default="pending")
    # the post or comment it is part of; an asset nobody attached is shown only to its owner
    submission_id = Column(Integer, default=None)
    comment_id = Column(Integer, default=None)
    created_utc = Column(Integer, nullable=False, default=0)
    updated_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<MediaAsset(id={self.id}, kind={self.kind}, provider={self.provider}, status={self.status})>"

    @property
    def path(self):
        """The site-relative address it is served at (served providers)."""
        return rules.media_path(self.id, self.token, self.ext)

    @property
    def json(self):
        data = {
            "id": rules.b36(self.id),
            "kind": self.kind,
            "status": self.status,
            "size": self.size,
        }
        if self.provider in rules.SERVED and self.ext:
            data["path"] = self.path
            if self.kind == rules.IMAGE:
                data["markdown"] = f"![]({self.path})"
                data["width"], data["height"] = self.width, self.height
        return data
