"""Health checks (ruqqus/routes/health.py): liveness answers without touching the database or Redis; readiness asks
both and names what failed; both are answered before `before_request` does anything, exempt from the rate limiter,
never cached, and say nothing about versions or settings."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


HEALTH = read("ruqqus", "routes", "health.py")
MAIN = read("ruqqus", "__main__.py")


def test_liveness_asks_nothing_and_readiness_asks_the_database_and_redis():
    live = HEALTH.split("def healthz(", 1)[1].split("\n\n\n", 1)[0]
    assert "_engine" not in live and "r.ping" not in live and "failing(" not in live
    check = HEALTH.split("def failing(", 1)[1].split("\n\n\n", 1)[0]
    assert 'SET LOCAL statement_timeout' in check and "SELECT 1" in check and "r.ping()" in check
    assert 'out.append("database")' in check and 'out.append("redis")' in check
    ready = HEALTH.split("def readyz(", 1)[1]
    assert '"status": "unavailable", "failing": down}, 503' in ready


def test_both_are_exempt_uncached_and_say_nothing_about_the_site():
    assert HEALTH.count("@limiter.exempt") == 2
    assert 'resp.headers["Cache-Control"] = "no-store"' in HEALTH
    for leak in ("version", "SERVER_NAME", "environ", "hostname", "DATABASE_URL"):
        assert leak not in HEALTH.split('"""', 2)[2], leak


def test_they_are_answered_before_anything_else_runs():
    assert 'HEALTH_PATHS = ("/healthz", "/readyz")' in MAIN
    hook = MAIN.split("def before_request():", 1)[1]
    first = hook.index("if request.path in HEALTH_PATHS:")
    assert first < hook.index("READ_ONLY") and first < hook.index("g.db = db_session()")
    assert "g.skip_session_cookie = True" in hook[first:first + 200]      # no cookie for a monitor
    assert "from .health import *" in read("ruqqus", "routes", "__init__.py")


def test_docker_watches_the_site():
    assert "HEALTHCHECK" in read("Dockerfile") and "/healthz" in read("Dockerfile")
    assert "ENV PYTHONUNBUFFERED=1" in read("Dockerfile")
    compose = read("docker-compose.yml")
    assert "/readyz" in compose
    assert compose.count("healthcheck:") >= 4 and compose.count("restart: unless-stopped") >= 5
