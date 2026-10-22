import mistletoe

from ruqqus.classes import *
from flask import g
from .markdown import *
from .sanitize import *


def send_notification(user, text):

    with CustomRenderer() as renderer:
        text_html = renderer.render(mistletoe.Document(text))

    text_html = sanitize(text_html, linkgen=True)#, noimages=True)

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
                         body_html=text_html
                         )
    g.db.add(new_aux)

    # in the same commit as the message (bug hunt M4: it was added after the commit, so a job that didn't commit
    # again lost it, e.g. the last "your membership lapsed" notice of each run of renew_circles.py)
    notif = Notification(comment_id=new_comment.id,
                         user_id=user.id)
    g.db.add(notif)
    g.db.commit()


def send_to_many(user_ids, text):
    """One notice to many people (the bells, helpers/bell.py): ONE system message and a notification row for each
    person, in one commit, instead of a message per person. The text must say nothing any one of them may not read
    (the bells name who did something and link to it, never a title)."""
    user_ids = sorted({int(x) for x in user_ids if x})
    if not user_ids:
        return 0

    with CustomRenderer() as renderer:
        text_html = renderer.render(mistletoe.Document(text))
    text_html = sanitize(text_html, linkgen=True)

    new_comment = Comment(author_id=1, parent_submission=None, distinguish_level=6, is_offensive=False)
    g.db.add(new_comment)
    g.db.flush()
    g.db.add(CommentAux(id=new_comment.id, body=text, body_html=text_html))
    g.db.add_all([Notification(comment_id=new_comment.id, user_id=uid) for uid in user_ids])
    g.db.commit()
    return len(user_ids)
