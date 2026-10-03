from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base


class Region(Base):

    __tablename__ = "regions"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True)
    default_name = Column(String(64))
    current_name = Column(String(64))
    color = Column(String(7))

    proposals = relationship("RegionNameProposal", lazy="dynamic", backref="region", overlaps="region")


class RegionNameProposal(Base):

    __tablename__ = "region_name_proposals"

    id = Column(Integer, primary_key=True)
    region_id = Column(Integer, ForeignKey("regions.id"))
    proposed_name = Column(String(64))
    created_by_id = Column(Integer, ForeignKey("users.id"))
    created_utc = Column(Integer, default=0)
    vote_count = Column(Integer, default=0)

    created_by = relationship("User", lazy="joined", overlaps="created_by")


class RegionNameVote(Base):

    __tablename__ = "region_name_votes"

    id = Column(Integer, primary_key=True)
    region_id = Column(Integer, ForeignKey("regions.id"))
    proposal_id = Column(Integer, ForeignKey("region_name_proposals.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    created_utc = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("region_id", "user_id", name="uq_region_name_votes_region_user"),
    )
