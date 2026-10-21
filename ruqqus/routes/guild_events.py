"""Guild events and their calendar (rules in helpers/guild_events.py, SQL in helpers/guild_event_store.py).

Every page is read through the guild's own visibility (`visible_guild` in routes/guild_wiki.py: `Board.can_view`,
`board_hidden`, a banned guild is a 404). Guildmasters with content permission create, edit and cancel events; anyone
signed in who can see the guild answers Going or Interested. **Counts only, never who**: nothing here draws or answers
who said what, only the counts and the reader's own answer. The `.ics` feed is for public guilds only.
"""
import html
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.classes import *
from ruqqus.__main__ import app

from ruqqus.helpers import guild_events as event_rules
from ruqqus.helpers import guild_event_store as event_store
from ruqqus.helpers import bell as bell_rules
from ruqqus.helpers.visibility import text_hidden
from ruqqus.routes.guild_wiki import visible_guild, _render, _severity, _content_gm, _log


def _error(message, status=400):
    return jsonify({"error": message}), status


def _now():
    return int(time.time())


def _event_or_404(board, eid):
    row = event_store.event(g.db, board.id, eid)
    if row is None:
        abort(404)
    return row


def _view(row, v, counts, now):
    """What a page draws of one event, as this reader may see it."""
    hidden = text_hidden(row[6], v)
    return {
        "id": row[0], "title": "Event hidden by your word filter" if hidden else row[3], "hidden": hidden,
        "details_html": "" if hidden else row[5], "starts_utc": row[7], "ends_utc": row[8],
        "location_url": row[9], "location_text": row[10], "space_post_id": row[11],
        "status": event_rules.status_of((row[7], row[8], row[12]), now),
        "going": counts.get(row[0], {}).get("going", 0), "interested": counts.get(row[0], {}).get("interested", 0),
    }


def _views(rows, v, now):
    counts = event_store.counts(g.db, [r[0] for r in rows])
    return [_view(r, v, counts, now) for r in rows]


def _space_post_id(value):
    """The id of the Space a field names (a post link or id of a post that is a Space), None for none."""
    value = str(value or "").strip()
    if not value:
        return None
    from ruqqus.helpers import guild_wiki as wiki_rules
    try:
        _, b36 = wiki_rules.clean_resource_target(wiki_rules.POST, value)
    except wiki_rules.Refused:
        raise event_rules.Refused("Give the Space's post link.")
    from ruqqus.helpers import space_store
    pid = base36decode(b36)
    if not space_store.for_posts(g.db, [pid]):
        raise event_rules.Refused("That post isn't a Space.")
    return pid


def _fields(now, keep_start=None):
    details = event_rules.clean_details(request.values.get("details"))
    starts, ends = event_rules.clean_times(request.values.get("starts_utc"), request.values.get("ends_utc"), now,
                                           keep_start=keep_start)
    url, place = event_rules.clean_location(request.values.get("location_url"), request.values.get("location_text"))
    title = event_rules.clean_title(request.values.get("title"))
    return {"title": title, "details": details, "details_html": _render(details) if details else "",
            "word_severity": max(_severity(title), _severity(details) if details else 0, _severity(place) if place else 0),
            "starts_utc": starts, "ends_utc": ends, "location_url": url, "location_text": place,
            "space_post_id": _space_post_id(request.values.get("space"))}


def has_events(board, v):
    """Whether the guild has an event coming that this reader may see (the Events tab)."""
    return board.can_view(v) and event_store.next_event(g.db, board.id, _now()) is not None


def next_event_for(board, v):
    """The sidebar's "Next event" box, or None."""
    if not board.can_view(v):
        return None
    row = event_store.next_event(g.db, board.id, _now())
    return _views([row], v, _now())[0] if row else None


app.jinja_env.globals.update(guild_has_events=has_events, guild_next_event=next_event_for)


# --- reading ---------------------------------------------------------------------------------------------------------

@app.route("/+<guildname>/events", methods=["GET"])
@auth_desired
def guild_events(guildname, v):
    board = visible_guild(guildname, v, need_wiki=False)
    now = _now()
    return render_template("guild/events.html", v=v, b=board, view="list", tab="events",
                           upcoming=_views(event_store.upcoming(g.db, board.id, now), v, now),
                           past=_views(event_store.past(g.db, board.id, now), v, now),
                           gm=_content_gm(board, v), ics=not (board.is_private or board.is_circle))


@app.route("/+<guildname>/events/calendar", methods=["GET"])
@auth_desired
def guild_events_calendar(guildname, v):
    board = visible_guild(guildname, v, need_wiki=False)
    now = _now()
    year, month = event_rules.parse_month(request.args.get("month"), now)
    weeks = event_rules.month_grid(year, month)
    start, end = event_rules.grid_span(weeks)
    events = _views(event_store.between(g.db, board.id, start, end), v, now)
    by_day = {}
    for e in events:
        by_day.setdefault(event_rules.day_key(e["starts_utc"]), []).append(e)
    prev_y, prev_m = event_rules.shift_month(year, month, -1)
    next_y, next_m = event_rules.shift_month(year, month, 1)
    return render_template("guild/events.html", v=v, b=board, view="calendar", tab="events", weeks=weeks, month=month,
                           year=year, by_day=by_day, prev=f"{prev_y:04d}-{prev_m:02d}", next=f"{next_y:04d}-{next_m:02d}",
                           today=event_rules.day_key(now), gm=_content_gm(board, v),
                           ics=not (board.is_private or board.is_circle))


