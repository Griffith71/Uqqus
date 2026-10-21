"""Guild analytics for guildmasters (helpers/guild_insights.py): `GET /+<guild>/mod/insights` (and `.json`) and the page-view
counter the guild page calls. Counts only, never who.

**Who may read**: a guildmaster of the guild with the configuration or content permission (or full), and not under a
ban. **Anyone else gets a plain 404**: a visitor, a member, another guild's guildmaster, a guildmaster without those
permissions, a banned guild, and a guild the viewer's filter hides, so the page never confirms anything about a guild. The
module is `guild_insights_pages.py` and not `guild_insights.py` so the star imports never replace the helper."""
import time

from flask import abort, g, jsonify, render_template, request

from ruqqus.classes.boards import PROFILE_BOARD_NAME
from ruqqus.helpers import guild_insights as rules
from ruqqus.helpers import guild_insights_store as store
from ruqqus.helpers import insights as view_rules
from ruqqus.helpers.get import get_guild
from ruqqus.helpers.visibility import board_hidden
from ruqqus.helpers.wrappers import auth_required
from ruqqus.__main__ import app, r


def count_page_view(viewer, board):
    """Counts one open of the guild's page when it counts: not an API call, not a guildmaster of it, not a robot, link
    preview or prefetch, and the same viewer only once in half an hour (a key that lives only in Redis, as post views do,
    helpers/insights.py). Best effort: nothing here can stop the page."""
    try:
        if r is None or board.is_banned or request.path.startswith("/api") or board.name.lower() == PROFILE_BOARD_NAME:
            return
        if viewer is not None and board.has_mod(viewer):
            return
        agent = request.headers.get("User-Agent", "")
        if view_rules.is_robot(agent) or view_rules.is_prefetch(request.headers):
            return
        key = view_rules.viewer_key(viewer.id if viewer else None, request.remote_addr, agent, app.config.get("SECRET_KEY") or "")
        if not r.set(f"gv:{board.id}:{key}", "1", nx=True, ex=rules.DEBOUNCE_SECONDS):
            return
        store.bump(g.db, board.id, "page_views", int(time.time()))
    except Exception:
        pass


def _board_for_guildmaster(guildname, v):
    """The guild, for a guildmaster who may read its insights; a 404 for everyone else, whatever the reason."""
    board = get_guild(guildname, v=v, graceful=True)
    if board is None or board.name.lower() == PROFILE_BOARD_NAME or board.is_banned or board_hidden(board, v):
        abort(404)
    if not rules.may_read(board.has_mod(v)) or v.is_suspended:
        abort(404)
    return board


def _report(board):
    days = rules.parse_days(request.args.get("days"))
    data = store.report(g.db, board.id, int(time.time()), days)
    data["chart"] = rules.chart_allowed(data["members"])
    if not data["chart"]:
        data["series"] = None            # a small guild shows window totals only
    return data


@app.route("/+<guildname>/mod/insights", methods=["GET"])
@auth_required
def guild_insights_page(guildname, v):
    board = _board_for_guildmaster(guildname, v)
    data = _report(board)
    peak = max([1] + [max(day["joins"], day["leaves"], day["posts"], day["comments"], day["views"]) for day in (data["series"] or [])])
    return render_template("guild/insights.html", v=v, b=board, data=data, windows=rules.WINDOWS, peak=peak,
                           started_note=rules.STARTED_NOTE, privacy_note=rules.PRIVACY_NOTE, signed=rules.signed,
                           min_chart=rules.MIN_CHART_MEMBERS)


@app.route("/+<guildname>/mod/insights.json", methods=["GET"])
@auth_required
def guild_insights_json(guildname, v):
    board = _board_for_guildmaster(guildname, v)
    response = jsonify(_report(board))
    response.headers["Cache-Control"] = "no-store"
    return response
