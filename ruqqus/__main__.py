# IMPORTANT: gevent monkey patching MUST be performed before any other imports.
# This prevents KeyError and threading/socket issues with gevent.
import gevent.monkey
gevent.monkey.patch_all()

import psycogreen.gevent
psycogreen.gevent.patch_psycopg()

# All other imports and code follow below
import os
from os import environ
import secrets
from flask import Flask, request, redirect, jsonify, abort, make_response, render_template, g
from flask.sessions import SecureCookieSessionInterface
from flask import session as flask_session  # Explicit alias for Flask's session
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
from time import sleep
from collections import deque
import psycopg2

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import OperationalError, StatementError, InternalError, DatabaseError
from sqlalchemy.orm import Session as SQLAlchemySession, sessionmaker, scoped_session, Query as _Query
from sqlalchemy import *
from sqlalchemy.pool import QueuePool
import requests
import random
import redis
import gevent
import sys
import traceback
import time

from redis import BlockingConnectionPool, ConnectionPool


_version = "2.38.0"

# def time_limit(s):
#     def wrapper_maker(f):
#         def wrapper(*args, **kwargs):

#             timeout=gevent.Timeout(s, gevent.Timeout)
#             timeout.start()
#             target_thread=gevent.spawn(f, *args, **kwargs)
#             try:
#                 target_thread.join()
#                 return target_thread.value
#             except gevent.timeout.Timeout as t:
#                 target_thread.kill()
#                 try:
#                     g.db.rollback()
#                     g.db.invalidate()
#                 except:
#                     pass
#                 abort(500)

#         wrapper.__name__=f.__name__
#         return wrapper
#     return wrapper_maker

# class Flask_Timeout(Flask):
    
            
#     def full_dispatch_request(self, *args, **kwargs):
        
        
#         @copy_current_request_context
#         @time_limit(10)
#         @copy_current_request_context
#         def thread_target(self, *args, **kwargs):   
#             return super(Flask_Timeout, self).full_dispatch_request(*args, **kwargs)
        
#         return thread_target(self, *args, **kwargs)

app = Flask(__name__,
            template_folder='./templates',
            static_folder='./static'
            )
# the client's real address, by the deployment's own proxies only (helpers/client_address.py: audit X-01)
from ruqqus.helpers.client_address import ClientAddress
app.wsgi_app = ClientAddress(app.wsgi_app)
app.url_map.strict_slashes = False

app.config["SITE_NAME"]=environ.get("SITE_NAME", "laba").lstrip().rstrip()
# the legal entity named in the terms, the legal pages and the footer; the site's own name until it is set
app.config["COMPANY_NAME"]=(environ.get("COMPANY_NAME") or "").strip() or app.config["SITE_NAME"]

app.config["SITE_COLOR"]=environ.get("SITE_COLOR", "148fd5").lstrip().rstrip()

app.config["RUQQUSPATH"]=environ.get("RUQQUSPATH", os.path.dirname(os.path.realpath(__file__)))

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['DATABASE_URL'] = environ.get(
    "DATABASE_CONNECTION_POOL_URL",
    environ.get("DATABASE_URL"))

app.config['SQLALCHEMY_READ_URIS'] = [
    environ.get("DATABASE_CONNECTION_READ_01_URL"),
    environ.get("DATABASE_CONNECTION_READ_02_URL"),
    environ.get("DATABASE_CONNECTION_READ_03_URL")
]

app.config['SECRET_KEY'] = environ.get('MASTER_KEY')

SERVER_NAME = environ.get("SERVER_NAME", environ.get("domain")).lstrip().rstrip()
ONION_NAME = environ.get("ONION_NAME", "")

class DomainMatcher(str):

    def __init__(self, *names):

        self.names = list(names)

    def __eq__(self, other):

        return other in self.names

app.config["SERVER_NAME"] = SERVER_NAME #DomainMatcher(SERVER_NAME, ONION_NAME)

