"""Who may share a post or a comment: copy its link, send it in a chat, forward it to a guild or repost it.

* A **public** post can be shared by anyone, and so can every comment on it.
* Anything else only by **its author**: a post made for a Circle (any audience, Circle guilds included) or a post that
  is in a private guild and was never public there (`Submission.is_public`), and a comment on such a post (by the
  comment's own author). Admins are not an exception: they may read it, but sharing it is not theirs to do.

What was made public stays easy to share: a post forwarded while public keeps being public, as a guild that turns
private keeps the posts it already had public (a post's public flag is set when it is made). What was not made public
is not convenient to share; a screenshot still is, and nothing can stop that.

Pure: it only reads the objects it is given. The share controls are drawn only where these say so
(partials/post_actions.html, comments.html), and the routes that share refuse the rest with `REFUSAL` (forward and
repost of a post or a comment). Sending in a chat has no server side to refuse: the message is written by the
browser's encrypted chat client, so the menu item is the control (tests/test_sharing.py).
"""

REFUSAL = "Only the author can share this: it isn't public."


def _viewer(v):
    """The viewer, or None for a visitor (a template may pass jinja's Undefined)."""
    from jinja2 import Undefined
    return None if v is None or isinstance(v, Undefined) else v


def may_share(post, v):
    """May `v` share this post? Anyone when it is public; otherwise only its author."""
    if post is None:
        return False
    if post.is_public:
        return True
    v = _viewer(v)
    return bool(v and v.id == post.author_id)


def may_share_comment(comment, v):
    """May `v` share this comment? Anyone when the post it is on is public; otherwise only the comment's author."""
    if comment is None:
        return False
    post = getattr(comment, "post", None)
    if post is None or post.is_public:
        return True
    v = _viewer(v)
    return bool(v and v.id == comment.author_id)


def post_refusal(post, v):
    return None if may_share(post, v) else REFUSAL


def comment_refusal(comment, v):
    return None if may_share_comment(comment, v) else REFUSAL


def narrowing_note(own_guilds, others, reposted):
    """What the author is told before a public post moves to a Circle (routes/circles.py `circle_post_audience`), or
    None when nothing was shared. What was public stays where others put it: copies other people forwarded stay in
    their guilds, and reposts follow the post itself, so they show it only to people who may see it now. The forward
    copies the author made are theirs, and are taken down with it (`own_guilds`: the guilds' names)."""
    parts = []
    if own_guilds:
        names = ["+" + name for name in own_guilds]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        parts.append(f"The {'copy' if len(names) == 1 else 'copies'} you forwarded to {listed} will be taken down.")
    if others:
        parts.append(f"{'The copy' if others == 1 else f'The {others} copies'} other people forwarded to guilds will stay there.")
    if reposted:
        parts.append("Reposts of it will show it only to people who can see it.")
    return " ".join(parts) or None
