"""Shared drafts and collaborative posts (rules in helpers/collab_rules.py, the text engine in helpers/collab_ot.py,
who typed what in helpers/collab_authorship.py, SQL in helpers/collab_store.py).

- **A shared draft is for its current authors only**: every page and every API call starts with `_doc_or_404`, and a
  draft someone isn't an author of is a 404. Its keystroke record (`collab_ops`) never leaves its authors.
- **Live co-editing**: `POST /api/collab/<id>/ops` commits one op stamped with the caller (`collab_store.commit_op`)
  and publishes the new revision number only on the draft's live topic (`collab:<id>`, guarded: authors only); each
  browser then asks `GET /api/collab/<id>/ops?after=` for what it hasn't seen (asked again of the author check).
- **Decisions are proposals** (`_settle`): add or remove an author, change the rules, delete the published post, and
  publish when the rules say publishing is voted on. The proposer's yes is recorded at once; a proposal passes, fails
  or expires by the draft's own rules, counted again at every vote and by the recompute job.
- **Publishing** goes through the site's own post machinery: the first time through the real `POST /api/vue/submit`
  in this process as the publishing author (`_post_as`), later through `posts.apply_post_edit` with the publisher as
  the one who edits. Each publish is a public revision (`collab_publishes`); "Who wrote what" and the revisions are
  public to whoever may see the post. The other authors are the post's accepted co-authors, so bylines, profiles and
  Following work unchanged.
- **Forks** copy a public collaborative post's latest published revision with its record of who typed what.
"""
import json
import secrets
import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.get import *
from ruqqus.helpers.base36 import base36decode, base36encode
from ruqqus.helpers.security import generate_hash
from ruqqus.classes import *
from ruqqus.__main__ import app, limiter, member_limit_key, r

from ruqqus.helpers import collab_authorship as authorship
from ruqqus.helpers import collab_ot as ot
from ruqqus.helpers import collab_rules as rules
from ruqqus.helpers import collab_store as store
from ruqqus.helpers import collab_view, coauthors, live_bus
from ruqqus.helpers.visibility import user_hidden

DAY = 86400
PRESENCE_SECONDS = 45
TINTS = 8


def _now():
    return int(time.time())


def _error(message, status=400, **extra):
    return jsonify(dict({"error": message}, **extra)), status


def _doc_or_404(doc_id, v):
    """The draft when `v` is one of its current authors, else a 404 (never says whether it exists)."""
    row = store.doc(g.db, doc_id)
    if row is None or v is None or not store.is_author(g.db, doc_id, v.id):
        abort(404)
    return row


def _may_write(v):
    return not (v.is_suspended or v.is_bot or v.is_deleted)


def _users(ids):
    ids = {int(i) for i in ids if i is not None}
    return {u.id: u for u in g.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}


def _name_of(user, v):
    if user is None or user.is_deleted:
        return {"name": "Deleted account", "handle": "", "link": None}
    if user_hidden(user, v):
        return {"name": "Hidden name", "handle": "", "link": None}
    return {"name": user.shown_name, "handle": user.username, "link": user.permalink}


def _changed(doc_id):
    """Tell the draft's open pages that its authors, proposals or rules changed (they redraw the panel)."""
    live_bus.publish(r, live_bus.topic_channel(f"collab:{doc_id}"), "collab.changed", {})


def _tell(user_ids, message):
    """Notifications, best effort: a failure to tell never undoes a decision (send_notification commits)."""
    from ruqqus.helpers.alerts import send_notification
    for user in _users(user_ids).values():
        if user.is_deleted:
            continue
        try:
            send_notification(user, message)
        except Exception:
            g.db.rollback()


def _guard_collab_topic(v, key):
    return bool(v) and key.isascii() and key.isdigit() and store.doc(g.db, int(key)) is not None and \
        store.is_author(g.db, int(key), v.id)


live_bus.register_topic("collab", _guard_collab_topic)


# --- the list, a new draft, the editor ------------------------------------------------------------------------------

@app.route("/collab", methods=["GET"])
@auth_required
def collab_list(v):
    docs = store.docs_of(g.db, v.id)
    invites = store.invitations_for(g.db, v.id, _now() - rules.INVITE_DAYS * DAY)
    invite_docs = {p[0]: store.doc(g.db, p[1]) for p in invites}
    return render_template("collab/list.html", v=v, docs=docs, invites=invites, invite_docs=invite_docs,
                           authors_of={d[0]: len(store.author_ids(g.db, d[0])) for d in docs})