# Matrix-backed chat - the homeserver is a separate, internal-only
# service (see docker-compose.yml's `synapse` service); the app talks to
# it server-side via an Application Service token (never exposed to the
# browser). MATRIX_PUBLIC_URL is what the browser's matrix-js-sdk client
# connects to directly for sync/send/E2EE - a different, publicly
# reachable homeserver URL than MATRIX_INTERNAL_URL.
app.config["MATRIX_INTERNAL_URL"] = environ.get("MATRIX_INTERNAL_URL", "http://synapse:8008")
app.config["MATRIX_PUBLIC_URL"] = environ.get("MATRIX_PUBLIC_URL", "http://localhost:8008")
app.config["MATRIX_SERVER_NAME"] = environ.get("MATRIX_SERVER_NAME", "chat.localhost")
app.config["MATRIX_AS_TOKEN"] = environ.get("MATRIX_AS_TOKEN")
app.config["MATRIX_HS_TOKEN"] = environ.get("MATRIX_HS_TOKEN")
app.config["MATRIX_BOT_LOCALPART"] = environ.get("MATRIX_BOT_LOCALPART", "ruqqus_bot")   # sender_localpart in synapse/ruqqus-appservice.yaml
app.config["MATRIX_USER_PREFIX"] = environ.get("MATRIX_USER_PREFIX", "ruqqus_")

#environ.get(
#    "domain", environ.get(
#        "SERVER_NAME", "")).lstrip().rstrip()


app.config["SHORT_DOMAIN"]=environ.get("SHORT_DOMAIN","").lstrip().rstrip()
app.config["SESSION_COOKIE_NAME"] = "session_ruqqus"
app.config["VERSION"] = _version
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
app.config["SESSION_COOKIE_SECURE"] = bool(int(environ.get("FORCE_HTTPS", 1)))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 365
app.config["SESSION_REFRESH_EACH_REQUEST"] = True


class _SessionInterface(SecureCookieSessionInterface):
    # An answer that everyone shares (a media file that is part of a post, kept by the CDN
    # in front of the site) must not carry one visitor's session cookie. A view marks such
    # an answer with g.skip_session_cookie (routes/media.py).
    def save_session(self, app, session, response):
        if g.get("skip_session_cookie"):
            return
        super().save_session(app, session, response)


app.session_interface = _SessionInterface()

app.config["FORCE_HTTPS"] = int(environ.get("FORCE_HTTPS", 1)) if ("localhost" not in app.config["SERVER_NAME"] and "127.0.0.1" not in app.config["SERVER_NAME"]) else 0
app.config["DISABLE_SIGNUPS"]=int(environ.get("DISABLE_SIGNUPS",0))

app.jinja_env.cache = {}

app.config["UserAgent"] = f"Content Aquisition for Porpl message board v{_version}."

if "localhost" in app.config["SERVER_NAME"]:
    app.config["CACHE_TYPE"] = "null"
else:
    app.config["CACHE_TYPE"] = environ.get("CACHE_TYPE", 'filesystem').lstrip().rstrip()

app.config["CACHE_DIR"] = environ.get("CACHE_DIR", "ruqquscache")

# captcha configs
app.config["HCAPTCHA_SITEKEY"] = environ.get("HCAPTCHA_SITEKEY","").lstrip().rstrip()
app.config["HCAPTCHA_SECRET"] = environ.get(
    "HCAPTCHA_SECRET","").lstrip().rstrip()
app.config["SIGNUP_HOURLY_LIMIT"]=int(environ.get("SIGNUP_HOURLY_LIMIT",0))

# antispam configs
app.config["SPAM_SIMILARITY_THRESHOLD"] = float(
    environ.get("SPAM_SIMILARITY_THRESHOLD", 0.5))
app.config["SPAM_SIMILAR_COUNT_THRESHOLD"] = int(
    environ.get("SPAM_SIMILAR_COUNT_THRESHOLD", 5))
app.config["SPAM_URL_SIMILARITY_THRESHOLD"] = float(
    environ.get("SPAM_URL_SIMILARITY_THRESHOLD", 0.1))
app.config["COMMENT_SPAM_SIMILAR_THRESHOLD"] = float(
    environ.get("COMMENT_SPAM_SIMILAR_THRESHOLD", 0.5))
app.config["COMMENT_SPAM_COUNT_THRESHOLD"] = int(
    environ.get("COMMENT_SPAM_COUNT_THRESHOLD", 5))

app.config["CACHE_REDIS_URL"] = environ.get(
    "REDIS_URL").rstrip().lstrip() if environ.get("REDIS_URL") else None
app.config["CACHE_DEFAULT_TIMEOUT"] = 60
app.config["CACHE_KEY_PREFIX"] = "flask_caching_"

app.config["S3_BUCKET"]=environ.get("S3_BUCKET_NAME","i.ruqqus.com").lstrip().rstrip()

