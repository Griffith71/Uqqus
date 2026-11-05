"""The age declaration and the child lock: a member says once whether they are 18 or over, or under 18; a child account (and, in the
UK, EU/EEA and Australia, anyone not yet age-confirmed) is locked. Under-13s may not have an account (help/terms.html "Age").
**A declaration is what someone said, never a check**: a child can sign up and say they are an adult, and anyone can read the site signed
out. Only a vendor's check (helpers/age_assurance.py) or an admin counts as confirming an age (`age_assured_utc`).

`users.age_declared` is NULL (not asked: a member from before, a bot, the site's account), ADULT or CHILD, `age_declared_utc` when.
`users.age_lock` is NULL, `REGION_LOCK` (1: a standalone locked account that a confirmed age may lift) or `PARENT_LOCK` (2: a parent's
sub-account, Stage 2). A lock only sits on a child account (the database CHECK `users_age_lock_rule`). Sign-up asks and refuses
without an answer (routes/login.py); a member from before is asked only in Settings > Content (the owner, 2026-11-05).

**Two things lock, kept apart.**
* *The stored lock* (`is_locked`): a child account (the under-18 option, or any sign-up from a strict country, where "18 or over" can't be
  picked). It stays on the Child word filter, never shows Sensitive content, and can't use chat, Spaces, money, anonymity or guild creation
  (`feature_refusal`; routes/wrappers.py `not_child_locked`); others can't start those things with it (`target_refusal`). Private posting
  starts on for it (helpers/private_posting.py).
* *The regional rule* (`region_locked`): anyone in a strict country who has not been age-confirmed browses at the Child level, a visitor or
  an account made elsewhere included. It changes only what they SEE, through `visibility.viewer_level`; features follow the stored lock.
  An unknown country (no header, Tor, a VPN) is not strict. Staff are confirmed through the admin route so they see what they moderate.

A stored lock is lifted by the vendor check anywhere, or by declaring 18 or over where that is accepted (not a strict country): `lift_fields`.
That is one statement, so the constraint is never broken in between, and it turns Private posting off. A child account's word filter and
sensitive switch are held by `locked_filter` / `locked_sensitive` and the database CHECK `users_age_declared_rule`.

What else a child account can't do is in routes/wrappers.py (`not_child_locked`). Rules here need no database except `terms_notice_ids`.
tests/test_age_declaration.py, tests/pg/test_age_declaration.py, tests/pg/test_age_lock.py.
"""
import os
import re

from sqlalchemy import text

from ruqqus.helpers import consent
from ruqqus.helpers.i18n_runtime import N_
from ruqqus.helpers.visibility import CHILD as CHILD_FILTER

ADULT, CHILD = 1, 2

REGION_LOCK, PARENT_LOCK = 1, 2

FILTER_LOCKED = N_("Your account is age-restricted, so its word filter stays on Child until you confirm your age in Settings.")
SENSITIVE_LOCKED = N_("Your account is age-restricted, so sensitive content stays hidden until you confirm your age in Settings.")
FEATURE_LOCKED = N_("This isn't available while your account is age-restricted. You can confirm your age in Settings > Content.")
TARGET_LOCKED = N_("You can't do that with this account.")
DECLARE_NOT_HERE = N_("Where you are, your age has to be confirmed with an age-check service.")
VENDOR_NOT_READY = N_("Age confirmation isn't available yet.")
UNDER_AGE_CLOSED_TITLE = N_("Your account was closed")
UNDER_AGE_CLOSED = N_("The age check found that you are under the youngest age allowed to have an account where you are.")
ALREADY_ANSWERED = N_("You've already answered this. Only an admin can change your answer.")
NO_ANSWER = N_("Please choose one of the two answers.")
SIGN_UP_NO_ANSWER = N_("Please say how old you are.")

ANSWER_TITLE = N_("Your age")
BACK = N_("Go back")

# told once, by scripts/notify_terms_age.py, in the words the Terms now use. Stored in English like every notice.
TERMS_NOTICE = N_("We've changed the Age section of our [Terms](/help/terms). An account for someone under 18 now has a child lock, "
                  "and in the UK, the EU and Australia your age has to be confirmed with an age-check service before the lock "
                  "comes off or you can see sensitive content. You can say whether you're 18 or over in Settings > Content.")