@app.route("/collab/new", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_new(v):
    """A new shared draft with its creator as the one author (a plain form: it works without script)."""
    if not _may_write(v):
        abort(403)
    now = _now()
    if store.made_since(g.db, v.id, now - DAY) >= rules.DOCS_PER_DAY:
        return _error(f"You can start {rules.DOCS_PER_DAY} shared drafts a day.", 429)
    doc_id = store.create(g.db, v.id, now)
    g.db.commit()
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"url": f"/collab/{doc_id}"})
    return redirect(f"/collab/{doc_id}")


def _panel_context(doc, v):
    """Everything the editor's side panel draws: authors (with whether they count), proposals with their votes,
    the rules in words, the public revisions, the fork it came from."""
    now = _now()
    doc_id = doc[0]
    doc_rules = store.rules_of(doc)
    author_rows = store.authors(g.db, doc_id)
    people = _users([a[0] for a in author_rows])
    window = doc_rules["inactive_after_days"]
    authors_out = []
    for uid, active, joined in author_rows:
        authors_out.append(dict(_name_of(people.get(uid), v), id=uid, me=(uid == v.id),
                                counted=rules.is_active(active, doc_rules, now)))
    proposals_out = []
    rows = store.proposals(g.db, doc_id, limit=30)
    subjects = _users([p[3] for p in rows] + [p[5] for p in rows])
    for p in rows:
        votes = store.votes(g.db, p[0])
        payload = store.payload_of(p)
        proposals_out.append({
            "id": p[0], "kind": p[2], "subject": _name_of(subjects.get(p[3]), v) if p[3] else None,
            "by": _name_of(subjects.get(p[5]), v), "created_utc": p[6], "closes_utc": p[7], "outcome": p[9],
            "invitee_answer": p[10], "yes": sum(1 for x in votes.values() if x), "no": sum(1 for x in votes.values() if not x),
            "mine": votes.get(v.id), "rules": rules.describe(payload.get("rules")) if p[2] == rules.RULES else None,
            "mine_to_withdraw": p[5] == v.id and p[9] == rules.OPEN,
        })
    publishes = store.publishes(g.db, doc_id)
    fork_post = get_post(base36encode(doc[10]), v=v, graceful=True) if doc[10] else None
    post = get_post(base36encode(doc[7]), v=v, graceful=True) if doc[7] else None
    return {
        "doc": doc, "authors": authors_out, "proposals": proposals_out, "rules_lines": rules.describe(doc_rules),
        "doc_rules": doc_rules, "publishes": publishes, "fork_post": fork_post, "post": post,
        "inactive_label": rules.INACTIVE_LABELS[window], "decision_labels": rules.DECISION_LABELS,
        "inactive_labels": rules.INACTIVE_LABELS, "publish_labels": rules.PUBLISH_LABELS,
        "proposal_days": rules.PROPOSAL_DAY_CHOICES, "shares": _shares(store.runs_of(doc)["body"] + store.runs_of(doc)["title"], v, doc_id),
        "unpublished": doc[4] != doc[8] or not doc[7],
    }


@app.route("/collab/<int:doc_id>", methods=["GET"])
@auth_required
def collab_editor(doc_id, v):
    doc = _doc_or_404(doc_id, v)
    ctx = _panel_context(doc, v)
    return render_template("collab/editor.html", v=v, title_max=rules.TITLE_MAX, body_max=rules.BODY_MAX, **ctx)


@app.route("/collab/<int:doc_id>/panel", methods=["GET"])
@auth_required
def collab_panel(doc_id, v):
    """The side panel alone, redrawn by the editor's script when the live topic says something changed."""
    doc = _doc_or_404(doc_id, v)
    return render_template("collab/panel.html", v=v, **_panel_context(doc, v))


# --- live co-editing ------------------------------------------------------------------------------------------------

def _ops_json(rows):
    return [{"r": rev, "u": uid, "f": field, "o": op} for rev, uid, field, op in rows]


@app.route("/api/collab/<int:doc_id>/state", methods=["GET"])
@auth_required
def collab_state(doc_id, v):
    """The draft as it is now (a browser that fell behind reloads from here)."""
    doc = _doc_or_404(doc_id, v)
    return jsonify({"revision": doc[4], "title": doc[2], "body": doc[3]})


@app.route("/api/collab/<int:doc_id>/ops", methods=["GET"])
@auth_required
def collab_ops_after(doc_id, v):
    doc = _doc_or_404(doc_id, v)
    after = request.args.get("after", type=int)
    if after is None or after < 0 or after > doc[4] or doc[4] - after > rules.RESYNC_AFTER:
        return _error("Reload the draft.", 409, resync=True)
    return jsonify({"revision": doc[4], "ops": _ops_json(store.ops_after(g.db, doc_id, after, rules.RESYNC_AFTER))})