app.config["REDIS_POOL_SIZE"]=int(environ.get("REDIS_POOL_SIZE", 10))

redispool=ConnectionPool(
    max_connections=app.config["REDIS_POOL_SIZE"],
    host=app.config["CACHE_REDIS_URL"][8:]
    ) if app.config["CACHE_TYPE"]=="redis" else None
app.config["CACHE_OPTIONS"]={'connection_pool':redispool} if app.config["CACHE_TYPE"]=="redis" else {}

app.config["READ_ONLY"]=bool(int(environ.get("READ_ONLY", False)))
app.config["BOT_DISABLE"]=bool(int(environ.get("BOT_DISABLE", False)))

app.config["TENOR_KEY"]=environ.get("TENOR_KEY",'').lstrip().rstrip()

import markdown
from markupsafe import Markup

def markdown_filter(text):
    html = markdown.markdown(text, extensions=['fenced_code', 'tables', 'sane_lists'])
    return Markup(html)

app.jinja_env.filters['markdown'] = markdown_filter

cache = Cache(app)
Compress(app)

# app.config["CACHE_REDIS_URL"]
app.config["RATELIMIT_STORAGE_URL"] = environ.get("REDIS_URL", "redis://redis").lstrip().rstrip()
app.config["RATELIMIT_KEY_PREFIX"] = "flask_limiting_"
app.config["RATELIMIT_ENABLED"] = True
app.config["RATELIMIT_DEFAULTS_DEDUCT_WHEN"]=lambda x:True
app.config["RATELIMIT_DEFAULTS_EXEMPT_WHEN"]=lambda:False
app.config["RATELIMIT_HEADERS_ENABLED"]=True


def limiter_key_func():
    return request.remote_addr


def member_limit_key():
    """The live features' limits (a call's or a Space's heartbeat, live comments and updates, the live stream) are
    per account, not per address: people sharing one address (a family, an office, a mobile network) would otherwise
    share one budget, and a busy Space asks often."""
    from flask import session
    user_id = session.get("user_id")
    return f"member:{user_id}" if user_id else request.remote_addr


limiter = Limiter(
    key_func=limiter_key_func,
    default_limits=["100/minute"],
    strategy="fixed-window",
    storage_uri=app.config["RATELIMIT_STORAGE_URL"],
    storage_options={'max_connections':100}
)
limiter.init_app(app) # Initialize the app with the limiter instance

# setup db
                         
#engines = {
#    "leader": create_engine(app.config['DATABASE_URL'], pool_size=pool_size, pool_use_lifo=True),
#    "followers": [create_engine(x, pool_size=pool_size, pool_use_lifo=True) for x in app.config['SQLALCHEMY_READ_URIS'] if x] if any(i for i in app.config['SQLALCHEMY_READ_URIS']) else [create_engine(app.config['DATABASE_URL'], pool_size=pool_size, pool_use_lifo=True)]
#}
_engine=create_engine(
    app.config['DATABASE_URL'],
    poolclass=QueuePool,
    pool_size=int(environ.get("PG_POOL_SIZE",10)),
    pool_use_lifo=True,
    pool_pre_ping=True
)


#These two classes monkey patch sqlalchemy

# class RoutingSession(Session):
#     def get_bind(self, mapper=None, clause=None):
#         try:
#             if self._flushing or request.method == "POST":
#                 return engines['leader']
#             else:
#                 return random.choice(engines['followers'])
#         except BaseException:
#             if self._flushing:
#                 return engines['leader']
#             else:
#                 return random.choice(engines['followers'])


def retry(f):

    def wrapper(self, *args, **kwargs):
        try:
            return f(self, *args, **kwargs)
        except (OperationalError, DatabaseError) as e:
            #self.session.rollback()
            raise(DatabaseOverload)
        except:
            self.session.rollback()
            return f(self, *args, **kwargs)

    wrapper.__name__=f.__name__
    return wrapper


class RetryingQuery(_Query):

    @retry
    def all(self):
        return super().all()

    @retry
    def count(self):
        return super().count()

    @retry
    def first(self):
        return super().first()

#db_session = scoped_session(sessionmaker(class_=RoutingSession))#, query_cls=RetryingQuery))
db_session=scoped_session(sessionmaker(bind=_engine, query_cls=RetryingQuery))


