"""The "About" box at the top of a profile's right sidebar (templates/partials/profile_about.html).

What it shows is already public elsewhere: the account's age, its Rep and the counts behind it, its badges, the guilds
it leads (guild pages list their guildmasters) and, in a box of its own, its public curations. It reads the same to the
owner as to anyone else, so an owner sees their profile as others do. Two rules keep it that way:
* a guild is listed only when a visitor could see it (never a private or Circle guild, whoever looks, the owner and its
  members included), and not when its name trips the viewer's word filter;
* a curation is listed only when it is public, to its owner too, and when the viewer's word filter allows its name
  (curations keep no stored rating, so the name is rated as it is drawn, as a username or a guild name is).
"""
import time

CURATIONS_SHOWN = 10

_DAY = 86400


def age_text(created_utc, now=None):
    """How long an account has existed, in its largest whole unit: "3 years", "2 months", "5 days", "today"."""
    now = int(time.time()) if now is None else now
    days = max(0, int(now) - int(created_utc or 0)) // _DAY
    if days >= 365:
        n, unit = days // 365, "year"
    elif days >= 30:
        n, unit = days // 30, "month"
    elif days >= 1:
        n, unit = days, "day"
    else:
        return "today"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def guild_roles(boards, owner_id, may_see):
    """[(board, role)] for the guilds an account leads that the viewer may see; role is "Founder" or "Guildmaster".

    boards: the account's accepted, unbanned guilds (User.boards_modded); may_see(board): the viewer's rule."""
    return [(b, "Founder" if b.creator_id == owner_id else "Guildmaster") for b in boards if may_see(b)]


def shown_curations(curations, hidden, limit=CURATIONS_SHOWN):
    """(shown, more): the public curations whose names the viewer may see, newest first as given, at most `limit`,
    and how many more there are. A private curation is never shown, whoever looks."""
    visible = [c for c in curations if not c.is_private and not hidden(c.name)]
    return visible[:limit], max(0, len(visible) - limit)


# --- what a page reads ---------------------------------------------------------

def guilds_for(owner, v):
    from ruqqus.helpers.visibility import board_hidden
    return guild_roles(owner.boards_modded, owner.id, lambda b: b.can_view(None) and not board_hidden(b, v))


def curations_for(db, owner, v):
    from ruqqus.classes.curations import Curation
    from ruqqus.helpers.visibility import viewer_level
    from ruqqus.helpers.word_filter_store import name_severity
    from ruqqus.helpers.wordfilter import is_hidden

    level = viewer_level(v)
    rows = db.query(Curation).filter(Curation.owner_id == owner.id, Curation.is_private.is_(False)).order_by(
        Curation.created_utc.desc()).limit(200).all()
    return shown_curations(rows, lambda name: is_hidden(level, name_severity(name or "", db)))