@app.route("/+<guildname>/events/<int:eid>", methods=["GET"])
@auth_desired
def guild_event(guildname, eid, v):
    board = visible_guild(guildname, v, need_wiki=False)
    now = _now()
    row = _event_or_404(board, eid)
    event = _views([row], v, now)[0]
    space_post = None
    if row[11]:
        post = get_post(base36encode(row[11]), v=v, graceful=True)
        if post is not None and not post.deleted_utc and not post.is_banned and not post.in_private_guild_for(v):
            space_post = post
    refusal = event_rules.rsvp_refusal(event=(row[12], row[8]), now=now, signed_in=v is not None,
                                       suspended=bool(v and v.is_suspended), bot=bool(v and v.is_bot),
                                       exiled=bool(v and board.has_ban(v)))
    return render_template("guild/event.html", v=v, b=board, tab="events", event=event, space_post=space_post,
                           mine=event_store.answer_of(g.db, eid, v.id if v else None), rsvp_refusal=refusal,
                           gm=_content_gm(board, v), ics=not (board.is_private or board.is_circle))


@app.route("/+<guildname>/events/<int:eid>.json", methods=["GET"])
@auth_desired
def guild_event_json(guildname, eid, v):
    """One event as JSON: counts and the reader's own answer, never who answered."""
    board = visible_guild(guildname, v, need_wiki=False)
    row = _event_or_404(board, eid)
    event = _views([row], v, _now())[0]
    event["mine"] = event_store.answer_of(g.db, eid, v.id if v else None)
    return jsonify(event)


def _ics_events(board, rows):
    host = app.config["SERVER_NAME"]
    out = []
    for r in rows:
        if text_hidden(r[6], None):           # the feed is read by other apps, as a visitor
            continue
        out.append({"id": r[0], "title": r[3], "details": r[4], "starts_utc": r[7], "ends_utc": r[8],
                    "url": f"https://{host}/+{board.name}/events/{r[0]}",
                    "location": r[10] or r[9], "cancelled": bool(r[12])})
    return out


def _ics_response(body, filename):
    resp = make_response(body)
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    # calendar apps may keep it a few minutes; no shared cache keeps a guild's events after it turns private (G-06)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


def _public_or_404(board):
    # a feed handed to another app carries the guild's events out of it: public guilds only
    if board.is_private or board.is_circle or board.is_banned:
        abort(404)


@app.route("/+<guildname>/events.ics", methods=["GET"])
def guild_events_ics(guildname):
    board = visible_guild(guildname, None, need_wiki=False)
    _public_or_404(board)
    now = _now()
    rows = list(event_store.past(g.db, board.id, now, limit=20)) + list(event_store.upcoming(g.db, board.id, now, limit=200))
    body = event_rules.ics(_ics_events(board, rows), host=app.config["SERVER_NAME"], name=f"+{board.name} events", now=now)
    return _ics_response(body, f"{board.name}-events.ics")


@app.route("/+<guildname>/events/<int:eid>.ics", methods=["GET"])
def guild_event_ics(guildname, eid):
    board = visible_guild(guildname, None, need_wiki=False)
    _public_or_404(board)
    row = _event_or_404(board, eid)
    body = event_rules.ics(_ics_events(board, [row]), host=app.config["SERVER_NAME"], name=f"+{board.name} events",
                           now=_now())
    return _ics_response(body, f"{board.name}-event-{eid}.ics")


# --- guildmasters: create, edit, cancel --------------------------------------------------------------------------

@app.route("/+<guildname>/events/new", methods=["GET"])
@auth_required
def guild_event_new(guildname, v):
    board = visible_guild(guildname, v, need_wiki=False)
    if not _content_gm(board, v):
        abort(403)
    return render_template("guild/event_edit.html", v=v, b=board, tab="events", event=None, limits=event_rules)