# an admin changed the answer: the member is told what that does to their account
CONFIRMED = 3     # not stored: an admin's "18 or over, and checked" (age_declared ADULT with age_assured_utc set)
ADMIN_NOTICES = {
    CHILD: N_("An admin marked this as a child account. It has the child lock: the Child word filter, which hides profanity and "
              "anything marked sensitive, and some features are off. You can lift it by confirming your age in Settings > Content."),
    ADULT: N_("An admin marked this account as belonging to someone 18 or over. Your word filter hasn't changed: you "
              "can change it in Settings > Content, and turning the Child filter down needs your password."),
    None: N_("An admin cleared the age answer on your account. You'll be asked again."),
    CONFIRMED: N_("An admin confirmed that you are 18 or over. Your account no longer has the child lock. Your word filter hasn't "
                  "changed: you can change it in Settings > Content, and turning the Child filter down needs your password."),
}

# the admin panel and the Log (admin pages stay English)
LABELS = {None: "not asked", ADULT: "18 or over", CHILD: "child account", CONFIRMED: "18 or over, age confirmed"}


def parse(value):
    """ADULT or CHILD from a form's value, else None. Only the two exact strings count."""
    if value == "1":
        return ADULT
    if value == "2":
        return CHILD
    return None


def is_child(v):
    return getattr(v, "age_declared", None) == CHILD


def is_locked(v):
    """Is this account under the stored lock (a child account, whichever way it came to be one)?"""
    return v is not None and (getattr(v, "age_declared", None) == CHILD or getattr(v, "age_lock", None) is not None)


def assured(v):
    """Has a vendor (or an admin) confirmed that this account's holder is 18 or over?"""
    return bool(v is not None and (getattr(v, "age_assured_utc", 0) or 0) > 0)


# --- where the law asks for it: the UK, the EU/EEA and Australia (the owner, 2026-11-05). An unknown country is NOT strict.
STRICT_DEFAULT = (consent.REGIONS - {"CH"}) | {"AU"}
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_strict_cache = {}


def _country(value):
    """The two capitals of a plain country, or None (no header, Tor `T1`, `XX`, any other shape)."""
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if _COUNTRY.match(value) and value not in consent.UNKNOWN else None


def strict_countries():
    """The countries that lock: AGE_LOCK_COUNTRIES (a comma-separated list) if set, else STRICT_DEFAULT."""
    raw = os.environ.get("AGE_LOCK_COUNTRIES", "")
    if raw not in _strict_cache:
        listed = frozenset(c for c in (_country(x) for x in raw.split(",")) if c) if raw.strip() else STRICT_DEFAULT
        _strict_cache[raw] = listed
    return _strict_cache[raw]


def is_strict(country):
    found = _country(country)
    return found is not None and found in strict_countries()


def region_locked(viewer, country):
    """Must this viewer, in this country, browse at the Child level for want of a confirmed age? (a visitor is `None`)"""
    return is_strict(country) and not assured(viewer)


def request_country():
    """The country of the request being answered (Cloudflare's header), or None outside a request: a job, a script, a test with
    no request. Posting jobs that run in-process send no header, so they follow the account's own state and never a country."""
    from flask import has_request_context, request
    return request.headers.get("CF-IPCountry") if has_request_context() else None


def current_region_locked(viewer):
    """`region_locked` for the request being answered."""
    return region_locked(viewer, request_country())


# --- the youngest a person may sign up on their own: the highest of Facebook's minimum for the country (13; South Korea and Spain 14,
# Australia 16, from Meta's help page on underage accounts) and the national digital age of consent in the UK/EU/EEA (GDPR Article 8).
# To be re-checked with a lawyer before launch: both lists are moving. AGE_MIN_AGES ("DE=16,FR=15") overrides.
DEFAULT_MIN_AGE = 13
FACEBOOK_MIN = {"KR": 14, "ES": 14, "AU": 16}
CONSENT_AGE = {
    "AT": 14, "BE": 13, "BG": 14, "HR": 16, "CY": 14, "CZ": 15, "DK": 13, "EE": 13, "FI": 13, "FR": 15, "DE": 16, "GR": 15, "HU": 16,
    "IE": 16, "IT": 14, "LV": 13, "LT": 14, "LU": 16, "MT": 13, "NL": 16, "PL": 16, "PT": 13, "RO": 16, "SK": 16, "SI": 16, "ES": 14,
    "SE": 13, "IS": 13, "LI": 16, "NO": 13, "GB": 13, "UK": 13,
}


def _min_age_overrides():
    found = {}
    for part in os.environ.get("AGE_MIN_AGES", "").split(","):
        code, _, number = part.partition("=")
        if _country(code) and number.strip().isdigit():
            found[_country(code)] = int(number)
    return found


