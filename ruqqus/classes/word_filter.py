from sqlalchemy import *

from ruqqus.__main__ import Base


class WordFilterEntry(Base):
    """One row of the word filter's list (see helpers/wordfilter.py for how
    it is matched, helpers/word_filter_store.py for how it is loaded).

    severity 2: extreme - hidden for the Standard and Child filters
    severity 1: profanity - hidden for the Child filter
    severity 0: explicitly allowed; a multi-word entry is an allowed phrase

    Admins enter plain words, never regular expressions."""

    __tablename__ = "word_filter_entries"

    id = Column(Integer, primary_key=True)
    word = Column(String(64), nullable=False)
    severity = Column(SmallInteger, nullable=False, default=1)
    # "word": the whole token must be the word; "anywhere": also inside longer tokens
    mode = Column(String(16), nullable=False, default="word")
    # comma separated other spellings
    variants = Column(String(512), default="")
    # comma separated endings; NULL means the engine's default endings
    suffixes = Column(String(512), default=None)
    enabled = Column(Boolean, nullable=False, default=True)
    note = Column(String(256), default="")
    created_utc = Column(Integer, default=0)

    def __repr__(self):
        return f"<WordFilterEntry(id={self.id}, severity={self.severity})>"

    @staticmethod
    def _split(value):
        return [x.strip() for x in (value or "").split(",") if x.strip()]

    @property
    def as_engine_entry(self):
        return {
            "word": self.word,
            "severity": self.severity,
            "mode": self.mode or "word",
            "variants": self._split(self.variants),
            "suffixes": None if self.suffixes is None else self._split(self.suffixes),
        }
