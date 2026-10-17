"""Shared drafts and collaborative posts: the rules (pure, no I/O; the SQL is helpers/collab_store.py, the routes
routes/collab.py).

A shared draft has **authors** and no owner. Who joins, who is removed, deleting the published post, publishing (when
the authors chose so) and **the rules themselves** are decided by **proposals** the authors vote on. The rules are the
authors' own (the owner's choice, 2026-10-17): by default every author must agree, except that an author who hasn't
been active on the site for 6 months isn't counted; the authors can change that together (majority or two thirds,
another window of inactivity, voting on publishing). Changing the rules always needs every counted author.

Only fixed names and values are ever stored (`clean_rules`), never free text.
"""
import re

DAY = 86400

UNANIMOUS, TWO_THIRDS, MAJORITY = "unanimous", "two_thirds", "majority"
DECISIONS = (UNANIMOUS, TWO_THIRDS, MAJORITY)
DECISION_LABELS = {UNANIMOUS: "Every author agrees", TWO_THIRDS: "Two thirds of the authors agree",
                   MAJORITY: "More than half of the authors agree"}
INACTIVE_CHOICES = (7, 30, 90, 180, 365, None)
INACTIVE_LABELS = {7: "a week", 30: "a month", 90: "3 months", 180: "6 months", 365: "12 months", None: "never"}
ANY_AUTHOR, VOTE = "any_author", "vote"
PUBLISH_LABELS = {ANY_AUTHOR: "Any author can publish", VOTE: "Publishing is put to a vote"}
PROPOSAL_DAY_CHOICES = (7, 14, 30)

DEFAULT_RULES = {"decision": UNANIMOUS, "inactive_after_days": 180, "publish": ANY_AUTHOR, "proposal_days": 30}

ADD, REMOVE, RULES, DELETE, PUBLISH = "add_author", "remove_author", "change_rules", "delete_post", "publish"
KINDS = (ADD, REMOVE, RULES, DELETE, PUBLISH)
OPEN, PASSED, FAILED, WITHDRAWN, EXPIRED = "open", "passed", "failed", "withdrawn", "expired"

MAX_AUTHORS = 50
PROPOSALS_PER_DAY = 20
OPEN_PER_AUTHOR = 3              # open proposals one author may have in one draft (audit C-06)
DOCS_PER_DAY = 20
FORKS_PER_DAY = 10
INVITE_DAYS = 30
OPS_PER_MINUTE = 600
RESYNC_AFTER = 500               # a browser more revisions behind than this reloads the text
TITLE_MAX = 280
BODY_MAX = 25_000

CANT_ADD = "@{name} can't be added."
NOT_ACTIVE = "@{name} hasn't been active for {window}, so isn't counted."

_NAME = re.compile(r"^@?([A-Za-z0-9_]{1,25})$")


class Refused(ValueError):
    """Shown to the member as it is."""


def clean_rules(value):
    """The rules from stored or sent JSON: only known names and values; anything else is the default."""
    value = value if isinstance(value, dict) else {}
    out = dict(DEFAULT_RULES)
    if value.get("decision") in DECISIONS:
        out["decision"] = value["decision"]
    inactive = value.get("inactive_after_days", out["inactive_after_days"])
    if inactive in INACTIVE_CHOICES and not isinstance(inactive, bool):
        out["inactive_after_days"] = inactive
    if value.get("publish") in (ANY_AUTHOR, VOTE):
        out["publish"] = value["publish"]
    days = value.get("proposal_days")
    if days in PROPOSAL_DAY_CHOICES and not isinstance(days, bool):
        out["proposal_days"] = days
    return out


def rules_from_form(form):
    """The rules a "change the rules" form asks for (strings), or Refused."""
    decision = (form.get("decision") or "").strip()
    publish = (form.get("publish") or "").strip()
    raw_inactive = (form.get("inactive_after_days") or "").strip()
    raw_days = (form.get("proposal_days") or "").strip()
    if decision not in DECISIONS or publish not in (ANY_AUTHOR, VOTE):
        raise Refused("Choose how decisions are made and who can publish.")
    if raw_inactive == "never":
        inactive = None
    elif raw_inactive.isascii() and raw_inactive.isdigit() and int(raw_inactive) in INACTIVE_CHOICES:
        inactive = int(raw_inactive)
    else:
        raise Refused("Choose when an author stops being counted.")
    if not (raw_days.isascii() and raw_days.isdigit() and int(raw_days) in PROPOSAL_DAY_CHOICES):
        raise Refused("Choose how long a proposal stays open.")
    return {"decision": decision, "inactive_after_days": inactive, "publish": publish, "proposal_days": int(raw_days)}


def describe(rules):
    """The rules in plain words, for the draft page and a rules proposal."""
    rules = clean_rules(rules)
    window = rules["inactive_after_days"]
    lines = [DECISION_LABELS[rules["decision"]] + " to add or remove an author or delete the post.",
             "Changing these rules always needs every counted author.",
             PUBLISH_LABELS[rules["publish"]] + "."]
    lines.append("Every author counts, however long they've been away." if window is None else
                 f"An author who hasn't been active on the site for {INACTIVE_LABELS[window]} isn't counted.")
    lines.append(f"A proposal nobody decides closes after {rules['proposal_days']} days.")
    return lines


def parse_name(value):
    found = _NAME.match(str(value or "").strip())
    if not found:
        raise Refused("Type the account's @name.")
    return found.group(1)


def is_active(last_active_utc, rules, now):
    window = clean_rules(rules)["inactive_after_days"]
    return window is None or (last_active_utc or 0) >= now - window * DAY


