"""Health checks for whatever watches the site (Docker, a load balancer, an uptime monitor).

`GET /healthz` is liveness: the process answers, and nothing else is asked (no database, no Redis), so a slow
database can't make a monitor restart a healthy worker. `GET /readyz` is readiness: the database answers `SELECT 1`
and Redis answers a ping, within a couple of seconds, else 503 naming what failed ("database", "redis").

Both are answered before `before_request` does anything (`__main__.HEALTH_PATHS`: no ban lookups, no session cookie),
are exempt from the rate limiter, are never cached and say nothing about versions, hostnames or settings.
"""
from flask import jsonify
from sqlalchemy import text

from ruqqus.__main__ import app, limiter, r, _engine

CHECK_SECONDS = 2


def _answer(body, status=200):
    resp = jsonify(body)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/healthz")
@limiter.exempt
def healthz():
    return _answer({"status": "ok"})


def failing():
    """What is not answering right now: a list of "database" and/or "redis" (empty when all is well)."""
    out = []
    try:
        with _engine.connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {int(CHECK_SECONDS * 1000)}"))
            conn.execute(text("SELECT 1"))
    except Exception:
        out.append("database")
    if r is not None:
        try:
            r.ping()
        except Exception:
            out.append("redis")
    return out


@app.get("/readyz")
@limiter.exempt
def readyz():
    down = failing()
    if down:
        return _answer({"status": "unavailable", "failing": down}, 503)
    return _answer({"status": "ok"})
