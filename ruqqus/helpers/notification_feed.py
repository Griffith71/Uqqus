"""The notifications page's data: the viewer's notifications as bundled rows, a page of rows at a time (the rules for what
each row is and what bundles are in helpers/notification_groups.py).

* **The rows** are the viewer's `notifications`, the comments and the posts together, newest first, filtered exactly as
  every list of them is (the builders on `User`: removed and deleted targets, the word filter, mutes, blocks), for one
  tab: all, unread, posts, comments, mentions or system.
* **A page is a page of rows, not of notifications**: the notifications are read newest first in chunks of CHUNK (at most
  CAP in all) until there are PER_PAGE rows past the page asked for, so a bundle never straddles two pages. A row's place
  never moves when older notifications join it, so the pages stay put.
* **What was new is kept**: each item carries the `read` it had before this page marked what it shows as read (as the
  old lists did, so the bell empties), and the page tints the new ones.
* **The menus' state is batched**: the follows and bells of the accounts named, the mutes, the guilds' memberships, bells
  and mutes, and each post's follow state, in a handful of queries for the whole page, never one per row.
"""
import json
import re
from dataclasses import dataclass, field

from sqlalchemy import bindparam, func, text

from ruqqus.classes import Notification, User
from ruqqus.helpers import anonymity, post_excerpt, post_follows
from ruqqus.helpers import notification_groups as ng
from ruqqus.helpers.get import get_comments, get_posts

PER_PAGE = 20
CHUNK = 100
CAP = 500
EXCERPT = 220
TABS = ("all", "unread", "posts", "comments", "mentions", "system")
_LINK = re.compile(r'<a\s[^>]*href="([^"]+)"')


@dataclass
class Page:
    groups: list = field(default_factory=list)
    next_exists: bool = False


def tab_of(path, all_flag=False):
    """The tab a notifications address stands for. The bare /notifications is the unread list, as before; ?all=true
    (which the account menu's inbox link has always carried) is the all list."""
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    if tail in ("all", "posts", "mentions", "system"):
        return tail
    if tail == "replies":
        return "comments"
    return "all" if all_flag else "unread"


def _rows(v, tab, before, limit):
    """[(notification id, comment id, post id, read)] below `before`, newest first, from both kinds of notification."""
    out = []
    if tab != "posts":
        q = v._notification_comment_query("unread" if tab == "unread" else tab)
        q = q.with_entities(Notification.id, Notification.comment_id, Notification.read)
        if before:
            q = q.filter(Notification.id < before)
        out += [(r[0], r[1], None, r[2]) for r in q.order_by(Notification.id.desc()).limit(limit)]
    if tab in ("all", "unread", "posts"):
        q = v._notification_post_query(all_=tab != "unread")
        q = q.with_entities(Notification.id, Notification.submission_id, Notification.read)
        if before:
            q = q.filter(Notification.id < before)
        out += [(r[0], None, r[1], r[2]) for r in q.order_by(Notification.id.desc()).limit(limit)]
    out.sort(key=lambda r: -r[0])
    return out[:limit]


def _actor(item, v):
    hidden = anonymity.identity_hidden(item, v)
    who = anonymity.author_of(item, v)
    return ng.Actor(name="" if hidden else (who.username or ""), avatar=who.profile_url or "",
                    link="" if hidden else (who.permalink or ""), anonymous=hidden)


def _mentions(html, username):
    """Does this comment name the viewer (a link to their profile, as the mention markup makes it)?"""
    return bool(html) and f'/@{username.lower()}"' in html.lower()


def _first_link(html):
    found = _LINK.search(html or "")
    return found.group(1) if found else ""


def _item(v, nid, read, comment, post):
    """One classified notification, from the comment or post it points at."""
    if comment is not None:
        system = comment.author_id == 1
        aux = getattr(comment, "comment_aux", None)
        msgid = getattr(aux, "notice_msgid", None) if system and aux is not None else None
        try:
            params = json.loads(getattr(aux, "notice_params", None) or "{}") if msgid else {}
        except ValueError:
            params = {}
        parent = comment.parent_comment if comment.level > 1 else None
        target = comment.post
        state = getattr(target, "_follow_state", None) if target is not None else None
        kind, key = ng.classify(source="comment", system=system, msgid=msgid, params=params, level=comment.level,
                                parent_id=comment.parent_comment_id, parent_by_viewer=bool(parent and parent.author_id == v.id),
                                post_id=comment.parent_submission, post_state=state, item_id=nid,
                                mentions_viewer=not system and _mentions(comment.body_html, v.username))
        item = ng.Item(id=nid, read=bool(read), kind=kind, key=key, created_utc=comment.created_utc, ref=comment,
                       post_id=comment.parent_submission, post_ref=target.base36id if target is not None else None, post_state=state)
        if system:
            item.link = _first_link(comment.body_html)
            item.account = str(params.get("name") or "") if kind == ng.BELL_ACCOUNT else ""
            item.guild = str(params.get("guild") or "") if kind == ng.BELL_GUILD else ""
            item.bundle_link = f"/+{item.guild}" if item.guild else (f"/@{item.account}" if item.account else item.link)
        else:
            item.actor = _actor(comment, v)
            item.excerpt = post_excerpt.excerpt(comment.body_html, limit=EXCERPT)[0]
            item.link = comment.permalink
            item.bundle_link = (parent.permalink if kind == ng.COMMENT_ON_COMMENT and parent is not None
                                else target.permalink if kind == ng.COMMENT_ON_POST and target is not None else comment.permalink)
        return item
    item = ng.Item(id=nid, read=bool(read), kind=ng.POST, key=("account", post.author_id), created_utc=post.created_utc, ref=post,
                   post_id=post.id, post_ref=post.base36id)
    item.actor = _actor(post, v)
    item.account = item.actor.name
    item.excerpt = post_excerpt.excerpt(post.body_html, limit=EXCERPT)[0]
    item.link = post.permalink
    item.bundle_link = item.actor.link or post.permalink
    return item


