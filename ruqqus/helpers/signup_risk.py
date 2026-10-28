"""Whether a sign-up looks like a person or a bot: the rules of the signup guard (pure, stdlib only).

`routes/login.py` `sign_up_post` gathers the facts (helpers/signup_signals.py counts, helpers/captcha.py answers) into
a `Facts` and asks `assess`, which answers one of three things:
* REFUSE: a hard sign of a bot (a failed captcha, the hidden field filled in, a form kept for hours, a throwaway email
  domain, a scripting tool's User-Agent). Nothing is made; the form says only "There was a problem" (a throwaway email
  gets its own message, so a person can use another).
* QUARANTINE: the soft signs add up to `QUARANTINE_AT` or more, or the site is in a surge. The account is made but
  can't act on anyone else (helpers/quarantine.py) until an admin approves it at /admin/signups or it verifies an
  email that isn't a throwaway.
* ALLOW: a sign-up like any other.

Reasons are fixed keys (`LABELS`, English: only admins read them), stored as keys, never free text. A rule needs its
key, its weight or its refusal here, and a label; nothing else changes. tests/test_signup_risk.py.
"""
import ipaddress
import re
from dataclasses import dataclass

ALLOW, QUARANTINE, REFUSE = "allow", "quarantine", "refuse"

QUARANTINE_AT = 50               # the default; admins change it at /admin/signups (helpers/signup_signals.py)
FORM_MIN_AGE = 5                 # the route's own floor, older than the guard
FORM_FAST = 10                   # under this many seconds a form was filled in quickly
FORM_MAX_AGE = 2 * 3600          # a form kept longer than this was not filled in by someone at the page
PREFIX_BURST = 3                 # this many sign-ups from one /24 (IPv4) or /64 (IPv6) in the hour before
SHAPE_BURST = 5                  # this many sign-ups of one name shape in the hour before
SURGE_CEILING = 60               # sign-ups an hour across the site that switch surge on by themselves

# soft signs: (weight, label)
WEIGHTS = {
    "fast_form": 20,
    "no_language": 15,
    "tor": 25,
    "prefix_burst": 50,
    "machine_name": 20,
    "shape_burst": 35,
    "ban_evasion": 40,
    "captcha_unavailable": 50,
    "surge": 0,                  # never a weight: a surge quarantines everyone whatever the score
}

# hard signs: a refusal
REFUSALS = ("captcha_missing", "captcha_failed", "honeypot", "stale_form", "disposable_email", "automation_agent")

LABELS = {
    "fast_form": "Form filled in within 10 seconds",
    "no_language": "Browser sent no language",
    "tor": "Signed up over Tor",
    "prefix_burst": "Several sign-ups from the same network in the hour",
    "machine_name": "Username looks machine-made",
    "shape_burst": "Several sign-ups with the same username pattern in the hour",
    "ban_evasion": "Browser was used by a suspended account",
    "captcha_unavailable": "The captcha service could not be reached",
    "surge": "Signed up during a surge",
    "captcha_missing": "No captcha answer",
    "captcha_failed": "Captcha failed",
    "honeypot": "Filled in the hidden field",
    "stale_form": "Form older than two hours",
    "disposable_email": "Throwaway email domain",
    "automation_agent": "Scripting tool's User-Agent",
}

# a scripting tool or a headless browser names itself in its User-Agent unless it is told not to
AUTOMATION_AGENTS = (
    "python-requests", "python-urllib", "python-httpx", "aiohttp", "httpx/", "curl/", "wget/", "go-http-client",
    "headlesschrome", "phantomjs", "selenium", "puppeteer", "scrapy", "okhttp", "java/", "libwww-perl",
    "node-fetch", "axios/", "guzzlehttp", "mechanize",
)

CAPTCHA_STATES = ("off", "ok", "missing", "failed", "unavailable")


@dataclass(frozen=True)
class Facts:
    captcha: str = "off"         # CAPTCHA_STATES; "off" when no provider is configured
    honeypot: str = ""           # what the hidden field held
    form_age: int = 60           # seconds since the page was drawn
    user_agent: str = ""
    accept_language: str = ""
    disposable_email: bool = False
    tor: bool = False
    prefix_recent: int = 0       # sign-ups from this network in the hour before this one
    shape_recent: int = 0        # sign-ups with this name's shape in the hour before this one
    username: str = ""
    ban_evasion: bool = False
    surge: bool = False          # the admins' switch or the site's own count over the ceiling (`in_surge`)
    quarantine_at: int = QUARANTINE_AT


