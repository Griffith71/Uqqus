"""Guard the site vocabulary (see "Terminology" in CLAUDE.md):

* anything that is a reply is a COMMENT - never reply/replies/replied/replying
* every post is first posted on your own profile; you cannot post to a guild
* sharing a post/comment with a guild is FORWARDING - never promote/yank

Scans templates, the main JS file and Python sources. Code identifiers that
are part of an external contract (URLs, JSON field names, DB columns) are
allowlisted below; everything else must use the vocabulary.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "ruqqus"

REPLY = re.compile(r"\b(reply|replies|replied|replying)\b", re.I)
PROMOTE = re.compile(r"\bpromot(e|ed|es|ing)\b", re.I)   # "promotion" (a promo code) is a different word
CROSSPOST = re.compile(r"cross-?post", re.I)
YANK = re.compile(r"\byank(ed|s)?\b", re.I)
OLD_PHRASES = (
    "Create post in +", "Share something with this guild", "Be the first to post",
    "disallows bots from posting", "Restricted Posting", "Disallow bots from posting",
)

# (path suffix or None for any file, substring) -> line is allowed to match
ALLOW = (
    (None, "/notifications/replies"),            # public URL, kept
    (None, "'replies'"), (None, '"replies"'),     # JSON field name, kept
    (None, "replies_last_checked_utc"),          # DB column, kept
    (None, "fa-reply"),                          # icon name
    (None, "reply_upvoted_1.svg"), (None, "reply_downvoted_1.svg"), (None, "reply_neutral_1.svg"),
    (None, "noreply"), (None, "lightning fast at replying"),   # email, not comments
    ("routes/front.py", "replies_only"),
    ("classes/mod_logs.py", "yank"),              # legacy mod-log kinds render old rows
    (None, "'is_crosspost'"),                     # API field name, kept
)
# plain English "promoting" harm, not the old name for forwarding
PROMOTE_PROSE = ("templates/help/csam.html", "templates/partials/posting_rules.html", "templates/help/rules.html")
SKIP_DIRS = ("chat/",)  # legacy DM chat: "reply" there is a different concept
SKIP_FILES = ("templates/embeds/comment_removed.html",)


def _files():
    for pattern in ("templates/**/*.html", "**/*.py", "assets/js/*.js"):
        for p in ROOT.glob(pattern):
            rel = p.relative_to(ROOT).as_posix()
            # the built chat bundle and vendored libraries are not ours to word
            if rel.startswith(SKIP_DIRS) or rel in SKIP_FILES or rel.endswith((".min.js", "chat_bundle.js", "js/bootstrap.js")):
                continue
            yield p, rel


def _violations(regex_or_phrases):
    out = []
    for p, rel in _files():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            hit = (regex_or_phrases.search(line) if hasattr(regex_or_phrases, "search")
                   else any(x in line for x in regex_or_phrases))
            if not hit:
                continue
            if any((suffix is None or rel.endswith(suffix)) and sub in line for suffix, sub in ALLOW):
                continue
            if regex_or_phrases is PROMOTE and rel in PROMOTE_PROSE:
                continue
            out.append(f"{rel}:{n}: {line.strip()[:110]}")
    return out


def test_no_reply_wording():
    assert not _violations(REPLY), "use 'comment', not 'reply':\n" + "\n".join(_violations(REPLY))


def test_no_promote_wording():
    assert not _violations(PROMOTE), "use 'forward', not 'promote':\n" + "\n".join(_violations(PROMOTE))


def test_no_yank_wording():
    assert not _violations(YANK), "use 'forward', not 'yank':\n" + "\n".join(_violations(YANK))


def test_no_posting_to_guild_phrases():
    assert not _violations(OLD_PHRASES), "guilds receive forwards, not posts:\n" + "\n".join(_violations(OLD_PHRASES))


def test_no_crosspost_wording():
    assert not _violations(CROSSPOST), "a post shared with a guild is forwarded:\n" + "\n".join(_violations(CROSSPOST))