def _warn_pending_migrations():
    """Says at start-up which migrations this database lacks (scripts/migrate.py, audit D-02). Never stops the app."""
    from pathlib import Path
    folder = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
    try:
        with _engine.connect() as conn:
            done = {row[0] for row in conn.exec_driver_sql("SELECT name FROM schema_migrations")}
    except Exception as e:
        print(f"migrations could not be checked ({type(e).__name__}: {str(e).splitlines()[0][:200]}): "
              "run PYTHONPATH=. python scripts/migrate.py --status", flush=True)
        return
    missing = sorted(p.name for p in folder.glob("*.sql") if p.name not in done)
    if missing:
        print(f"{len(missing)} migrations not applied (scripts/migrate.py --apply): {', '.join(missing)}", flush=True)


if environ.get("CHECK_MIGRATIONS", "1") != "0":
    _warn_pending_migrations()

Base = declarative_base()


#set the shared redis cache for misc stuff

r=redis.Redis(
    host=app.config["CACHE_REDIS_URL"][8:], 
    decode_responses=True, 
    ssl_cert_reqs=None,
    connection_pool = redispool
    ) if app.config["CACHE_REDIS_URL"] else None


#import and bind chat function

local_ban_cache={}

#IP_BAN_CACHE_TTL = int(environ.get("IP_BAN_CACHE_TTL", 3600))
UA_BAN_CACHE_TTL = int(environ.get("UA_BAN_CACHE_TTL", 3600))



# import and bind all routing functions
import ruqqus.classes
from ruqqus.routes import *
import ruqqus.helpers.jinja2
import ruqqus.helpers.page_assets
ruqqus.helpers.page_assets.register(app)

#purge css from cache
cache.delete_memoized(ruqqus.routes.board_css)


@cache.memoize(UA_BAN_CACHE_TTL)
def get_useragent_ban_response(user_agent_str):
    """
    Given a user agent string, returns a tuple in the form of:
    (is_user_agent_banned, (insult, status_code))
    """
    #if request.path.startswith("/socket.io/"):
    #    return False, (None, None)

    result = g.db.query(
        ruqqus.classes.Agent).filter(
        ruqqus.classes.Agent.kwd.in_(
            user_agent_str.split())).first()
    if result:
        return True, (result.mock or "Follow the robots.txt, dumbass",
                      result.status_code or 418)
    return False, (None, None)

def drop_connection():

    g.db.close()
    gevent.getcurrent().kill()


# enforce https
@app.before_request
def before_request():

    if request.method.lower() != "get" and app.config["READ_ONLY"]:
        return jsonify({"error":f"{app.config['SITE_NAME']} is currently in read-only mode."}), 500

    if app.config["BOT_DISABLE"] and request.headers.get("X-User-Type")=="Bot":
        abort(503)



    if r and bool(r.get(f"ban_ip_{request.remote_addr}")):
        return jsonify({"error":"Too many requests. You are in time out for 1 hour. Rate limit is 100/min; less for authentication and content creation endpoints."}), 429

    g.db = db_session()

    # Check IP ban with transient-retry and better logging. If the DB
    # is temporarily unavailable we'll recreate the scoped session and
    # retry a few times before treating it as overload.
    addr = request.remote_addr
    import logging
    for attempt in range(3):
        try:
            if g.db.query(IP).filter_by(addr=addr).first():
                abort(503)
            break
        except DatabaseError as e:
            logging.warning("DB error checking IP ban (attempt %d): %s", attempt+1, repr(e))
            logging.debug(traceback.format_exc())
            try:
                db_session.remove()
                g.db = db_session()
            except Exception:
                logging.exception("Failed to recreate DB session during IP ban check")
            # small backoff
            time.sleep(0.2 * (attempt + 1))
    else:
        # all attempts failed
        logging.exception("DB error during IP ban check after retries")
        raise(DatabaseOverload)

    g.timestamp = int(time.time())

    flask_session.permanent = True

    ua_banned, response_tuple = get_useragent_ban_response(
        request.headers.get("User-Agent", "NoAgent"))
    if ua_banned and request.path != "/robots.txt":
        return response_tuple

    if app.config["FORCE_HTTPS"] and request.url.startswith(
            "http://") and "localhost" not in app.config["SERVER_NAME"]:
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)

    if not flask_session.get("session_id"):
        flask_session["session_id"] = secrets.token_hex(16)

    ua=request.headers.get("User-Agent","")
    if "CriOS/" in ua:
        g.system="ios/chrome"
    elif "Version/" in ua:
        g.system="android/webview"
    elif "Mobile Safari/" in ua:
        g.system="android/chrome"
    elif "Safari/" in ua:
        g.system="ios/safari"
    elif "Mobile/" in ua:
        g.system="ios/webview"
    else:
        g.system="other/other"

    #try:
    #    print(session.get('user_id'), request.remote_addr, request.method, request.path)
    #except:
    #    pass

    # g.db.begin_nested()