@dataclass(frozen=True)
class Verdict:
    action: str
    score: int
    reasons: tuple


def assess(facts):
    """REFUSE on the first hard sign; otherwise the soft signs' score decides between QUARANTINE and ALLOW."""
    hard = refusal(facts)
    if hard:
        return Verdict(REFUSE, 0, (hard,))
    reasons = tuple(key for key, present in (
        ("captcha_unavailable", facts.captcha == "unavailable"),
        ("fast_form", facts.form_age < FORM_FAST),
        ("no_language", not facts.accept_language.strip()),
        ("tor", facts.tor),
        ("prefix_burst", facts.prefix_recent >= PREFIX_BURST),
        ("machine_name", machine_made(facts.username)),
        ("shape_burst", shape_counts(name_shape(facts.username)) and facts.shape_recent >= SHAPE_BURST),
        ("ban_evasion", facts.ban_evasion),
        ("surge", facts.surge),
    ) if present)
    score = sum(WEIGHTS[key] for key in reasons)
    quarantined = facts.surge or score >= max(1, facts.quarantine_at)
    return Verdict(QUARANTINE if quarantined else ALLOW, score, reasons)


def refusal(facts):
    if facts.captcha == "missing":
        return "captcha_missing"
    if facts.captcha == "failed":
        return "captcha_failed"
    if facts.honeypot.strip():
        return "honeypot"
    if facts.form_age > FORM_MAX_AGE:
        return "stale_form"
    if facts.disposable_email:
        return "disposable_email"
    if automation_agent(facts.user_agent):
        return "automation_agent"
    return None


def automation_agent(agent):
    agent = (agent or "").lower()
    return any(marker in agent for marker in AUTOMATION_AGENTS)


def in_surge(site_recent, switched_on, ceiling=SURGE_CEILING):
    """The admins' switch, or more sign-ups across the site in the hour before than the ceiling."""
    return bool(switched_on) or site_recent >= max(1, ceiling)


def label(key):
    return LABELS.get(key, key)


def parse_reasons(stored):
    """A stored `reasons` column back into known keys (anything else is dropped)."""
    return tuple(key for key in (stored or "").split(",") if key in LABELS)


# ---------------------------------------------------------------- names

_VOWELS = set("aeiouy")
_CONSONANT_RUN = re.compile(r"[b-df-hj-np-tv-xz]{6,}")
_DIGIT_RUN = re.compile(r"[0-9]{5,}")
_HEXISH = re.compile(r"[0-9a-f]{12,}")


def machine_made(username):
    """A name a generator made rather than a person: five digits in a row, six consonants in a row ("Lindstrom" has
    five), eight or more letters without a vowel, or a long run that reads like a hex id (letters and digits mixed).
    A rare real name can match (a German compound); alone it never holds an account (its weight is under the
    threshold)."""
    name = (username or "").lower()
    if not name:
        return False
    if _DIGIT_RUN.search(name) or _CONSONANT_RUN.search(name):
        return True
    letters = [c for c in name if c.isalpha()]
    if len(letters) >= 8 and not any(c in _VOWELS for c in letters):
        return True
    for run in _HEXISH.findall(name):
        if any(c.isdigit() for c in run) and any(c.isalpha() for c in run):
            return True
    return False


def name_shape(username):
    """The pattern of a name, for spotting many sign-ups made from one template: a run of letters is `a`, a run of
    digits `9` and its length, an underscore `_` ("john48291" and "mary12345" are both `a9{5}`)."""
    shape = []
    for run in re.findall(r"[A-Za-z]+|[0-9]+|_+", username or ""):
        if run[0].isalpha():
            shape.append("a")
        elif run[0].isdigit():
            shape.append(f"9{{{len(run)}}}")
        else:
            shape.append("_")
    return "".join(shape)[:40]


def shape_counts(shape):
    """Only a shape with three or more digits in a row says anything: plain names and a name with a short number
    are what most people pick."""
    return bool(re.search(r"9\{([3-9]|\d\d)\}", shape or ""))


# ---------------------------------------------------------------- addresses and email

def ip_prefix(address):
    """The network an address is in: its /24 for IPv4, its /64 for IPv6 (one home or one small host). An address that
    can't be read is its own prefix."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return (address or "").strip()[:64]
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    bits = 24 if ip.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False))


def email_domain(email):
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower().rstrip(".")


def is_disposable(domain, domains):
    """`domain` or any domain it is under is in `domains` (a set of lowercase names)."""
    parts = (domain or "").lower().split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts) - 1))
