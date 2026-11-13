from flask import (
    request, g, abort, jsonify, make_response, redirect, render_template
)
from flask import session as flask_session
from ruqqus.helpers.wrappers import *
from ruqqus.helpers.session_helpers import *
from ruqqus.classes.custom_errors import *
from ruqqus.helpers import circle_guard, circle_store, circles
from ruqqus.helpers import consent as consent_rules
from ruqqus.helpers import qr_login
from ruqqus.helpers.i18n_runtime import tr
from urllib.parse import quote, urlencode
import time
import jinja2
from ruqqus.__main__ import app, r, cache
import gevent

# Errors


def error_wrapper(f):

    def wrapper(*args, **kwargs):

        resp = make_response(f(*args, **kwargs))
        # g.db may not be set for requests that bypass DB (static files). Be
        # defensive when rolling back/closing.
        try:
            if hasattr(g, 'db') and g.db is not None:
                g.db.rollback()
                g.db.close()
        except Exception:
            pass
        return resp

    wrapper.__name__=f.__name__
    return wrapper



def _viewer():
    """The signed-in viewer for a 4xx page, so the page does not look signed out (it used to be drawn with v=None).
    Whatever the request had changed is rolled back first: an error page writes nothing, and a session the error left
    broken would otherwise fail the page itself. 5xx pages keep v=None: the database may be what failed."""
    try:
        if getattr(g, "db", None) is not None:
            g.db.rollback()
    except Exception:
        return None
    return getattr(g, "v", None)


@app.errorhandler(401)
@error_wrapper
def error_401(e):

    path = request.path
    qs = urlencode(dict(request.args))
    argval = quote(f"{path}?{qs}", safe='')
    output = f"/login?redirect={argval}"

    if request.path.startswith(("/api/v1/", "/api/v2/")):
        return jsonify({"error": tr("401 Not Authorized")}), 401
    if request.method == "GET" and request.args.get("embed") and request.path.startswith(("/chat", "/notifications")):
        # a frame of the site (the guild Chat tab's, a side panel's) whose sign-in ended meanwhile (another browser logged out
        # everywhere, a new password): the sign-in page may not be framed, so a redirect there left the frame blank. It says so
        # itself, and its link signs in from the page around it (default.html gives a frame <base target="_top">)
        back = quote(_frame_parent_path(), safe='')
        return render_template("message.html", v=None, title=tr("Signed out"),
                               error=tr("This browser was signed out. Sign in again to carry on."),
                               link=f"/login?redirect={back}", link_text=tr("Sign in")), 401
    return redirect(output)


def _frame_parent_path():
    """The page a frame sits in (its Referer, when it is this site), as a path to come back to after signing in; else the front page."""
    from urllib.parse import urlsplit
    from ruqqus.helpers.redirects import same_site
    parts = urlsplit(request.referrer or "")
    if parts.netloc != request.host:
        return "/"
    return same_site(parts.path + (f"?{parts.query}" if parts.query else ""))

@app.errorhandler(PaymentRequired)
@error_wrapper
@api(error_handler=True)
def error_402(e):
    return {"html": lambda: (render_template('errors/402.html', v=_viewer()), 402),
            "api": lambda: (jsonify({"error": tr("402 Payment Required")}), 402)
            }

@app.errorhandler(CircleOnly)
@error_wrapper
@api(error_handler=True)
def error_circle_only(e):
    """A post made for an audience, asked for by someone outside it (helpers/circle_guard.py, helpers/get.py). A page
    gets a gate that names the owner and nothing of the post; everything else gets a plain 404. It never names a custom
    circle: only the paid circles the post is for, which anyone may join from the owner's profile anyway."""

    def gate():
        post = e.post
        paid = []
        if post.audience == circles.CIRCLES:
            ids = circle_guard.post_circles(g.db, [post.id]).get(post.id, ())
            paid = [row for row in (circle_store.get(g.db, i) for i in sorted(ids))
                    if row is not None and row.kind == circles.PAID and row.price_coins > 0]
        response = make_response(render_template("errors/circle_gate.html", v=getattr(g, "v", None), owner=post.author,
                                                 audience=post.audience, paid=paid), 403)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    return {"html": gate,
            "api": lambda: (jsonify({"error": tr("404 Not Found")}), 404)
            }


@app.errorhandler(403)
@error_wrapper
@api(error_handler=True)
def error_403(e):
    return {"html": lambda: (render_template('errors/403.html', v=_viewer()), 403),
            "api": lambda: (jsonify({"error": tr("403 Forbidden")}), 403)
            }


@app.errorhandler(404)
@error_wrapper
@api(error_handler=True)
def error_404(e):
    return {"html": lambda: (render_template('errors/404.html', v=_viewer()), 404),
            "api": lambda: (jsonify({"error": tr("404 Not Found")}), 404)
            }