@app.route("/api/collab/<int:doc_id>/ops", methods=["POST"])
@limiter.limit(f"{rules.OPS_PER_MINUTE}/minute", key_func=member_limit_key)
@is_not_banned
@validate_formkey
def collab_send_op(doc_id, v):
    """One op on one field, made on revision `base`. Answers {own: the op's revision or null when it changed
    nothing, revision: the draft's revision now}."""
    _doc_or_404(doc_id, v)
    if not _may_write(v):
        abort(403)
    field = request.form.get("field", "")
    base = request.form.get("base", type=int)
    client_key = (request.form.get("key") or "")[:32]
    try:
        op = json.loads(request.form.get("op") or "")
    except ValueError:
        return _error("That edit couldn't be read.", 400, resync=True)
    if base is None:
        return _error("That edit couldn't be read.", 400, resync=True)
    now = _now()
    try:
        own = store.commit_op(g.db, doc_id, v.id, field, base, op, now, client_key=client_key)
    except store.Resync:
        g.db.rollback()
        return _error("Reload the draft.", 409, resync=True)
    except ot.OpError:
        g.db.rollback()
        return _error("That edit didn't fit the draft.", 400, resync=True)
    except rules.Refused as e:
        g.db.rollback()
        return _error(str(e), 400, resync=True)
    g.db.commit()
    revision = store.doc(g.db, doc_id)[4]
    if own is not None:
        live_bus.publish(r, live_bus.topic_channel(f"collab:{doc_id}"), "collab.rev", {"revision": revision})
    return jsonify({"own": own, "revision": revision})


@app.route("/api/collab/<int:doc_id>/presence", methods=["POST"])
@limiter.limit("60/minute", key_func=member_limit_key)
@auth_required
@validate_formkey
def collab_presence(doc_id, v):
    """An author's page says it is open (every 20 s) or typing; answers who else is. Kept only in Redis, for two
    minutes; it changes nothing anyone else can read but the draft's own authors, so it needs no form key."""
    _doc_or_404(doc_id, v)
    now = _now()
    here, typing = [], []
    if r is not None:
        try:
            r.zadd(f"collab:here:{doc_id}", {str(v.id): now})
            if request.values.get("typing") == "1":
                r.zadd(f"collab:typing:{doc_id}", {str(v.id): now})
            for key in (f"collab:here:{doc_id}", f"collab:typing:{doc_id}"):
                r.zremrangebyscore(key, "-inf", now - PRESENCE_SECONDS)
                r.expire(key, 120)
            here = [int(x) for x in r.zrange(f"collab:here:{doc_id}", 0, -1)]
            typing = [int(x) for x in r.zrangebyscore(f"collab:typing:{doc_id}", now - 6, "+inf")]
        except Exception:
            here, typing = [], []
    current = set(store.author_ids(g.db, doc_id))
    people = _users([u for u in here if u in current and u != v.id])
    return jsonify({"here": [dict(_name_of(people[u], v), typing=u in typing) for u in here if u in people]})


# --- proposals and votes -----------------------------------------------------------------------------------------

def _settle(doc, proposal, actor, now):
    """Counts a proposal again by the draft's rules and, the first time it is decided, does what it says. Returns
    its outcome. The caller commits."""
    if proposal[9] != rules.OPEN:
        return proposal[9]
    doc_id, kind = doc[0], proposal[2]
    doc_rules = store.rules_of(doc)
    exclude = proposal[3] if kind == rules.REMOVE else None
    voters = rules.counted(store.voters(g.db, doc_id), doc_rules, now, exclude=exclude)
    outcome = rules.tally(kind, doc_rules, voters, store.votes(g.db, proposal[0]))
    if outcome == rules.OPEN and now >= proposal[7]:
        outcome = rules.EXPIRED
    if outcome == rules.OPEN or not store.decide(g.db, proposal[0], outcome, now):
        return rules.OPEN if outcome == rules.OPEN else proposal[9]
    authors_now = store.author_ids(g.db, doc_id)
    if outcome == rules.PASSED:
        payload = store.payload_of(proposal)
        if kind == rules.ADD:
            g.db.commit()
            _tell([proposal[3]], f"You're invited to write a shared draft with others. [See the invitation](/collab/invite/{proposal[0]})")
        elif kind == rules.REMOVE:
            store.remove_author(g.db, doc_id, proposal[3], now)
            live_bus.revoke(r, proposal[3], f"collab:{doc_id}")       # their open pages stop hearing it (C-07)
            _sync_coauthors(store.doc(g.db, doc_id))
            g.db.commit()
            _tell([proposal[3]], "The other authors of a shared draft removed you as an author. What you wrote stays credited to you.")
        elif kind == rules.RULES:
            store.set_rules(g.db, doc_id, payload.get("rules"), now)
        elif kind == rules.DELETE:
            # the deletion is the proposer's (the history and the deletion log name who asked for it)
            post = g.db.query(Submission).filter_by(id=doc[7]).first() if doc[7] else None
            proposer = g.db.query(User).filter_by(id=proposal[5]).first()
            if post and not post.deleted_utc and proposer is not None:
                from ruqqus.routes.posts import delete_post_as
                delete_post_as(post, proposer)
        elif kind == rules.PUBLISH:
            proposer = g.db.query(User).filter_by(id=proposal[5]).first()
            if proposer is not None:
                g.db.commit()
                error = _publish(store.doc(g.db, doc_id), proposer, payload, now)
                if error:
                    _tell([proposal[5]], f"Your shared draft couldn't be published: {error} [Open the draft](/collab/{doc_id})")
    g.db.commit()
    names = {rules.ADD: "add an author", rules.REMOVE: "remove an author", rules.RULES: "change the rules",
             rules.DELETE: "delete the post", rules.PUBLISH: "publish"}
    verdict = {rules.PASSED: "passed", rules.FAILED: "didn't pass", rules.EXPIRED: "closed undecided"}[outcome]
    # the outcome is told to whoever made or voted on the proposal (audit C-06: it went to every author); the panel
    # shows it to the rest
    took_part = set(store.votes(g.db, proposal[0])) | {proposal[5]}
    _tell([u for u in authors_now if u != actor.id and u in took_part],
          f"A proposal to {names[kind]} in a shared draft {verdict}. [Open the draft](/collab/{doc_id})")
    _changed(doc_id)
    return outcome


