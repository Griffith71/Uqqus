from sqlalchemy import *

from .mix_ins import *
from ruqqus.__main__ import Base


class Circle(Base, Age_times):
    """An exclusive audience (helpers/circles.py): an account's Circle, or a Circle guild's. Exactly one of
    `user_id` / `board_id` is set. `price_coins` is what a subscriber pays per 30 days (0: nobody can subscribe,
    the Circle is close friends only)."""

    __tablename__ = "circles"
    __table_args__ = (
        CheckConstraint("(user_id IS NOT NULL) <> (board_id IS NOT NULL)", name="circles_one_owner"),
        CheckConstraint("price_coins >= 0 AND price_coins <= 100", name="circles_price_range"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    board_id = Column(Integer, ForeignKey("boards.id"), nullable=True)
    price_coins = Column(Integer, nullable=False, default=0)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<Circle(id={self.id}, user_id={self.user_id}, board_id={self.board_id})>"


class CircleMember(Base, Age_times):
    """Someone in a Circle: a close friend the owner added (free), or a subscriber (paid, renewed every 30 days
    while `renews_utc` is ahead). `status` is active or ended; an ended row is kept for the owner's history."""

    __tablename__ = "circle_members"
    __table_args__ = (UniqueConstraint("circle_id", "user_id", name="circle_members_pair_key"),)

    id = Column(Integer, primary_key=True)
    circle_id = Column(Integer, ForeignKey("circles.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tier = Column(String(12), nullable=False)
    status = Column(String(12), nullable=False, default="active")
    started_utc = Column(Integer, nullable=False, default=0)
    renews_utc = Column(Integer, nullable=False, default=0)
    cancelled = Column(Boolean, nullable=False, default=False)
    # what this subscriber pays every 30 days: the price when they subscribed (0 for a close friend), so the
    # owner raising the price never charges an existing subscriber more
    price_coins = Column(Integer, nullable=False, default=0)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<CircleMember(circle_id={self.circle_id}, user_id={self.user_id}, tier={self.tier})>"


class CirclePayment(Base, Age_times):
    """One coin payment into a Circle (a first subscription or a renewal): the owner's record of what came in."""

    __tablename__ = "circle_payments"

    id = Column(Integer, primary_key=True)
    circle_id = Column(Integer, ForeignKey("circles.id", ondelete="CASCADE"), nullable=False)
    payer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coins = Column(Integer, nullable=False)
    kind = Column(String(12), nullable=False)
    created_utc = Column(Integer, nullable=False, default=0)

    def __repr__(self):
        return f"<CirclePayment(id={self.id}, circle_id={self.circle_id}, coins={self.coins})>"