@app.errorhandler(405)
@error_wrapper
@api(error_handler=True)
def error_405(e):
    return {"html": lambda: (render_template('errors/405.html', v=_viewer()), 405),
            "api": lambda: (jsonify({"error": tr("405 Method Not Allowed")}), 405)
            }


@app.errorhandler(409)
@error_wrapper
@api(error_handler=True)
def error_409(e):
    return {"html": lambda: (render_template('errors/409.html', v=_viewer()), 409),
            "api": lambda: (jsonify({"error": tr("409 Conflict")}), 409)
            }


@app.errorhandler(413)
@error_wrapper
@api(error_handler=True)
def error_413(e):
    return {"html": lambda: (render_template('errors/413.html', v=_viewer()), 413),
            "api": lambda: (jsonify({"error": tr("413 Request Payload Too Large")}), 413)
            }


@app.errorhandler(422)
@error_wrapper
@api(error_handler=True)
def error_422(e):
    return {"html": lambda: (render_template('errors/422.html', v=_viewer()), 422),
            "api": lambda: (jsonify({"error": tr("422 Unprocessable Entity")}), 422)
            }


@app.errorhandler(429)
@error_wrapper
@api(error_handler=True)
def error_429(e):

    ip = request.remote_addr
    # the limiter can refuse before the site's own before_request opens the database: the sign-in in the cookie wasn't checked, so
    # the answer writes no cookie (helpers/sign_in_ends.py: a copy of an ended one must not be refreshed)
    if getattr(g, "db", None) is None:
        g.skip_session_cookie = True

    # limited per code, so nobody else's requests can use them up and these say nothing about the address: thirty of them would
    # put everyone behind it in time out (routes/qr_login_pages.py). A script that ignores the body, so no page is drawn.
    if request.path in qr_login.PER_CODE_PATHS:
        return "", 429

    #get recent violations
    if r:
        count_429s = r.get(f"429_count_{ip}")
        if not count_429s:
            count_429s=0
        else:
            count_429s=int(count_429s)

        count_429s+=1

        r.set(f"429_count_{ip}", count_429s)
        r.expire(f"429_count_{ip}", 60)

        #if you exceed 30x 429 without a 60s break, you get IP banned for 1 hr:
        if count_429s>=30:
            try:
                print("triggering IP ban", request.remote_addr, flask_session.get("user_id"), flask_session.get("history"))
            except:
                pass
            
            r.set(f"ban_ip_{ip}", int(time.time()))
            r.expire(f"ban_ip_{ip}", 3600)
            return "", 429

    # the answer to the video question (routes/consent_pages.py): a script that ignores the body, on a route that asks no database
    # and draws no page. It has counted above like any other.
    if request.path == consent_rules.ANSWER_PATH:
        return "", 429

    return {"html": lambda: (render_template('errors/429.html', v=_viewer()), 429),
        "api": lambda: (jsonify({"error": tr("429 Too Many Requests")}), 429)
        }


@app.errorhandler(451)
@error_wrapper
@api(error_handler=True)
def error_451(e):
    return {"html": lambda: (render_template('errors/451.html', v=_viewer()), 451),
            "api": lambda: (jsonify({"error": tr("451 Unavailable For Legal Reasons")}), 451)
            }


@app.errorhandler(500)
@error_wrapper
@api(error_handler=True)
def error_500(e):
    try:
        if hasattr(g, 'db') and g.db is not None:
            g.db.rollback()
    except Exception:
        pass

    return {"html": lambda: (render_template('errors/500.html', v=None), 500),
            "api": lambda: (jsonify({"error": tr("500 Internal Server Error")}), 500)
            }

@app.errorhandler(503)
@error_wrapper
@api(error_handler=True)
def error_503(e):
    try:
        if hasattr(g, 'db') and g.db is not None:
            g.db.rollback()
    except Exception:
        pass

    return {"html": lambda: (render_template('errors/503.html'), 503),
            "api": lambda: (jsonify({"error": tr("503 Service Unavailable")}), 503)
            }

@app.route("/error/<eid>", methods=["GET"])
@admin_level_required(3)
def error_all_preview(eid, v):
    """A preview of an error page, for admins. Only a page that exists (a bad id was a 500)."""
    if not (eid.isascii() and eid.isdigit()):
        abort(404)
    try:
        return render_template(f"errors/{int(eid)}.html", v=v)
    except jinja2.exceptions.TemplateNotFound:
        abort(404)



@app.errorhandler(DatabaseOverload)
@error_wrapper
@auth_desired
@api(error_handler=True)
def error_database_overload(e, v):
    return{"html": lambda: (render_template('errors/overload.html', v=v), 500),
           "api": lambda: (jsonify({"error": tr("500 Internal Server Error (database overload)")}), 500)
           }