def min_age(country):
    """The youngest a person may be to sign up on their own from this country (13 where nothing says more)."""
    code = _country(country)
    override = _min_age_overrides().get(code)
    if override is not None:
        return override
    return max(DEFAULT_MIN_AGE, FACEBOOK_MIN.get(code, 0), CONSENT_AGE.get(code, 0))


def signup_answer(answer, country):
    """The answer a sign-up stores. From a strict country "18 or over" can't be picked (a posted one is stored as the locked answer):
    an adult there looks like a teenager until a vendor has checked."""
    if answer == ADULT and is_strict(country):
        return CHILD
    return answer


def feature_refusal(v):
    """The message when this account may not chat, host Spaces, spend or earn coins, post anonymously or make a guild, else None."""
    return FEATURE_LOCKED if is_locked(v) else None


def post_refusal(v, sensitive, anonymous):
    """The message when a locked account's new post asks for something it may not have: to be marked Sensitive (it can't see such
    content, so it doesn't post it) or anonymous. The composers don't draw those switches for it; this is the server's word."""
    return FEATURE_LOCKED if is_locked(v) and (sensitive or anonymous) else None


def target_refusal(target):
    """The message when somebody may not start something with this account (a chat, a group, a call, a tip). It never says why."""
    return TARGET_LOCKED if is_locked(target) else None


def is_asked(v):
    """Does this signed-in member still have to answer? Never a bot account: nobody signs in to one."""
    return v is not None and getattr(v, "age_declared", 0) is None and not getattr(v, "is_bot", False)


def fields_for(answer, now):
    """The columns an answer writes, in one statement so the constraints are never broken in between. The only place a filter
    level, the sensitive choice, the lock and Private posting are set for being a child account. `answer` None clears it (an admin)."""
    fields = {"age_declared": answer, "age_declared_utc": now if answer else 0, "age_lock": None}
    if answer == CHILD:
        fields.update(filter_level=CHILD_FILTER, hide_offensive=True, all_shows_sensitive=False, age_lock=REGION_LOCK,
                      private_posting=True)
    return fields


def lift_fields(now, assured_by_check, reset_filter=True):
    """The columns that lift a lock, in one statement: 18 or over, no lock, Private posting off (a grown-up who just confirmed
    would otherwise keep posting to Friends only without knowing why), the word filter back to Standard (an admin's confirmation
    leaves it: the member lowers it with their password). `assured_by_check` is a vendor's check or an admin's, never a tick."""
    fields = {"age_declared": ADULT, "age_declared_utc": now, "age_lock": None, "private_posting": False}
    if assured_by_check:
        fields["age_assured_utc"] = now
    if reset_filter:
        fields["filter_level"] = 1
    return fields


def assure_fields(v, now):
    """The columns a confirmed age (a vendor's check, or an admin) writes for this account. A locked account is lifted (`lift_fields`);
    an account that was never locked only records the check, and keeps the word filter and Private posting it chose."""
    if is_locked(v):
        return lift_fields(now, True)
    fields = {"age_assured_utc": now}
    if getattr(v, "age_declared", None) is None:
        fields.update(age_declared=ADULT, age_declared_utc=now)
    return fields


def locked_filter(v, new_level):
    """The message when this account may not move to `new_level`, or None."""
    return FILTER_LOCKED if is_locked(v) and new_level != CHILD_FILTER else None


def locked_sensitive(v, wanted):
    """The message when this account may not turn on sensitive content in All and For You, or None."""
    return SENSITIVE_LOCKED if is_locked(v) and wanted else None


def terms_notice_ids(db):
    """Who is told the Terms changed: live people's accounts that agreed to the old Terms, not told yet. An account that
    signed up since answered on the page that links the new ones (its answer is as old as the account: the model stamps its
    own created_utc, so they can be a moment apart), and looking for the notice itself makes the script safe to run again."""
    rows = db.execute(text(
        "SELECT u.id FROM users u WHERE NOT u.is_deleted AND NOT u.is_bot AND u.id <> 1 "
        "AND (u.age_declared IS NULL OR u.age_declared_utc > u.created_utc + 60) "
        "AND NOT EXISTS (SELECT 1 FROM notifications n JOIN comments_aux a ON a.id = n.comment_id "
        "                WHERE n.user_id = u.id AND a.notice_msgid = :msgid) ORDER BY u.id"),
        {"msgid": TERMS_NOTICE})
    return [row[0] for row in rows]