def log_event(name, link):

    x = requests.get(link)

    if x.status_code != 200:
        return

    text = f'> **{name}**\r> {link}'

    url = os.environ.get("DISCORD_WEBHOOK")
    headers = {"Content-Type": "application/json"}
    data = {"username": "ruqqus",
            "content": text
            }

    x = requests.post(url, headers=headers, json=data)
    print(x.status_code)


@app.after_request
def after_request(response):

    try:
        g.db.commit()
        g.db.close()
    except AttributeError:
        pass
    except BaseException:
        g.db.rollback()
        g.db.close()
        abort(500)

    response.headers.add('Access-Control-Allow-Headers',
                         "Origin, X-Requested-With, Content-Type, Accept, x-auth"
                         )
    # caching (audit X-02: every answer said "maxage=600", not a directive at all, on personalised pages too). A route
    # that set its own keeps it (/media/..., the .ics feeds, anything no-store); assets that aren't versioned are
    # kept ten minutes; every page is personal (the viewer, their filter, their form key), so it is private and
    # asked for again each time.
    if "Cache-Control" not in response.headers:
        if request.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=600"
        else:
            response.headers["Cache-Control"] = "private, no-cache"
    # a versioned asset (helpers/page_assets.asset_url, ?v= is a hash of its content) never changes at that
    # address: kept for a year, never asked for again
    if request.path.startswith("/assets/") and request.args.get("v") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers.add("Strict-Transport-Security", "max-age=31536000")
    # Google's consent banner for ads does not serve without a cross-origin referrer (support.google.com/adsense/
    # answer/14660912): the browsers' default policy only while ads are on (helpers/ads.py), otherwise none at all
    from ruqqus.helpers import ads
    response.headers.add("Referrer-Policy", "strict-origin-when-cross-origin" if ads.config() else "same-origin")
    # a file is what its Content-Type says, never what the browser guesses from its bytes (audit X-02)
    response.headers["X-Content-Type-Options"] = "nosniff"
    # microphone and camera only on the pages that hold a call or a Space (helpers/device_access.py)
    from ruqqus.helpers import device_access
    response.headers.add("Permissions-Policy", device_access.permissions_policy(request.path))
    if request.path.startswith("/embed/"):
        ancestors = "*"
    elif request.args.get("embed") and request.path.startswith(("/notifications", "/chat", "/composer")):
        # the side panels (assets/js/side_panels.js) show these pages in a frame of the
        # site itself; no other site may frame them
        response.headers.add("X-Frame-Options", "sameorigin")
        ancestors = "'self'"
    else:
        response.headers.add("X-Frame-Options",
                             "deny")
        ancestors = "'none'"
    # a Content-Security-Policy that hardens without breaking the inline handlers the pages still use (audit X-02):
    # no plugins, no <base> that would send relative links elsewhere, forms only to this site, framing as above. A
    # route that set its own (served media: helpers/media) keeps it.
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (f"object-src 'none'; base-uri 'self'; form-action 'self'; "
                                                       f"frame-ancestors {ancestors}")

    # signups - hit discord webhook
    # if request.method == "POST" and response.status_code in [
    #         301, 302] and request.path == "/signup":
    #     link = f'https://{app.config["SERVER_NAME"]}/@{request.form.get("username")}'
    #     thread = gevent.spawn(
    #         lambda: log_event(
    #             name="Account Signup", link=link))
    #     thread.start()

    # req_stop = time.time()

    # try:
    #     req_time=req_stop - g.timestamp
    #     site_performance(req_time)
    # except AttributeError:
    #     pass

    return response


@app.route("/<path:path>", subdomain="www")
def www_redirect(path):

    return redirect(f"https://{app.config['SERVER_NAME']}/{path}")

#engines["leader"].dispose()
#for engine in engines["followers"]:
#    engine.dispose()
