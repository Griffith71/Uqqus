import json

from sqlalchemy import *

from ruqqus.__main__ import Base


class PostDraft(Base):
    """A post saved from the composer (Save draft) and optionally scheduled to
    be published later. Never a row in `submissions`: a draft must not show in
    feeds, counts or search. See helpers/post_drafts.py for the rules and
    scripts/publish_scheduled.py for the publisher."""

    __tablename__ = "post_drafts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(300), nullable=False, default="")
    url = Column(String(2048), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    # JSON list of the guild names to forward to
    forward_guilds = Column(Text, nullable=False, default="[]")
    # JSON object: comment_permission, paid_partnership, made_with_ai, sensitive
    options = Column(Text, nullable=False, default="{}")
    status = Column(String(12), nullable=False, default="draft")
    publish_utc = Column(Integer, default=None)
    attempts = Column(SmallInteger, nullable=False, default=0)
    claimed_utc = Column(Integer, default=None)
    error = Column(String(512), nullable=False, default="")
    published_post_id = Column(Integer, default=None)
    creation_ip = Column(String(64), nullable=False, default="")
    creation_region = Column(String(2), default=None)
    created_utc = Column(Integer, nullable=False, default=0)
    updated_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<PostDraft(id={self.id}, user_id={self.user_id}, status={self.status})>"

    @property
    def forward_guild_list(self):
        try:
            return list(json.loads(self.forward_guilds or "[]"))
        except ValueError:
            return []

    @property
    def option_dict(self):
        try:
            return dict(json.loads(self.options or "{}"))
        except ValueError:
            return {}

    def option(self, name, default=None):
        return self.option_dict.get(name, default)

    def set_fields(self, fields):
        """Store validated fields (helpers/post_drafts.clean_fields)."""
        self.title = fields["title"]
        self.url = fields["url"]
        self.body = fields["body"]
        self.forward_guilds = json.dumps(fields["forward_guilds"])
        self.options = json.dumps(fields["options"])

    @property
    def fields(self):
        """The saved fields in the shape clean_fields returns."""
        return {
            "title": self.title,
            "url": self.url,
            "body": self.body,
            "forward_guilds": self.forward_guild_list,
            "options": self.option_dict,
        }

    @property
    def json(self):
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "publish_utc": self.publish_utc,
            "updated_utc": self.updated_utc,
            "error": self.error,
        }