def counted(authors, rules, now, *, exclude=None):
    """The authors who count for a decision: [(user_id, last_active_utc)] -> ids, minus `exclude` (the subject of a
    removal) and anyone inactive under the rules."""
    return [uid for uid, active in authors if uid != exclude and is_active(active, rules, now)]


def threshold(kind, rules, voters):
    """How many yes votes pass a proposal among `voters` counted authors."""
    decision = UNANIMOUS if kind == RULES else clean_rules(rules)["decision"]
    if voters <= 0:
        return 1
    if decision == UNANIMOUS:
        return voters
    if decision == TWO_THIRDS:
        return -(-2 * voters // 3)              # ceil(2/3)
    return voters // 2 + 1                     # more than half


def tally(kind, rules, voter_ids, votes):
    """OPEN, PASSED or FAILED. `voter_ids` are the counted authors now; `votes` {user_id: True/False}. Votes of anyone
    no longer counted don't count. A proposal fails as soon as the yes votes can no longer reach the threshold."""
    voters = set(voter_ids)
    if not voters:
        return FAILED
    need = threshold(kind, rules, len(voters))
    yes = sum(1 for uid, v in votes.items() if uid in voters and v)
    no = sum(1 for uid, v in votes.items() if uid in voters and not v)
    if yes >= need:
        return PASSED
    if len(voters) - no < need:
        return FAILED
    return OPEN


def add_refusal(*, name, candidate, authors_now, already, blocked_with_any):
    """Why an account can't be proposed as an author (one message for every reason a block could explain)."""
    if candidate is None or candidate.is_deleted or getattr(candidate, "is_bot", False) or candidate.is_suspended:
        return CANT_ADD.format(name=name)
    if already:
        return f"@{candidate.username} is already an author."
    if blocked_with_any:
        return CANT_ADD.format(name=candidate.username)
    if authors_now >= MAX_AUTHORS:
        return f"A shared draft can have at most {MAX_AUTHORS} authors."
    return None


def remove_refusal(*, subject_is_author, subject_is_me):
    if subject_is_me:
        return "To stop being an author, leave the draft."
    if not subject_is_author:
        return "That account isn't an author."
    return None


def closes_at(rules, now):
    return now + clean_rules(rules)["proposal_days"] * DAY


def invite_open(passed_utc, now):
    return passed_utc and now < passed_utc + INVITE_DAYS * DAY


def check_title(value):
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if not title:
        raise Refused("Give the post a title before publishing.")
    if len(title) > TITLE_MAX:
        raise Refused(f"A title can be at most {TITLE_MAX} characters.")
    return title


# --- the post options a draft keeps ------------------------------------------------------------------------------
# What a regular post has beside its title and text and that a collaborative post can have too: a link, who can
# comment, content disclosure, sensitive, and guilds to forward to. Not here, on purpose: an audience of circles,
# anonymous, co-authors (the authors ARE the co-authors), a poll, a Space or a live thread (see the module notes).

URL_MAX = 2048
FORWARD_MAX = 10
COMMENT_CHOICES = (0, 1, 2, 3)           # everyone, accounts the author follows, Premium, no one (helpers/comment_permission.py)
FLAG_OPTIONS = ("paid_partnership", "made_with_ai", "sensitive")
_GUILD_NAME = re.compile(r"[A-Za-z0-9_]{1,40}")


def clean_options(raw):
    """Only known options, in their own types; anything else is dropped. Used on what is stored and on what arrives."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    url = str(raw.get("url") or "").strip()
    if url and len(url) <= URL_MAX and re.match(r"https?://\S+$", url, re.I):
        out["url"] = url
    try:
        permission = int(raw.get("comment_permission", 0))
    except (TypeError, ValueError):
        permission = 0
    out["comment_permission"] = permission if permission in COMMENT_CHOICES else 0
    for name in FLAG_OPTIONS:
        out[name] = raw.get(name) in (True, "true", "True", "1", 1, "on")
    guilds, seen = [], set()
    for name in raw.get("forward_guilds") or []:
        name = str(name).strip().lstrip("+")
        if _GUILD_NAME.fullmatch(name) and name.lower() not in seen and len(guilds) < FORWARD_MAX:
            seen.add(name.lower())
            guilds.append(name)
    out["forward_guilds"] = guilds
    return out


def check_options(raw):
    """`clean_options` for what a member typed: a link that isn't one or too many guilds is refused, not dropped."""
    raw = raw if isinstance(raw, dict) else {}
    url = str(raw.get("url") or "").strip()
    if url and not (len(url) <= URL_MAX and re.match(r"https?://\S+$", url, re.I)):
        raise Refused("A link starts with http:// or https://.")
    if len(raw.get("forward_guilds") or []) > FORWARD_MAX:
        raise Refused(f"You can forward to at most {FORWARD_MAX} guilds.")
    for name in raw.get("forward_guilds") or []:
        if not _GUILD_NAME.fullmatch(str(name).strip().lstrip("+")):
            raise Refused(f"“{str(name)[:40]}” isn't a guild name.")
    return clean_options(raw)


def post_fields(options):
    """The options as the form fields POST /api/vue/submit and the edit read."""
    options = clean_options(options)
    fields = {"comment_permission": str(options["comment_permission"])}
    if options.get("url"):
        fields["url"] = options["url"]
    for name in ("paid_partnership", "made_with_ai"):
        fields[name] = "true" if options[name] else ""
    if options["sensitive"]:
        fields["sensitive"] = "on"
    if options["forward_guilds"]:
        fields["forward_guilds"] = list(options["forward_guilds"])
    return fields
