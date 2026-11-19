"""Parent and child accounts (Stage 2 of the child lock; the owner, 2026-11-05). The rules, with no I/O; the SQL is helpers/child_store.py, what a parent
may see is helpers/child_activity.py, the routes routes/children_pages.py.

An adult whose age is confirmed (`parent_refusal`) makes a **child sub-account** from their own Settings > Children: a username, a password and the
month and year the child was born (checked and not kept: `birth_refusal`). The account is a child account with `age_lock` 2 (`age_declaration.PARENT_LOCK`)
and is linked to its parent in `account_links`. **A child who signs up alone is a standalone child and is not linked.** Rules that hold:

* **Up to `MAX_CHILDREN` (5) children a parent**, and at most `PER_DAY` new ones in a day. A child is under 18 (an adult makes their own account) and at
  least 13, or 16 in Australia, where the law keeps anyone under 16 off a service whatever their parent says (`child_min_age`). This is also how a teen
  below their country's digital age of consent gets an account: the parent consents.
* **What the parent switches** (`child_controls`): chat, group chats and calls (`chat`), Spaces and live threads (`spaces`), coins, tips, Premium and paid
  circles (`money`) and starting a guild (`anonymous_guilds`, its old name) all start **off**. A locked account is refused a feature unless its parent has
  switched it on (`age_declaration.feature_refusal`); Sensitive content and the Child word filter are never the parent's to switch. **There is no switch for
  posting or commenting: a child account is read-only** (`age_declaration.content_refusal`, the owner's decision of 2026-11-05), so nothing it could post is
  left for Private posting or anonymity to govern; both are gone.
* **What the parent sees** (`child_activity`): the child's posts, comments, votes, bookmarks, follows, guilds, the reports made against them, their
  recent sign-ins and the names of who they chat with. **Never the content of a chat**: chats are end-to-end encrypted, the server can't read them. The
  child is told all of this in their own Settings and in the notice they get when the account is made.
* **Growing up**: a sub-account that an age check confirms as 18 or over is **released automatically** (the parent is told and their access ends). One
  that only says it is 18 or over, where a tick is accepted, **asks the parent**, who agrees or doesn't (`ask_release_utc`). A parent may also stop managing
  an account (it stays a standalone child account) or delete it.
* **A parent that goes**: when their account is deleted or permanently banned the children are deleted; when it is suspended for a time the children are
  suspended for the same time and come back with it. The parent is warned first and can download each child's data (`routes/settings.py`, `User.ban`).

tests/test_child_accounts.py, tests/test_child_accounts_assets.py, tests/pg/test_child_accounts.py."""
from ruqqus.helpers.i18n_runtime import N_

MAX_CHILDREN = 5
PER_DAY = 3
PER_WINDOW = 5               # accounts made, whatever became of them (stopped, released, deleted): or stopping and deleting would make the limit free
WINDOW_DAYS = 90
ADULT_AGE = 18
DEFAULT_MIN_AGE = 13
PARENT_MIN_AGE = {"AU": 16}           # Australia: nobody under 16 may hold an account, with or without a parent's consent

FEATURES = ("chat", "spaces", "money", "anonymous_guilds")      # all start off
CONTROLS = FEATURES
DEFAULTS = {name: False for name in FEATURES}

# what each control says on the parent's page: (name, what it allows). English stored, drawn with msg_text / msg
CONTROL_TEXT = {
    "chat": (N_("Chat, group chats and calls"),
             N_("They can message people, join group chats and make calls. The site never shows you what a chat says: chats are end-to-end encrypted. Whoever knows their password can still sign in and read them.")),
    "spaces": (N_("Spaces and live threads"), N_("They can join and listen to Spaces. They can't host one or post in one: a child account only reads. Voice calls in a Space also need Chat to be on.")),
    "money": (N_("Coins, tips, Premium and paid circles"), N_("They can buy and spend coins, tip, buy Premium and join or run paid circles.")),
    "anonymous_guilds": (N_("Starting guilds"), N_("They can start a guild. They still can't post or comment: a child account only reads.")),
}

NEED_ADULT = N_("You can make a child account once your own account is confirmed as 18 or over. You can say so in Settings > Content.")
NEED_CHECK = N_("Where you are, your age has to be confirmed with an age-check service before you can make a child account.")
TOO_MANY = N_("You can have at most %(num)s child accounts.")
TODAY = N_("You can make %(num)s child accounts a day. Try again tomorrow.")
RECENT = N_("You have made %(num)s child accounts in the last %(days)s days. Try again later.")
BIRTH_INVALID = N_("Enter the month and year they were born.")
TOO_YOUNG = N_("Nobody under %(age)s may have an account where you are.")
NOT_A_CHILD = N_("They are 18 or over, so they can make their own account.")
TERMS_NEEDED = N_("Tick the box to agree to the Terms for them.")
NO_CHILD = N_("You don't have a child account called that.")
WRONG_PASSWORD = N_("That password isn't right.")
UNKNOWN_CONTROL = N_("That isn't something you can switch.")
NO_REQUEST = N_("They haven't asked for that.")
OWES = N_("This account owes money, so it can't be deleted yet.")