def _accounts(names):
    """{lower-case name: User} for the accounts a page names."""
    names = sorted({n.lower() for n in names if n})
    if not names:
        return {}
    from flask import g
    return {u.username.lower(): u for u in g.db.query(User).filter(func.lower(User.username).in_(names)).all()}


def _state(db, v, groups):
    """What the viewer has set for the accounts and guilds the rows name (helpers/notification_groups.controls)."""
    accounts = {i.account.lower() for grp in groups for i in grp.items if i.account}
    accounts |= {a.identity for grp in groups for a in grp.actors if a.name and not a.anonymous}
    guilds = {i.guild.lower() for grp in groups for i in grp.items if i.guild}
    state = {"follow": {}, "mute": set(), "guild": {}}
    if accounts:
        names = sorted(accounts)
        q = text("""SELECT lower(u.username), f.get_notifs FROM follows f JOIN users u ON u.id = f.target_id
                    WHERE f.user_id = :v AND lower(u.username) IN :names""").bindparams(bindparam("names", expanding=True))
        for name, bell in db.execute(q, {"v": v.id, "names": names}):
            state["follow"][name] = {"following": True, "bell": bool(bell)}
        q = text("""SELECT lower(u.username) FROM usermutes m JOIN users u ON u.id = m.target_id
                    WHERE m.user_id = :v AND lower(u.username) IN :names""").bindparams(bindparam("names", expanding=True))
        state["mute"] = {row[0] for row in db.execute(q, {"v": v.id, "names": names})}
    if guilds:
        names = sorted(guilds)
        q = text("""SELECT lower(b.name), s.get_notifs FROM subscriptions s JOIN boards b ON b.id = s.board_id
                    WHERE s.user_id = :v AND s.is_active = :yes AND lower(b.name) IN :names""").bindparams(bindparam("names", expanding=True))
        for name, bell in db.execute(q, {"v": v.id, "yes": True, "names": names}):
            state["guild"][name] = {"member": True, "bell": bool(bell), "mute": False}
        q = text("""SELECT lower(b.name) FROM boardblocks x JOIN boards b ON b.id = x.board_id
                    WHERE x.user_id = :v AND lower(b.name) IN :names""").bindparams(bindparam("names", expanding=True))
        for (name,) in db.execute(q, {"v": v.id, "names": names}):
            state["guild"].setdefault(name, {"member": False, "bell": False})["mute"] = True
    return state


def page(db, v, tab, number=1):
    """The `number`th page of the viewer's rows for `tab`; marks everything on it read (after noting what was new), unless the
    viewer is a limited QR sign-in."""
    if tab not in TABS:
        tab = "all"
    need = PER_PAGE * number
    comments, posts, items = {}, {}, []
    before, rows_read = None, 0
    while rows_read < CAP:
        rows = _rows(v, tab, before, CHUNK)
        if not rows:
            break
        rows_read += len(rows)
        before = rows[-1][0]
        fresh_c = [c for _, c, _, _ in rows if c and c not in comments]
        fresh_p = [p for _, _, p, _ in rows if p and p not in posts]
        if fresh_c:
            comments.update({c.id: c for c in get_comments(fresh_c, v=v, load_parent=True)})
        if fresh_p:
            posts.update({p.id: p for p in get_posts(fresh_p, v=v, sort="new")})
        post_follows.attach(db, list({c.post.id: c.post for c in comments.values() if c.post is not None}.values()), v)
        for nid, cid, pid, read in rows:
            comment, post = comments.get(cid), posts.get(pid)
            if (cid and comment is None) or (pid and post is None):
                continue                                  # gone from under the list (removed since): nothing to draw
            items.append(_item(v, nid, read, comment, post))
        if len(ng.group(items)) > need or len(rows) < CHUNK:        # enough rows past this page, or no more notifications
            break
    everything = ng.group(items)
    shown = everything[need - PER_PAGE:need]
    # the viewer's accounts named by bell notices get their avatar
    people = _accounts(i.account for grp in shown for i in grp.items if i.kind == ng.BELL_ACCOUNT)
    for grp in shown:
        for i in grp.items:
            if i.kind == ng.BELL_ACCOUNT and i.actor is None:
                user = people.get(i.account.lower())
                i.actor = ng.Actor(name=user.username if user else i.account, avatar=user.profile_url if user else "",
                                   link=f"/@{user.username if user else i.account}")
    state = _state(db, v, shown)
    for grp in shown:
        grp.menu = ng.controls(grp, state)
    ids = [i.id for grp in shown for i in grp.items if not i.read]
    # a limited QR sign-in reads them and leaves them new: the notice of that sign-in has to stay in the member's bell (helpers/qr_login.py)
    if ids and not v.qr_limited:
        db.query(Notification).filter(Notification.id.in_(ids)).update({"read": True}, synchronize_session=False)
        db.commit()
    return Page(groups=shown, next_exists=len(everything) > need)
