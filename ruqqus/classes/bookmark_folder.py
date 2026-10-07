from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class BookmarkFolder(Base, Age_times):
    """One of a member's folders for sorting their bookmarks (helpers/bookmark_folders.py).

    A bookmark (`SaveRelationship` for a post, `CommentSaveRelationship` for a comment) points at
    one folder through `folder_id`, or at none (unsorted). Deleting a folder sets those back to
    none; it never deletes a bookmark. A folder is private to its owner. The name is unique per
    member ignoring case: a functional index in the database (`bookmark_folders_name_key`)."""

    __tablename__ = "bookmark_folders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(40), nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<BookmarkFolder(id={self.id}, user_id={self.user_id}, name={self.name!r})>"
