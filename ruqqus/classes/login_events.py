from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base


class LoginEvent(Base):

    __tablename__ = "login_events"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    created_utc = Column(Integer, default=0)
    ip = Column(String(255), default=None)
    cf_country = Column(String(2), default=None)
    region_code = Column(String(32), default=None)

    user = relationship("User", lazy="joined", overlaps="user")
