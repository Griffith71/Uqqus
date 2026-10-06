from sqlalchemy import *

from ruqqus.__main__ import Base


class PostTemplate(Base):
    """A snippet of post text an author saved to insert again later (the
    Template button in the post editor). Private to its owner; at most
    helpers/post_fields.TEMPLATE_LIMIT per user."""

    __tablename__ = "post_templates"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(60), nullable=False)
    body = Column(Text, nullable=False)
    created_utc = Column(Integer, default=0)

    def __repr__(self):
        return f"<PostTemplate(id={self.id}, user_id={self.user_id})>"

    @property
    def json(self):
        return {"id": self.id, "name": self.name, "body": self.body}
