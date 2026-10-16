"""The infrastructure under calls and Spaces: which pages may use the microphone and camera, the relay (coturn) and
how Synapse hands out its credentials, the job that keeps it within its monthly allowance, and the live-updates
stream. The promises: no page but a call or Space page can open the microphone or camera; the relay never reaches a
private address, its secret is never in git and only its client port is published; and the live stream never
touches the database once it is streaming."""
import re
from pathlib import Path

from ruqqus.helpers import device_access

ROOT = Path(__file__).resolve().parent.parent


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# --- microphone and camera ------------------------------------------------------------------------------------

def test_only_call_and_space_pages_may_use_the_microphone_and_camera():
    for path in ("/call/!abc:ruqqus.local", "/space/12"):
        assert device_access.is_call_page(path)
        assert "microphone 'self';" in device_access.feature_policy(path)
        assert "camera 'self';" in device_access.feature_policy(path)
        assert "microphone=(self)" in device_access.permissions_policy(path)
        assert "camera=(self)" in device_access.permissions_policy(path)
    # the chat page and its side panel never hold a call: it runs in a window of its own
    for path in ("/", "/chat", "/api/chat/token", "/call/", "/space/", "/calls/x", "/settings/media", "", None):
        assert not device_access.is_call_page(path)
        assert "microphone 'none';" in device_access.feature_policy(path)
        assert "camera 'none';" in device_access.feature_policy(path)
        assert "microphone=()" in device_access.permissions_policy(path)
        assert "camera=()" in device_access.permissions_policy(path)


def test_every_other_feature_stays_off_on_every_page():
    for path in ("/", "/call/x"):
        policy = device_access.feature_policy(path)
        for name in ("geolocation", "payment", "fullscreen", "sync-xhr", "push", "notifications"):
            assert f"{name} 'none';" in policy
        assert "geolocation=()" in device_access.permissions_policy(path)
        assert "payment=()" in device_access.permissions_policy(path)


def test_every_response_gets_both_headers_from_the_one_rule():
    main = read("ruqqus", "__main__.py")
    assert 'device_access.feature_policy(request.path)' in main
    assert 'device_access.permissions_policy(request.path)' in main
    assert "microphone 'none'; camera 'none'" not in main      # the old fixed string is gone


# --- the relay ------------------------------------------------------------------------------------------------

def test_synapse_hands_out_relay_credentials_from_a_secret_file():
    hs = read("synapse", "homeserver.yaml")
    assert "turn_uris:" in hs and "turn:localhost:3478?transport=udp" in hs
    assert "turn_shared_secret_path: /data/turn_shared_secret" in hs
    assert not re.search(r"^turn_shared_secret:", hs, re.M)      # never the secret itself
    assert "turn_allow_guests: false" in hs and "turn_user_lifetime: 1h" in hs


def test_images_are_pinned_and_only_the_relays_client_port_is_published():
    compose = read("docker-compose.yml")
    assert "matrixdotorg/synapse:latest" not in compose and "matrixdotorg/synapse:v1." in compose
    assert re.search(r"image: coturn/coturn:\d+\.\d+\.\d+", compose)
    coturn = compose.split("  coturn:\n", 1)[1].split("\n\n", 1)[0]
    assert '"3478:3478/udp"' in coturn and '"3478:3478/tcp"' in coturn
    assert "9641" not in coturn and "50000" not in coturn          # metrics and relay ports stay inside
    assert "./coturn/turnserver.conf:/etc/coturn/turnserver.conf:ro" in coturn
    assert "--allowed-peer-ip=$$(hostname -i)" in coturn          # only itself, among private addresses
    assert "./synapse/turn_shared_secret:/data/turn_shared_secret:ro" in compose
    assert "TURN_MONTHLY_GB=${TURN_MONTHLY_GB:-" in compose and "TURN_METRICS_URL=" in compose


def test_the_relay_never_reaches_a_private_address():
    conf = read("coturn", "turnserver.conf.template")
    denied = set(re.findall(r"^denied-peer-ip=(\S+)$", conf, re.M))
    for block in ("10.0.0.0-10.255.255.255", "172.16.0.0-172.31.255.255", "192.168.0.0-192.168.255.255",
                  "127.0.0.0-127.255.255.255", "169.254.0.0-169.254.255.255", "100.64.0.0-100.127.255.255",
                  "0.0.0.0-0.255.255.255", "::1", "fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
                  "fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff", "::ffff:0.0.0.0-::ffff:255.255.255.255"):
        assert block in denied, block
    assert "allowed-peer-ip" not in conf.split("# On a live site", 1)[1].split("listening-port", 1)[1]
    for line in ("no-multicast-peers", "no-cli", "no-tcp-relay", "use-auth-secret", "fingerprint", "prometheus"):
        assert re.search(rf"^{line}$", conf, re.M), line
    assert re.search(r"^user-quota=\d+$", conf, re.M) and re.search(r"^max-bps=\d+$", conf, re.M)
    assert re.search(r"^total-quota=\d+$", conf, re.M)


