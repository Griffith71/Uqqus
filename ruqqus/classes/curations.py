from sqlalchemy import *
from sqlalchemy.orm import relationship

from .mix_ins import *
from ruqqus.__main__ import Base


class Curation(Base, Stndrd, Age_times):

    __tablename__ = "curations"

    id = Column(BigInteger, primary_key=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"))
    name = Column(String(100))
    # URL-safe, globally unique, auto-generated once from `name` at creation
    # time (see _generate_curation_slug() in routes/curations.py) and never
    # changed afterward - the actual identifier for /&<slug> and &mentions.
    # `name` stays free text with no uniqueness requirement, shown as the
    # bold display title; `slug` is the small grey handle underneath.
    slug = Column(String(25), unique=True)
    description = Column(String(500), default="")
    is_private = Column(Boolean, default=True)
    created_utc = Column(BigInteger, default=0)
    # Lineage only - set once at fork time, never a live link back to the
    # original. A fork is an independent copy from that point forward.
    forked_from_id = Column(BigInteger, ForeignKey("curations.id"), default=None)

    # Optional Regional/Language/Categorical filters, intrinsic to the
    # curation itself (not the viewer's personal session filters - anyone
    # who views or follows this curation sees the same filtered result).
    # Comma-separated, same convention as User.custom_filter_list - no
    # array-column precedent exists elsewhere in this schema.
    region_filter = Column(String(500), default="")
    language_filter = Column(String(500), default="")
    category_filter = Column(String(500), default="")

    owner = relationship("User", primaryjoin="User.id==Curation.owner_id")

    @property
    def permalink(self):
        return f"/&{self.slug}"

    @property
    def region_filter_list(self):
        return [x for x in self.region_filter.split(',') if x] if self.region_filter else []

    @property
    def language_filter_list(self):
        return [x for x in self.language_filter.split(',') if x] if self.language_filter else []

    @property
    def category_filter_list(self):
        return [int(x) for x in self.category_filter.split(',') if x] if self.category_filter else []


class CurationGuild(Base):

    __tablename__ = "curation_guilds"

    id = Column(BigInteger, primary_key=True)
    curation_id = Column(BigInteger, ForeignKey("curations.id"))
    board_id = Column(BigInteger, ForeignKey("boards.id"))
    created_utc = Column(BigInteger, default=0)

    __table_args__ = (
        UniqueConstraint("curation_id", "board_id", name="uq_curation_guilds_curation_board"),
    )


class CurationUser(Base):

    __tablename__ = "curation_users"

    id = Column(BigInteger, primary_key=True)
    curation_id = Column(BigInteger, ForeignKey("curations.id"))
    target_user_id = Column(BigInteger, ForeignKey("users.id"))
    created_utc = Column(BigInteger, default=0)

    __table_args__ = (
        UniqueConstraint("curation_id", "target_user_id", name="uq_curation_users_curation_user"),
    )


class CurationFollow(Base):

    __tablename__ = "curation_follows"

    id = Column(BigInteger, primary_key=True)
    curation_id = Column(BigInteger, ForeignKey("curations.id"))
    user_id = Column(BigInteger, ForeignKey("users.id"))
    created_utc = Column(BigInteger, default=0)

    __table_args__ = (
        UniqueConstraint("curation_id", "user_id", name="uq_curation_follows_curation_user"),
    )
