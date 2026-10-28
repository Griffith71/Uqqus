import json

import mistletoe

from ruqqus.classes import *
from flask import g
from .markdown import *
from .sanitize import *


def _render(text):
    with CustomRenderer() as renderer:
        text_html = renderer.render(mistletoe.Document(text))
    return sanitize(text_html, linkgen=True)


def _notice(message, params):
    """(the English text, its HTML, the message, its values as JSON) of a notice made with notify / notify_many."""
    from ruqqus.helpers.i18n_runtime import site_name
    values = dict(params)
    values.setdefault("site", site_name())
    text = message % values
    return text, _render(text), message, json.dumps(values, sort_keys=True)


def send_notification(user, text, notice=None):
    """A system notice to one person. `text` is markdown, kept as written (English); `notice` is (message, values
    JSON) when it was made with notify, so each reader sees it in their interface language (notice_html)."""

    text_html = _render(text)

    new_comment = Comment(author_id=1,
                          # body=text,
                          # body_html=text_html,
                          parent_submission=None,
                          distinguish_level=6,
                          is_offensive=False
                          )
    g.db.add(new_comment)

    g.db.flush()

    new_aux = CommentAux(id=new_comment.id,
                         body=text,
                         body_html=text_html,
                         notice_msgid=notice[0] if notice else None,
                         notice_params=notice[1] if notice else None,
                         )
    g.db.add(new_aux)

    # in the same commit as the message (bug hunt M4: it was added after the commit, so a job that didn't commit
    # again lost it, e.g. the last "your membership lapsed" notice of each run of renew_circles.py)
    notif = Notification(comment_id=new_comment.id,
                         user_id=user.id)
    g.db.add(notif)
    g.db.commit()


def notify(user, message, **params):
    """A system notice marked for translation: `message` is an English markdown message marked with N_
    ("Your post [%(title)s](%(link)s) was pinned."), `params` its values. Stored in English, as every notice is, with
    the message and values, so the notifications page draws it in each reader's language."""
    text, _html, msgid, values = _notice(message, params)
    send_notification(user, text, notice=(msgid, values))


def send_to_many(user_ids, text, notice=None):
    """One notice to many people (the bells, helpers/bell.py): ONE system message and a notification row for each
    person, in one commit, instead of a message per person. The text must say nothing any one of them may not read
    (the bells name who did something and link to it, never a title). With `notice` (notify_many), each reader sees it
    in their own language, from the one shared message."""
    user_ids = sorted({int(x) for x in user_ids if x})
    if not user_ids:
        return 0

    text_html = _render(text)

    new_comment = Comment(author_id=1, parent_submission=None, distinguish_level=6, is_offensive=False)
    g.db.add(new_comment)
    g.db.flush()
    g.db.add(CommentAux(id=new_comment.id, body=text, body_html=text_html,
                        notice_msgid=notice[0] if notice else None, notice_params=notice[1] if notice else None))
    g.db.add_all([Notification(comment_id=new_comment.id, user_id=uid) for uid in user_ids])
    g.db.commit()
    return len(user_ids)


def notify_many(user_ids, message, **params):
    """send_to_many with a message marked for translation (as notify)."""
    text, _html, msgid, values = _notice(message, params)
    return send_to_many(user_ids, text, notice=(msgid, values))