# The public reason of a child account suspended with its parent: neutral, because a banned profile and the API print it (a reason that said "your parent" would
# tell anyone the account is a child's). It is listed in action_log.AUTOMATIC_REASONS so the line it writes is the staff's only. The child is told why in a notice,
# and `child_family.suspended_until_utc` (not this text) is how the parent's unban finds the suspension that was theirs to lift.
SUSPENDED_REASON = N_("This account is suspended.")
SUSPENDED_NOTICE = N_("Your parent or guardian's account was suspended, so yours is suspended until theirs is back.")


# The steps that close an account live with the routes they need (Matrix, the chat groups): routes/settings.py hands them over when it loads, so nothing under
# helpers/ or classes/ imports a route (tests/test_ad_income_stays_out_assets.py follows every import, lazy ones too).
closer = None


def close_account(row, reason):
    """Close a child account that isn't the signed-in browser's: a parent's account was banned or deleted, or the parent deletes it."""
    if closer is None:
        raise RuntimeError("the steps that close an account are not loaded: routes/settings.py sets child_accounts.closer")
    return closer(row, reason, sign_out_browser=False)


def child_min_age(country):
    """The youngest a child may be for a parent to make their account: 13, or 16 in Australia."""
    from ruqqus.helpers import age_declaration
    code = age_declaration._country(country)
    return PARENT_MIN_AGE.get(code, DEFAULT_MIN_AGE)


def parent_refusal(parent, region_locked, children, made_today, made_recent=0):
    """The message when this account may not make a child account now, else None. `region_locked` is whether the regional rule holds the parent at
    the Child level (a strict country, age not confirmed), `children` how many they have, `made_today` how many they made in the last day and `made_recent`
    how many in the last WINDOW_DAYS (made, not still linked)."""
    from ruqqus.helpers import age_declaration
    if parent is None or getattr(parent, "is_bot", False) or getattr(parent, "is_deleted", False) or getattr(parent, "is_suspended", False) \
            or getattr(parent, "is_quarantined", False):
        return NEED_ADULT
    if age_declaration.is_locked(parent) or getattr(parent, "age_declared", None) != age_declaration.ADULT:
        return NEED_ADULT
    if region_locked:
        return NEED_CHECK
    if children >= MAX_CHILDREN:
        return TOO_MANY
    if made_today >= PER_DAY:
        return TODAY
    if made_recent >= PER_WINDOW:
        return RECENT
    return None


def eligible_parent(parent, region_locked):
    """May this account make child accounts at all (ignoring how many)? The page offers the form only to them."""
    return parent_refusal(parent, region_locked, 0, 0) is None


def parse_birth(year, month):
    """(year, month) as whole numbers, or None for anything else (ASCII digits only: `str.isdigit()` takes other scripts' digits too)."""
    out = []
    for raw in (year, month):
        text = (raw or "").strip() if isinstance(raw, str) else ""
        if not text or len(text) > 4 or not (text.isascii() and text.isdigit()):
            return None
        out.append(int(text))
    return tuple(out)


def months_since(year, month, now_year, now_month):
    """Whole months from the birth month to this one."""
    return (now_year * 12 + now_month) - (year * 12 + month)


def birth_refusal(year, month, now_year, now_month, country=None):
    """The message when a child born in this month and year may not have an account made for them, else None. Only the month and year are asked and
    neither is kept. The day isn't known, so each limit leans away from the risk: a child counts as a month younger than the months say when the minimum
    is checked (a child who might still be 12 is refused) and as the full months when 18 is (one who might already be 18 is refused)."""
    parsed = parse_birth(year, month)
    if parsed is None:
        return BIRTH_INVALID
    year, month = parsed
    if not (1 <= month <= 12) or not (1900 <= year <= now_year) or (year, month) > (now_year, now_month):
        return BIRTH_INVALID
    elapsed = months_since(year, month, now_year, now_month)
    if elapsed - 1 < child_min_age(country) * 12:
        return TOO_YOUNG
    if elapsed >= ADULT_AGE * 12:
        return NOT_A_CHILD
    return None


def clean_control(name):
    """`name` if it is one of the switches, else None."""
    return name if isinstance(name, str) and name in CONTROLS else None


def switches(controls):
    """The controls with every one present: what is stored, or the defaults for what isn't."""
    controls = controls or {}
    return {name: bool(controls.get(name, DEFAULTS[name])) for name in CONTROLS}
