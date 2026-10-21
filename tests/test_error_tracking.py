"""Error tracking (ruqqus/helpers/error_tracking.py): what leaves the server is the error and where it happened, and
nothing that could name a person. `scrub` is pure, so it is tested without the Sentry SDK."""
from pathlib import Path

from ruqqus.helpers import error_tracking as et

ROOT = Path(__file__).resolve().parent.parent


def event():
    return {
        "request": {"method": "POST", "url": "https://site.example/@alice/post/abc?token=secret&email=a@b.co",
                    "query_string": "token=secret", "data": {"password": "hunter2"}, "cookies": {"session_ruqqus": "xyz"},
                    "headers": {"Cookie": "session_ruqqus=xyz", "Authorization": "Bearer abc", "User-Agent": "Mozilla",
                                "X-Forwarded-For": "203.0.113.7", "Content-Type": "text/html"}},
        "user": {"id": 7, "username": "alice", "ip_address": "203.0.113.7", "email": "alice@example.com"},
        "server_name": "host-1",
        "extra": {"body": "what someone typed"},
        "message": "login failed for alice@example.com from 203.0.113.7",
        "logentry": {"message": "bad %s", "params": ["alice@example.com"]},
        "exception": {"values": [{
            "type": "ProgrammingError",
            "value": "(psycopg2.errors.UndefinedColumn) no such column\n[SQL: SELECT * FROM users WHERE email = %(e)s]\n"
                     "[parameters: {'e': 'alice@example.com'}]",
            "stacktrace": {"frames": [{"filename": "ruqqus/routes/login.py", "function": "login_post", "lineno": 90,
                                       "vars": {"password": "hunter2"}, "context_line": "x = 1", "pre_context": ["a"],
                                       "post_context": ["b"]}]}}]},
        "breadcrumbs": {"values": [{"message": "GET /@alice from 198.51.100.4", "data": {"url": "http://x/?t=1"}}]},
    }


def test_a_report_holds_the_error_and_where_it_happened_and_nothing_personal():
    out = et.scrub(event())
    assert out["request"] == {"method": "POST", "url": "https://site.example/@alice/post/abc",
                              "headers": {"User-Agent": "Mozilla", "Content-Type": "text/html"}}
    for gone in ("user", "server_name", "extra"):
        assert gone not in out
    value = out["exception"]["values"][0]["value"]
    assert "alice" not in value and "SELECT" not in value and "parameters" not in value
    assert value.startswith("(psycopg2.errors.UndefinedColumn) no such column") and value.endswith("[query removed]")
    frame = out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame == {"filename": "ruqqus/routes/login.py", "function": "login_post", "lineno": 90}      # never locals
    assert out["message"] == "login failed for [email] from [address]"
    assert "params" not in out["logentry"]
    crumb = out["breadcrumbs"]["values"][0]
    assert crumb["message"] == "GET /@alice from [address]" and "data" not in crumb


def test_the_whole_event_has_no_email_or_address_left():
    import json
    text = json.dumps(et.scrub(event()))
    for leak in ("alice@example.com", "203.0.113.7", "198.51.100.4", "hunter2", "session_ruqqus", "Bearer", "secret"):
        assert leak not in text, leak


def test_noisy_breadcrumbs_are_dropped_and_the_rest_cleaned():
    assert et.scrub_breadcrumb({"category": "httplib", "message": "x"}) is None
    assert et.scrub_breadcrumb({"category": "console", "message": "x"}) is None
    kept = et.scrub_breadcrumb({"category": "query", "message": "a@b.co", "data": {"q": 1}})
    assert kept == {"category": "query", "message": "[email]"}


def test_it_is_off_without_a_dsn_and_never_imports_the_sdk():
    assert et.init({}, "1.0") is False and et.init({"SENTRY_DSN": "  "}, "1.0") is False


def test_it_sends_no_personal_data_by_setting():
    src = (ROOT / "ruqqus" / "helpers" / "error_tracking.py").read_text(encoding="utf-8")
    for setting in ("send_default_pii=False", "include_local_variables=False", 'max_request_body_size="never"',
                    "traces_sample_rate=0", "before_send=scrub", "before_breadcrumb=scrub_breadcrumb"):
        assert setting in src, setting
    main = (ROOT / "ruqqus" / "__main__.py").read_text(encoding="utf-8")
    assert main.index("error_tracking.init(environ, _version)") < main.index("app = Flask(__name__,")
    assert "sentry-sdk==" in (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "'ERROR_TRACKING_ON' | app_config" in (ROOT / "ruqqus" / "templates" / "help" / "privacy.html").read_text(encoding="utf-8")
