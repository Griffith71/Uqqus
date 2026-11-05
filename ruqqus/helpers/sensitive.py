"""Sensitive content and the two discovery feeds, All and For You.

"Sensitive" on this site is one general test, **anchored on film and game ratings** rather than a list (the owner, 2026-11-05: "the
definition of sensitive needs to be highly generalised"): a film or game with the same content would be rated 15, R or 16+, or given a
content warning (`DEFINITION`, written once here and drawn everywhere by the Jinja global `sensitive_definition()`: the Rules, the
composers, Settings, the guild setting). It is a heads-up, not a rule break; content made mainly to be consumed as sexual, violent or
grotesque material is still against the rules. Ordinary grief or recovery posts do not earn a 15 or R, so they are not Sensitive.
A child-locked account (helpers/age_declaration.py) can't see, mark or post it. A post (`submissions.is_sensitive`), a guild (`boards.is_sensitive`) and an account (`users.is_sensitive`) can be
sensitive.

* All and For You leave sensitive content out unless the viewer chose to see it (`users.all_shows_sensitive`, Settings
  > Content). A visitor never sees it there, and neither does a Child-filter viewer (the word filter hides sensitive
  content everywhere for them: helpers/visibility.py). Anyone can still go to it: the guild, the profile, Following,
  search and a direct link don't apply this rule, because going there is a choice (the same line mutes draw).
* A post by a sensitive account is left out only when it is not anonymous. Leaving out an anonymous post because of who
  wrote it would tell readers which anonymous posts belong to a sensitive account (the promise hide_muted makes). A
  sensitive account's new posts are marked sensitive when written (`marks_post`), anonymous or not, so they leave by
  rule (a) and the mark on the post says nothing about who wrote it.
* The owner of an account and the guildmasters of a guild switch it on and off, unless an admin forced it on
  (`sensitive_forced`): then only an admin can unlock it (`owner_refusal`). A post the same: its author and the guild's
  guildmasters, unless an admin forced it, which is kept on the post a copy copies (`Submission.sensitive_lock`).

Rules here are pure; the two subqueries are functions so tests can stand in for them (tests/test_sensitive.py).
"""
from sqlalchemy import and_, or_, select

from ruqqus.helpers import anonymity
from ruqqus.helpers.i18n_runtime import N_, tr_lazy

FORCED = tr_lazy("An admin marked this sensitive, so it can't be switched off here.")

# the one definition (see the docstring); its words are the owner's and are hand-checked in every language
DEFINITION = N_("Mark a post Sensitive if a film or game with the same content would be rated 15, R or 16+, or given a content "
                "warning. That means strong sexual content or nudity, graphic violence or injury, strong drug use, self-harm or "
                "suicide, or very distressing or frightening scenes. If you're not sure, mark it Sensitive.")


def shows_sensitive(v):
    """Does this viewer see sensitive content in All and For You? Only a member who chose to, not on the Child filter."""
    if v is None or not getattr(v, "all_shows_sensitive", False):
        return False
    from ruqqus.helpers.visibility import CHILD, viewer_level
    return viewer_level(v) < CHILD


def owner_refusal(forced, wanted):
    """Why an owner (an account's member, a guild's guildmasters) may not set the flag to `wanted`, or None. Switching it
    on is always allowed; switching it off is not while an admin forced it."""
    if forced and not wanted:
        return FORCED
    return None


def marks_post(chosen, author):
    """A new post is marked sensitive when its author chose to, or when the author's account is sensitive."""
    return bool(chosen) or bool(getattr(author, "is_sensitive", False))


def sensitive_board_ids():
    from ruqqus.classes.boards import Board
    return select(Board.id).where(Board.is_sensitive.is_(True))


def sensitive_user_ids():
    from ruqqus.classes.user import User
    return select(User.id).where(User.is_sensitive.is_(True))


def hide_sensitive(query, v, model):
    """`query` (over Submission, or anything with is_sensitive, board_id, author_id and is_anonymous) without the
    sensitive posts, unless the viewer chose to see them. The viewer's own posts always stay."""
    if shows_sensitive(v):
        return query
    keep = and_(
        model.is_sensitive.isnot(True),
        model.board_id.notin_(sensitive_board_ids()),
        or_(model.is_anonymous.is_(True), model.author_id.notin_(sensitive_user_ids())),
    )
    if v is not None:
        keep = or_(anonymity.own(model, v), keep)
    return query.filter(keep)