def test_the_relay_secret_is_never_in_git():
    conf = read("coturn", "turnserver.conf.template")
    assert "static-auth-secret=__TURN_SHARED_SECRET__" in conf
    ignored = read(".gitignore")
    assert "/coturn/turnserver.conf\n" in ignored and "/synapse/turn_shared_secret\n" in ignored
    gen = read("scripts", "gen_dev_env.py")
    assert '"TURN_SHARED_SECRET": lambda: secrets.token_hex(32)' in gen
    assert 'newline=""' in gen                                    # coturn would read a CR as part of the secret
    assert "TURN_SHARED_SECRET=" in read(".env.example")


# --- the monthly allowance ------------------------------------------------------------------------------------

def test_the_counting_job_runs_as_its_own_program():
    conf = read("supervisord.conf")
    job = conf.split("[program:ruqqusturn]")[1].split("[program:")[0]
    assert "scripts/turn_usage.py" in job and "autostart=true" in job
    assert 'environment=PYTHONPATH="/opt/ruqqus/service"' in job
    script = read("scripts", "turn_usage.py")
    assert '"--once" in sys.argv' in script and "db.commit()" in script and "timeout=5" in script
    assert "TURN_METRICS_URL" in script


def test_the_table_is_in_the_migration_and_the_schema():
    migration = read("scripts", "migrations", "2026-10-21_turn_usage.sql")
    assert "CREATE TABLE IF NOT EXISTS public.turn_usage" in migration
    schema = read("schema.sql")
    assert "CREATE TABLE public.turn_usage (" in schema
    for column in ("month character(7) PRIMARY KEY", "sent_bytes bigint DEFAULT 0 NOT NULL", "last_counter bigint",
                   "updated_utc integer DEFAULT 0 NOT NULL"):
        assert column in migration and column in schema


def test_the_store_builds_no_sql_from_text():
    store = read("ruqqus", "helpers", "turn_usage_store.py")
    assert 'f"' not in store and "f'" not in store and ".format(" not in store and "commit" not in store.split('"""', 2)[2]


# --- live updates ---------------------------------------------------------------------------------------------

def test_the_live_stream_is_for_members_and_never_reads_the_database():
    route = read("ruqqus", "routes", "live_events.py")
    assert '@app.route("/api/live/events", methods=["GET"])\n@limiter.limit("120/minute", key_func=member_limit_key)\n@auth_required' in route
    code = route.split('"""', 2)[2]
    assert "g.db" not in code and "stream_with_context" not in code
    assert 'mimetype="text/event-stream"' in route and 'resp.headers["Cache-Control"] = "no-store"' in route
    assert "live_bus.allowed_topics(v," in route                  # topics only through their guards
    assert "from .live_events import *" in read("ruqqus", "routes", "__init__.py")


def test_compression_never_buffers_the_stream():
    # Flask-Compress only compresses the mimetypes it is given; the stream must not be one of them
    assert "event-stream" not in read("ruqqus", "__main__.py")


def test_the_live_endpoints_are_limited_per_account_not_per_address():
    main = read("ruqqus", "__main__.py")
    assert "def member_limit_key():" in main and 'f"member:{user_id}"' in main
    limit = '@limiter.limit("120/minute", key_func=member_limit_key)'
    limited = {
        "live_events.py": ['@app.route("/api/live/events", methods=["GET"])'],
        "calls.py": ['@app.route("/api/calls/heartbeat", methods=["POST"])', '@app.route("/api/calls/status", methods=["GET"])'],
        "spaces.py": ['@app.route("/api/spaces/<sid>/heartbeat", methods=["POST"])', '@app.route("/api/spaces/<sid>/state", methods=["GET"])'],
        "live_comments.py": ['@app.route("/api/post/<pid>/live_comments", methods=["GET"])'],
        "live_threads.py": ['@app.route("/api/post/<pid>/live_updates", methods=["GET"])'],
    }
    for name, routes in limited.items():
        lines = read("ruqqus", "routes", name).splitlines()
        for route in routes:
            assert lines[lines.index(route) + 1] == limit, (name, route)