@app.route("/+<guildname>/events/new", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_event_create(guildname, v):
    board = visible_guild(guildname, v, need_wiki=False)
    if not _content_gm(board, v):
        abort(403)
    now = _now()
    refusal = event_rules.make_refusal(event_store.made_since(g.db, board.id, now - 30 * 86400))
    if refusal:
        return _error(refusal, 429)
    try:
        fields = _fields(now)
    except event_rules.Refused as e:
        return _error(str(e))
    eid = event_store.create(g.db, board.id, v.id, fields, now)
    _log(board, v, "event_create", fields["title"])
    g.db.commit()
    # a new event is a guildmaster's announcement: it rings the guild's bell (helpers/bell.py)
    bell_rules.ring_guild(board, v, bell_rules.EVENT, eid, f"/+{board.name}/events/{eid}")
    return jsonify({"ok": True, "url": f"/+{board.name}/events/{eid}"})


@app.route("/+<guildname>/events/<int:eid>/edit", methods=["GET"])
@auth_required
def guild_event_edit(guildname, eid, v):
    board = visible_guild(guildname, v, need_wiki=False)
    if not _content_gm(board, v):
        abort(403)
    row = _event_or_404(board, eid)
    space = f"/post/{base36encode(row[11])}" if row[11] else ""
    return render_template("guild/event_edit.html", v=v, b=board, tab="events", event=row, space=space, limits=event_rules)


@app.route("/+<guildname>/events/<int:eid>/edit", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_event_save(guildname, eid, v):
    board = visible_guild(guildname, v, need_wiki=False)
    if not _content_gm(board, v):
        abort(403)
    row = _event_or_404(board, eid)
    if row[12]:
        return _error("A cancelled event can't be changed.", 409)
    try:
        fields = _fields(_now(), keep_start=row[7])
    except event_rules.Refused as e:
        return _error(str(e))
    event_store.edit(g.db, board.id, eid, fields)
    _log(board, v, "event_edit", fields["title"])
    g.db.commit()
    return jsonify({"ok": True, "url": f"/+{board.name}/events/{eid}"})


@app.route("/+<guildname>/events/<int:eid>/cancel", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_event_cancel(guildname, eid, v):
    board = visible_guild(guildname, v, need_wiki=False)
    if not _content_gm(board, v):
        abort(403)
    row = _event_or_404(board, eid)
    event_store.cancel(g.db, board.id, eid, _now())
    _log(board, v, "event_cancel", row[3])
    g.db.commit()
    return jsonify({"ok": True, "url": f"/+{board.name}/events/{eid}"})


# --- answers --------------------------------------------------------------------------------------------------------

@app.route("/+<guildname>/events/<int:eid>/rsvp", methods=["POST"])
@is_not_banned
@validate_formkey
def guild_event_rsvp(guildname, eid, v):
    """Going, Interested, or neither. Answers with the counts and the reader's own answer, never who else."""
    board = visible_guild(guildname, v, need_wiki=False)
    row = _event_or_404(board, eid)
    now = _now()
    refusal = event_rules.rsvp_refusal(event=(row[12], row[8]), now=now, signed_in=True,
                                       suspended=bool(v.is_suspended), bot=bool(v.is_bot), exiled=bool(board.has_ban(v)))
    if refusal:
        return _error(refusal, 403)
    try:
        status = event_rules.clean_status(request.values.get("status"))
    except event_rules.Refused as e:
        return _error(str(e))
    event_store.answer(g.db, eid, v.id, status, now)
    g.db.commit()
    counts = event_store.counts(g.db, [eid])[eid]
    return jsonify({"mine": status, "going": counts["going"], "interested": counts["interested"]})


# --- reminders (scripts/recomputes.py) ------------------------------------------------------------------------------

def send_due_reminders(db_session, now=None):
    """Tells everyone who answered an event starting within the hour, once (`claim_reminder`), if they can still see
    the guild. The notice names the event unless the person's word filter hides its title. Returns how many told."""
    from ruqqus.helpers.alerts import send_notification
    now = now or _now()
    told = 0
    with app.test_request_context():
        g.db = db_session()
        try:
            for row in event_store.due_reminders(g.db, now, event_rules.REMIND_BEFORE):
                if not event_store.claim_reminder(g.db, row[0], now):
                    continue
                g.db.commit()
                board = g.db.query(Board).filter_by(id=row[1]).first()
                if board is None or board.is_banned:
                    continue
                for uid in event_store.reminder_user_ids(g.db, row[0]):
                    user = g.db.query(User).filter_by(id=uid).first()
                    if user is None or user.is_deleted or user.is_suspended or not board.can_view(user) or board.has_ban(user):
                        continue
                    name = "An event" if text_hidden(row[6], user) else f"**{_md_plain(row[3])}**"
                    send_notification(user, f"{name} in +{board.name} starts within the hour: "
                                            f"[see the event](/+{board.name}/events/{row[0]})")
                    told += 1
        finally:
            g.db.close()
    return told


def _md_plain(value):
    """A title inside notification markdown: no markdown of its own."""
    return "".join("\\" + c if c in "\\`*_{}[]()#+-.!|<>~=" else c for c in html.unescape(str(value)))


@app.template_filter("iso_utc")
def iso_utc(utc):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(utc or 0)))


@app.template_filter("day_badge")
def day_badge(utc):
    """"16 Oct" (UTC; the script writes the reader's own day)."""
    t = time.gmtime(int(utc or 0))
    return f"{t.tm_mday} {time.strftime('%b', t)}"


@app.template_filter("month_name")
def month_name(month):
    import calendar
    return calendar.month_name[int(month)]


@app.template_filter("link_host")
def link_host(url):
    from urllib.parse import urlparse
    return urlparse(str(url or "")).hostname or str(url or "")
