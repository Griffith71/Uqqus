import json

from sqlalchemy import *

from ruqqus.__main__ import Base


class TrendingTopic(Base):
    """One line of a trending list (helpers/trending.py finds them, scripts/compute_trending.py
    replaces every row each run). A list belongs to a scope ("all", "lang:fr", "region:AU")
    and a word-filter level, so reading one is a single indexed query."""

    __tablename__ = "trending_topics"

    id = Column(Integer, primary_key=True)
    scope = Column(String(48), nullable=False)
    filter_level = Column(SmallInteger, nullable=False, default=1)
    rank = Column(SmallInteger, nullable=False)
    # the topic itself ("world cup", "link:example.org/story") and its form in an address
    key = Column(String(320), nullable=False)
    slug = Column(String(64), nullable=False)
    kind = Column(String(8), nullable=False)
    label = Column(String(120), nullable=False)
    score = Column(Float, nullable=False, default=0)
    post_count = Column(Integer, nullable=False, default=0)
    author_count = Column(Integer, nullable=False, default=0)
    # JSON list of post ids, newest first (the topic's page reads it through the feed rules)
    post_ids = Column(Text, nullable=False, default="[]")
    computed_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<TrendingTopic(scope={self.scope}, level={self.filter_level}, rank={self.rank}, key={self.key})>"

    @property
    def permalink(self):
        return f"/trending/{self.slug}"

    @property
    def post_id_list(self):
        try:
            return [int(x) for x in json.loads(self.post_ids or "[]")]
        except (ValueError, TypeError):
            return []


class TrendingBlocked(Base):
    """A topic an admin hid from every trending list (/admin/trending)."""

    __tablename__ = "trending_blocked"

    id = Column(Integer, primary_key=True)
    key = Column(String(320), nullable=False, unique=True)
    label = Column(String(120), nullable=False, default="")
    admin_id = Column(Integer, default=None)
    created_utc = Column(Integer, nullable=False, default=0)