def _settle_all(doc, actor, now):
    for p in store.proposals(g.db, doc[0], open_only=True):
        _settle(store.doc(g.db, doc[0]), store.proposal(g.db, doc[0], p[0]), actor, now)


@app.route("/api/collab/<int:doc_id>/propose", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_propose(doc_id, v):
    """kind add_author | remove_author (`username`), change_rules (the rules fields), delete_post."""
    doc = _doc_or_404(doc_id, v)
    if not _may_write(v):
        abort(403)
    now = _now()
    if store.proposals_since(g.db, v.id, now - DAY) >= rules.PROPOSALS_PER_DAY:
        return _error(f"You can make {rules.PROPOSALS_PER_DAY} proposals a day.", 429)
    if store.open_by(g.db, doc_id, v.id) >= rules.OPEN_PER_AUTHOR:
        return _error(f"You can have {rules.OPEN_PER_AUTHOR} open proposals in a draft at a time.", 429)
    kind = request.values.get("kind", "")
    doc_rules = store.rules_of(doc)
    subject, payload = None, {}
    try:
        if kind == rules.ADD:
            name = rules.parse_name(request.values.get("username"))
            candidate = get_user(name, graceful=True)
            current = store.author_ids(g.db, doc_id)
            people = _users(current)
            refusal = rules.add_refusal(
                name=name, candidate=candidate, authors_now=len(current), already=bool(candidate and candidate.id in current),
                blocked_with_any=bool(candidate and any(u.any_block_exists(candidate) for u in people.values())))
            if refusal:
                return _error(refusal)
            subject = candidate.id
        elif kind == rules.REMOVE:
            name = rules.parse_name(request.values.get("username"))
            candidate = get_user(name, graceful=True)
            refusal = rules.remove_refusal(subject_is_author=bool(candidate and store.is_author(g.db, doc_id, candidate.id)),
                                           subject_is_me=bool(candidate and candidate.id == v.id))
            if refusal:
                return _error(refusal)
            subject = candidate.id
        elif kind == rules.RULES:
            new_rules = rules.rules_from_form(request.values)
            if new_rules == doc_rules:
                return _error("Those are already the rules.")
            payload = {"rules": new_rules}
        elif kind == rules.DELETE:
            if not doc[7]:
                return _error("This draft hasn't been published.")
        else:
            return _error("Choose what to propose.")
    except rules.Refused as e:
        return _error(str(e))
    if store.open_like(g.db, doc_id, kind, subject):
        return _error("There's already an open proposal like this one.", 409)
    pid = store.propose(g.db, doc_id, kind, v.id, now, rules.closes_at(doc_rules, now), subject=subject, payload=payload)
    store.vote(g.db, pid, v.id, True, now)
    g.db.commit()
    outcome = _settle(store.doc(g.db, doc_id), store.proposal(g.db, doc_id, pid), v, now)
    if outcome == rules.OPEN:
        counted = rules.counted(store.voters(g.db, doc_id), doc_rules, now,
                                exclude=subject if kind == rules.REMOVE else None)
        _tell([u for u in counted if u != v.id],                     # only the authors whose vote counts (C-06)
              f"@{v.username} made a proposal in a shared draft you write: your vote counts. [Open the draft](/collab/{doc_id})")
        _changed(doc_id)
    return jsonify({"ok": True, "outcome": outcome})


@app.route("/api/collab/<int:doc_id>/proposals/<int:pid>/vote", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_vote(doc_id, pid, v):
    doc = _doc_or_404(doc_id, v)
    proposal = store.proposal(g.db, doc_id, pid)
    if proposal is None:
        abort(404)
    if proposal[9] != rules.OPEN:
        return _error("This proposal is closed.", 409)
    if proposal[2] == rules.REMOVE and proposal[3] == v.id:
        return _error("You don't vote on your own removal.", 403)
    choice = request.values.get("vote")
    if choice not in ("yes", "no"):
        return _error("Vote yes or no.")
    now = _now()
    store.vote(g.db, pid, v.id, choice == "yes", now)
    g.db.commit()
    outcome = _settle(store.doc(g.db, doc_id), store.proposal(g.db, doc_id, pid), v, now)
    _changed(doc_id)
    return jsonify({"ok": True, "outcome": outcome})


@app.route("/api/collab/<int:doc_id>/proposals/<int:pid>/withdraw", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_withdraw(doc_id, pid, v):
    _doc_or_404(doc_id, v)
    proposal = store.proposal(g.db, doc_id, pid)
    if proposal is None or proposal[5] != v.id:
        abort(404)
    if not store.decide(g.db, pid, rules.WITHDRAWN, _now()):
        return _error("This proposal is closed.", 409)
    g.db.commit()
    _changed(doc_id)
    return jsonify({"ok": True})


# --- joining and leaving ----------------------------------------------------------------------------------------

def _invite_or_404(pid, v):
    proposal = store.proposal_by_id(g.db, pid)
    if (proposal is None or proposal[2] != rules.ADD or proposal[3] != v.id or proposal[9] != rules.PASSED
            or proposal[10] or not rules.invite_open(proposal[8], _now())):
        abort(404)
    doc = store.doc(g.db, proposal[1])
    if doc is None:
        abort(404)
    return proposal, doc


@app.route("/collab/invite/<int:pid>", methods=["GET"])
@auth_required
def collab_invite(pid, v):
    proposal, doc = _invite_or_404(pid, v)
    people = _users(store.author_ids(g.db, doc[0]))
    return render_template("collab/invite.html", v=v, proposal=proposal, doc=doc,
                           authors=[_name_of(u, v) for u in people.values()], rules_lines=rules.describe(store.rules_of(doc)))


@app.route("/api/collab/invite/<int:pid>/<answer>", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_invite_answer(pid, answer, v):
    proposal, doc = _invite_or_404(pid, v)
    if answer not in ("accept", "decline"):
        abort(404)
    now = _now()
    if answer == "accept":
        current = store.author_ids(g.db, doc[0])
        people = _users(current)
        refusal = rules.add_refusal(name=v.username, candidate=v, authors_now=len(current), already=v.id in current,
                                    blocked_with_any=any(u.any_block_exists(v) for u in people.values()))
        if refusal:
            return _error("You can't join this draft now.", 409)
    if not store.answer_invite(g.db, pid, "accepted" if answer == "accept" else "declined"):
        return _error("This invitation was already answered.", 409)
    if answer == "accept":
        store.add_author(g.db, doc[0], v.id, now, proposal_id=pid)
        _sync_coauthors(store.doc(g.db, doc[0]))
    g.db.commit()
    _settle_all(store.doc(g.db, doc[0]), v, now)
    _changed(doc[0])
    url = f"/collab/{doc[0]}" if answer == "accept" else "/collab"
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"ok": True, "url": url})
    return redirect(url)


@app.route("/api/collab/<int:doc_id>/leave", methods=["POST"])
@auth_required
@validate_formkey
def collab_leave(doc_id, v):
    """Anyone can stop being an author at any time; what they typed stays credited to them. A draft nobody writes
    any more is closed (its published post stays)."""
    doc = _doc_or_404(doc_id, v)
    now = _now()
    store.remove_author(g.db, doc_id, v.id, now)
    live_bus.revoke(r, v.id, f"collab:{doc_id}")
    _sync_coauthors(store.doc(g.db, doc_id))
    if not store.author_ids(g.db, doc_id):
        # nobody writes it any more: the draft closes and its post becomes an ordinary post of the profile it is
        # on (edited and deleted by that account, as any post), never one nobody can change (audit C-01)
        store.close(g.db, doc_id, now)
        g.db.commit()
        collab_view.forget()
    else:
        g.db.commit()
        _settle_all(store.doc(g.db, doc_id), v, now)
        _changed(doc_id)
    return jsonify({"ok": True, "url": "/collab"})


# --- publishing ---------------------------------------------------------------------------------------------------

def _post_as(user, path, data):
    """POST to one of the site's own routes in this process, as `user` (a one-off session), from the address and
    country of the request that asked: the draft scheduler's way (scripts/publish_scheduled.py), so every posting rule
    applies as if they had pressed Post themselves. It runs in a greenlet of its own: database sessions are kept per
    greenlet and so is Flask's context, so the inner request can't close or replace this request's session or `g`."""
    import gevent

    user_id, nonce = user.id, user.login_nonce
    headers = {"User-Agent": request.headers.get("User-Agent", "shared draft")[:200]}
    if request.headers.get("cf-ipcountry"):
        headers["cf-ipcountry"] = request.headers["cf-ipcountry"]
    address = request.remote_addr or "127.0.0.1"

    def run():
        client = app.test_client()
        session_id = secrets.token_hex(16)
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["login_nonce"] = nonce
            session["session_id"] = session_id
        form = dict(data, formkey=generate_hash(f"{session_id}+{user_id}+{nonce}"))
        response = client.post(path, data=form, headers=headers, environ_base={"REMOTE_ADDR": address})
        return response.status_code, response.get_json(silent=True) or {}

    return gevent.spawn(run).get(timeout=60)


def _sync_coauthors(doc):
    """The published post's accepted co-authors are exactly the draft's current authors (other than whoever's
    profile it is on), so its byline, profile listings and Following follow the authors."""
    if doc is None or not doc[7]:
        return
    post = g.db.query(Submission).filter_by(id=doc[7]).first()
    if post is None:
        return
    current = set(store.author_ids(g.db, doc[0])) - {post.author_id}
    rows = {row.user_id: row for row in g.db.query(PostCoauthor).filter_by(post_id=post.id).all()}
    now = _now()
    for uid, row in rows.items():
        if uid not in current:
            g.db.delete(row)
    for uid in current:
        row = rows.get(uid)
        if row is None:
            g.db.add(PostCoauthor(post_id=post.id, user_id=uid, invited_by_id=post.author_id,
                                  status=coauthors.ACCEPTED, created_utc=now, accepted_utc=now))
        elif row.status != coauthors.ACCEPTED:
            row.status, row.accepted_utc = coauthors.ACCEPTED, now
            g.db.add(row)
    g.db.flush()
    coauthors.forget()


def _snapshot(doc):
    return {"title": doc[2], "body": doc[3], "runs": store.runs_of(doc), "revision": doc[4]}


def _publish(doc, publisher, snap, now):
    """Puts a snapshot of the draft on the post (making the post the first time). Returns an error message or None.
    The caller has committed: the first publish runs a whole request of its own."""
    title = (snap.get("title") or "").strip()
    try:
        rules.check_title(title)
    except rules.Refused as e:
        return str(e)
    body = snap.get("body") or ""
    post = g.db.query(Submission).filter_by(id=doc[7]).first() if doc[7] else None
    if post is not None and post.is_banned:
        return "This post was removed, so it can't be changed."
    if post is None or post.deleted_utc:
        status, data = _post_as(publisher, "/api/vue/submit", {"title": title, "body": body})
        if status != 200 or not data.get("id"):
            return str(data.get("error") or f"The server answered {status}.")[:300]
        post_id = base36decode(data["id"])
        store.set_published(g.db, doc[0], post_id, snap["revision"])
        g.db.commit()
        collab_view.forget()
        post = g.db.query(Submission).filter_by(id=post_id).first()
    else:
        from ruqqus.routes.posts import apply_post_edit
        answer = apply_post_edit(post, post, publisher, {"title": title, "body": body}, None,
                                 media_owner_ids=store.everyone(g.db, doc[0]))
        if isinstance(answer, tuple) and answer[1] >= 400:
            g.db.rollback()
            data = answer[0].get_json(silent=True) or {}
            return str(data.get("error") or "The post refused the change.")[:300]
        if publisher.is_suspended:
            return "Your account can't publish right now."
        g.db.expire_all()
        post = g.db.query(Submission).filter_by(id=doc[7]).first()
        store.set_published(g.db, doc[0], post.id, snap["revision"])
    # every author's own uploads in the text are the post's (each can only claim their own)
    from ruqqus.helpers.media import attach as media_attach, safety as media_safety
    attached = media_attach.sync(g.db, store.everyone(g.db, doc[0]), (post.url or "", post.body or ""), submission_id=post.id)
    store.add_publish(g.db, doc[0], snap["revision"], title, body, snap["runs"], publisher.id, now)
    _sync_coauthors(store.doc(g.db, doc[0]))
    g.db.commit()
    media_safety.scan_later(attached)
    _changed(doc[0])
    return None


@app.route("/api/collab/<int:doc_id>/publish", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_publish(doc_id, v):
    """Publish the working copy: at once when any author may, or as a proposal when the rules put it to a vote."""
    _doc_or_404(doc_id, v)
    # the draft's row stays locked until this publish commits: two authors pressing Publish at once make one post,
    # and the second sees it (audit C-02: it used to make two)
    doc = store.doc(g.db, doc_id, lock=True)
    if not _may_write(v):
        abort(403)
    now = _now()
    if doc[7] and doc[4] == doc[8]:
        post = g.db.query(Submission).filter_by(id=doc[7]).first()
        if post is not None and not post.deleted_utc:
            return _error("Nothing has changed since the last publish.", 409)
    snap = _snapshot(doc)
    if store.rules_of(doc)["publish"] == rules.VOTE:
        if store.open_like(g.db, doc_id, rules.PUBLISH):
            return _error("A proposal to publish is already open.", 409)
        try:
            rules.check_title(snap["title"])
        except rules.Refused as e:
            return _error(str(e))
        pid = store.propose(g.db, doc_id, rules.PUBLISH, v.id, now, rules.closes_at(store.rules_of(doc), now), payload=snap)
        store.vote(g.db, pid, v.id, True, now)
        g.db.commit()
        outcome = _settle(store.doc(g.db, doc_id), store.proposal(g.db, doc_id, pid), v, now)
        if outcome == rules.OPEN:
            _tell([u for u in store.author_ids(g.db, doc_id) if u != v.id],
                  f"@{v.username} asked to publish a shared draft you write: your vote counts. [Open the draft](/collab/{doc_id})")
        return jsonify({"ok": True, "proposal": True, "outcome": outcome})
    error = _publish(doc, v, snap, now)
    if error:
        return _error(error)
    doc = store.doc(g.db, doc_id)
    return jsonify({"ok": True, "url": f"/post/{base36encode(doc[7])}"})


# --- forks ------------------------------------------------------------------------------------------------------

def _readable_collab_post(pid, v):
    """(post, draft row) for a collaborative post `v` may read, else a 404."""
    from ruqqus.routes.live_comments import readable_post
    post = readable_post(pid, v)
    if post is None:
        abort(404)
    primary_id = post.repost_id or post.id
    doc = store.doc_for_post(g.db, primary_id)
    if doc is None:
        abort(404)
    primary = post if primary_id == post.id else get_post(base36encode(primary_id), v=v, graceful=True)
    if primary is None:
        abort(404)
    return primary, doc


@app.route("/collab/fork/<pid>", methods=["GET"])
@auth_required
def collab_fork_page(pid, v):
    post, doc = _readable_collab_post(pid, v)
    return render_template("collab/fork.html", v=v, p=post, doc=doc, may_fork=post.is_public)


@app.route("/collab/fork/<pid>", methods=["POST"])
@is_not_banned
@validate_formkey
def collab_fork(pid, v):
    """A new shared draft from a public collaborative post's latest published revision, with who typed what kept."""
    post, doc = _readable_collab_post(pid, v)
    if not _may_write(v):
        abort(403)
    if not post.is_public:
        return _error("Only a public post can be forked.", 403)
    now = _now()
    if store.forks_since(g.db, v.id, now - DAY) >= rules.FORKS_PER_DAY:
        return _error(f"You can fork {rules.FORKS_PER_DAY} posts a day.", 429)
    latest = store.latest_publish(g.db, doc[0])
    if latest is None:
        abort(404)
    doc_id = store.create(g.db, v.id, now, title=latest[3], body=latest[4], runs=latest[5],
                          forked_from={"doc_id": doc[0], "post_id": post.id, "publish_id": latest[0]})
    g.db.commit()
    return redirect(f"/collab/{doc_id}")


# --- who wrote what, revisions (public once published) ----------------------------------------------------------------

def _shares(runs, v, doc_id):
    """[{name..., percent, tint, from_original}] for a legend; tints are fixed classes, by share."""
    ever = set(store.everyone(g.db, doc_id)) if doc_id else set()
    out = []
    shares = authorship.shares(runs)
    people = _users([u for u, _, _ in shares])
    for index, (uid, units, percent) in enumerate(shares):
        out.append(dict(_name_of(people.get(uid), v), id=uid, percent=percent, tint=index % TINTS,
                        from_original=bool(doc_id) and uid not in ever))
    return out


def _tinted(text_, runs, legend):
    tint = {p["id"]: p for p in legend}
    return [(tint.get(uid, {"tint": None, "name": "Unknown", "handle": ""}), piece) for uid, piece in authorship.pieces(text_, runs)]


@app.route("/post/<pid>/written_by", methods=["GET"])
@auth_desired
def collab_written_by(pid, v):
    post, doc = _readable_collab_post(pid, v)
    number = request.args.get("revision", type=int)
    row = store.publish_row(g.db, doc[0], number) if number else store.latest_publish(g.db, doc[0])
    if row is None:
        abort(404)
    legend = _shares(row[5]["body"] + row[5]["title"], v, doc[0])
    return render_template("collab/written_by.html", v=v, p=post, doc=doc, row=row, legend=legend,
                           title_pieces=_tinted(row[3], row[5]["title"], legend),
                           body_pieces=_tinted(row[4], row[5]["body"], legend), draft=False)


@app.route("/collab/<int:doc_id>/written_by", methods=["GET"])
@auth_required
def collab_draft_written_by(doc_id, v):
    """Who wrote what in the working copy, for its authors (it is private until published)."""
    doc = _doc_or_404(doc_id, v)
    runs = store.runs_of(doc)
    legend = _shares(runs["body"] + runs["title"], v, doc_id)
    return render_template("collab/written_by.html", v=v, p=None, doc=doc, row=None, legend=legend,
                           title_pieces=_tinted(doc[2], runs["title"], legend),
                           body_pieces=_tinted(doc[3], runs["body"], legend), draft=True)


@app.route("/post/<pid>/revisions", methods=["GET"])
@auth_desired
def collab_revisions(pid, v):
    post, doc = _readable_collab_post(pid, v)
    rows = store.publishes(g.db, doc[0])
    people = _users([row[4] for row in rows])
    fork_post = get_post(base36encode(doc[10]), v=v, graceful=True) if doc[10] else None
    return render_template("collab/revisions.html", v=v, p=post, doc=doc, rows=rows, diff=None,
                           publishers={uid: _name_of(u, v) for uid, u in people.items()},
                           fork_post=fork_post, fork_count=store.fork_count(g.db, post.id))


@app.route("/post/<pid>/revisions/<int:number>", methods=["GET"])
@auth_desired
def collab_revision(pid, number, v):
    """One public revision against the one before it (+/- lines, as the wiki's diff)."""
    from ruqqus.helpers import guild_wiki
    post, doc = _readable_collab_post(pid, v)
    row = store.publish_row(g.db, doc[0], number)
    if row is None:
        abort(404)
    before = store.publish_row(g.db, doc[0], number - 1)
    lines = guild_wiki.diff(((before[3] + "\n\n" + before[4]) if before else ""), row[3] + "\n\n" + row[4])
    people = _users([row[6]])
    return render_template("collab/revisions.html", v=v, p=post, doc=doc, rows=None, diff=lines, row=row, before=before,
                           publishers={uid: _name_of(u, v) for uid, u in people.items()}, fork_post=None, fork_count=0)


# --- for the post page and menus ----------------------------------------------------------------------------------

def collab_of(p, v=None):
    """What the post page says of a collaborative post (None for any other post): its draft, how many public
    revisions, the post it was forked from (only one this reader may open), how many forks, whether `v` writes it."""
    if not collab_view.is_collab(p):
        return None
    memo = g.__dict__.setdefault("_collab_of", {})
    key = (p.repost_id or p.id, v.id if v else None)
    if key not in memo:
        doc = store.doc_for_post(g.db, key[0])
        if doc is None:
            memo[key] = None
        else:
            fork_post = get_post(base36encode(doc[10]), v=v, graceful=True) if doc[10] else None
            if fork_post is not None and (fork_post.deleted_utc or fork_post.is_banned or fork_post.in_private_guild_for(v)):
                fork_post = None
            memo[key] = {"doc_id": doc[0], "revisions": len(store.publishes(g.db, doc[0])), "fork_post": fork_post,
                         "forks": store.fork_count(g.db, key[0]),
                         "mine": bool(v) and doc[14] == 0 and store.is_author(g.db, doc[0], v.id)}
    return memo[key]


app.jinja_env.globals.update(collab_of=collab_of, is_collab_post=collab_view.is_collab)


# --- the recompute job -----------------------------------------------------------------------------------------

def settle_open_proposals(db_session):
    """Counts every open proposal again (authors become inactive, proposals reach their end). Returns how many were
    decided. Run by scripts/recomputes.py."""
    decided = 0
    now = _now()
    with app.test_request_context("/"):
        g.db = db_session()
        try:
            system = g.db.query(User).filter_by(id=1).first()
            for pid, doc_id in store.open_proposal_ids(g.db):
                doc = store.doc(g.db, doc_id)
                proposal = store.proposal(g.db, doc_id, pid)
                if doc is None:
                    store.decide(g.db, pid, rules.EXPIRED, now)
                    g.db.commit()
                    continue
                if proposal is not None and system is not None and _settle(doc, proposal, system, now) != rules.OPEN:
                    decided += 1
            g.db.commit()
        finally:
            g.db.close()
    return decided
