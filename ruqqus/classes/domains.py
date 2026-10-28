from sqlalchemy import *
from ruqqus.helpers.i18n_runtime import N_
from ruqqus.__main__ import Base, cache

# why a site is banned: English, drawn with msg() where a member is told (routes/posts.py, routes/comments.py)
reasons = {
    1: N_("Deceptive URL shorteners are not allowed."),
    3: N_("Piracy is not allowed."),
    4: N_("Sites hosting digitally malicious content are not allowed."),
    # Translators: why a site is banned from being linked
    5: N_("Spam"),
    6: N_("Doxxing is not allowed."),
    7: N_("Sexualizing minors is strictly prohibited."),
    8: N_('User safety - This site is a Ruqqus clone which is still displaying "Ruqqus" as its site name.'),
    9: N_("You may not use Ruqqus to engage in or plan unlawful activity.")
}


class Domain(Base):

    __tablename__ = "domains"
    id = Column(Integer, primary_key=True)
    domain = Column(String)
    can_submit = Column(Boolean, default=True)
    can_comment = Column(Boolean, default=True)
    reason = Column(Integer, default=0)
    show_thumbnail = Column(Boolean, default=False)
    embed_function = Column(String(64), default=None)
    embed_template = Column(String(32), default=None)

    @property
    def reason_text(self):
        return reasons.get(self.reason)

    @property
    def permalink(self):
        return f"/admin/domain/{self.domain}"
    


class BadLink(Base):

    __tablename__ = "badlinks"
    id = Column(Integer, primary_key=True)
    reason = Column(Integer)
    link = Column(String(512))
    autoban = Column(Boolean, default=False)

    @property
    def reason_text(self):
        return reasons.get(self.reason)
