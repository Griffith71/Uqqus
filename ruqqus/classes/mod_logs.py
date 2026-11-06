from ruqqus.helpers.base36 import *
from ruqqus.helpers.security import *
from sqlalchemy import *
from sqlalchemy.orm import relationship
from ruqqus.__main__ import Base, cache, app
from .mix_ins import *
from babel.support import LazyProxy
from ruqqus.helpers.i18n_runtime import N_, msg, tr, trp
from ruqqus.helpers import action_log
from sqlalchemy import event
import logging
import re
import time


_VALUE = re.compile(r"%\((\w+)\)s")


def _filled(template, values):
    """An entry's English template (marked with N_ in ACTIONTYPES) in the reader's language, filled with its values;
    the English when the translation doesn't name the same values."""
    translated = msg(template)
    if sorted(_VALUE.findall(translated)) != sorted(_VALUE.findall(template)):
        translated = template
    try:
        return translated % values
    except (KeyError, ValueError, TypeError):
        return template % values


class ModAction(Base, Stndrd, Age_times):
    __tablename__ = "modactions"
    id = Column(BigInteger, primary_key=True)

    user_id = Column(Integer, ForeignKey("users.id"))
    board_id = Column(Integer, ForeignKey("boards.id"))
    kind = Column(String(32))
    target_user_id = Column(Integer, ForeignKey("users.id"), default=0)
    target_submission_id = Column(Integer, ForeignKey("submissions.id"), default=0)
    target_comment_id = Column(Integer, ForeignKey("comments.id"), default=0)
    #targetLodge = Column(Integer, ForeignKey("lodges.id"), default=0)
    #targetRule = Column(Boolean, ForeignKey("rules.id"), default=False)
    _note=Column(String(256), default=None)
    created_utc = Column(Integer, default=0)


    user = relationship("User", lazy="joined", primaryjoin="User.id==ModAction.user_id")
    target_user = relationship("User", lazy="joined", primaryjoin="User.id==ModAction.target_user_id")
    board = relationship("Board", lazy="joined")
    #target_lodge = relationship("Lodge", lazy="joined")
    #target_rule = relationship("Rule", lazy="joined")
    target_post = relationship("Submission", lazy="joined")
    target_comment = relationship("Comment", lazy="joined")


    def __init__(self, *args, **kwargs):
        if "created_utc" not in kwargs:
            kwargs["created_utc"] = int(time.time())

        if "note" in kwargs:
            kwargs["_note"]=kwargs["note"]

        super().__init__(*args, **kwargs)

    def __repr__(self):
        return f"<ModAction(id={self.base36id})>"

    @property
    def actiontype(self):
        return ACTIONTYPES[self.kind]

    @property
    def note(self):

        if self.kind=="exile_user":
            if self.target_post:
                # Translators: why someone was exiled, in a guild's moderation log: for a post of theirs
                return tr('for <a href="%(link)s">post</a>', link=self.target_post.permalink)
            elif self.target_comment:
                # Translators: why someone was exiled, in a guild's moderation log: for a comment of theirs
                return tr('for <a href="%(link)s">comment</a>', link=self.target_comment.permalink)
            return self._note or ""
        elif self.kind=="chatban_user":
            return tr('Reason: %(reason)s', reason=self._note) if self._note else ''
        else:
            return self._note or ""

    @note.setter
    def note(self, x):
        self._note=x

    @property
    def string(self):
        return self.string_for(True)

    def string_for(self, titles=True):
        """The entry as a line of words. `titles=False` names a post by its id, not its title: a title is the author's
        text, which only the guild's guildmasters and the admins see in a log (the public Log names none)."""
        output = self._text("str", titles)

        if self.note:
            output += f" <i>({self.note})</i>"

        return output

    @property
    def target_link(self):
        return self._target_link(True)

    def _target_link(self, titles=True):
        if self.target_user:
            if self.target_user.is_deleted:
                return tr("[deleted user]")
            else:
                return f'<a href="{self.target_user.permalink}">{self.target_user.username}</a>'
        elif self.target_post:
            label = self.target_post.title if titles else f"#{self.target_post.base36id}"
            return f'<a href="{self.target_post.permalink}">{label}</a>'
        elif self.target_comment:
            # Translators: a link to a comment in a guild's moderation log ("hid a comment from this guild")
            return tr('<a href="%(link)s">comment</a>', link=self.target_comment.permalink)

        else:
            return ''

    @property
    def json(self):
        data={
            "id":self.base36id,
            "guild": self.board.name,
            "kind": self.kind,
            "created_utc": self.created_utc,
            # an admin is never named in a public answer, as the page doesn't (the Log: helpers/action_log.py)
            "mod": app.config['SITE_NAME'] if self.by_admin else self.user.username,
        }

        if self.target_user_id and self.target_user:
            data["target_user_id"]=self.target_user.base36id
            data["target_user"]=self.target_user.username

        if self.target_comment_id and self.target_comment:
            data["target_comment_id"]=self.target_comment.base36id

        if self.target_submission_id and self.target_post:
            data["target_submission_id"]=self.target_post.base36id

        if self._note:
            data["note"]=self._note

        return data
    



    @property
    def icon(self):
        return self.actiontype['icon']

    @property
    def color(self):
        return self.actiontype['color']

    @property
    def permalink(self):
        return f"{self.board.permalink}/mod/log/{self.base36id}"

    def _text(self, key, titles=True):
        """The entry's "str" or "title" in the reader's language, with the values its English names."""
        template = self.actiontype[key]
        values = {"target": lambda: self._target_link(titles),
                  "title": lambda: self.target_post.title if titles else f"#{self.target_post.base36id}",
                  "name": lambda: self.target_user.username, "guild": lambda: self.board.name,
                  "who": self._who}
        return _filled(template, {name: values[name]() for name in _VALUE.findall(template)})

    @property
    def by_admin(self):
        """Done by the site's admins: they are not named in a public entry (the Log: helpers/action_log.py). The note's words
        say so only for an account that really is an admin: a guildmaster's reason is the same note, free text, and "admin
        action" in it must not make their own action read as the site's (the audit, 2026-11-05: H3)."""
        user = self.user
        return bool(user is not None and (user.admin_level or 0) >= 3
                    and any(mark in (self._note or "").lower() for mark in action_log.ADMIN_MARKERS))

    def _who(self):
        if self.user.is_deleted:
            return tr("[deleted user]")
        elif self.by_admin:
            return f"@{app.config['SITE_NAME']}"
        else:
            return f"@{self.user.username}"

    def title_text(self, titles=True):
        """The page title of one entry. `titles=False` names a post by its id: a title is the author's text, which the public
        sees none of (the audit, 2026-11-05: H4)."""
        return self._text("title", titles)
    
    


@event.listens_for(ModAction, "after_insert")
def _copy_into_the_log(mapper, connection, target):
    """Every guild mod-log row is copied into the Log as it is made (helpers/action_log.py): the 78 places that write
    one need no change. In a savepoint, so a failed copy never loses the mod action itself; it is logged loudly."""
    try:
        with connection.begin_nested():
            action_log.mirror_modaction(connection, target.id, target.user_id, target.board_id, target.kind,
                                        target.target_user_id, target.target_submission_id, target.target_comment_id,
                                        target._note, target.created_utc)
    except Exception:
        logging.getLogger(__name__).exception("the Log could not copy mod action %s (%s)", target.id, target.kind)


ACTIONTYPES={
    "kick_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('kicked post %(target)s'),
        "icon":"fa-sign-out fa-flip-horizontal",
        "color": "bg-danger",
        "title": N_('%(who)s kicked post %(title)s')
    },
    "hide_post_from_guild":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('hid post %(target)s from this guild'),
        "icon":"fa-eye-slash",
        "color": "bg-danger",
        "title": N_('%(who)s hid post %(title)s from this guild')
    },
    "unhide_post_from_guild":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('unhid post %(target)s in this guild'),
        "icon":"fa-eye",
        "color": "bg-muted",
        "title": N_('%(who)s unhid post %(title)s in this guild')
    },
    "hide_comment_from_guild":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('hid a %(target)s from this guild'),
        "icon":"fa-eye-slash",
        "color": "bg-danger",
        "title": N_('%(who)s hid a comment from this guild')
    },
    "unhide_comment_from_guild":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('unhid a %(target)s in this guild'),
        "icon":"fa-eye",
        "color": "bg-muted",
        "title": N_('%(who)s unhid a comment in this guild')
    },
    "approve_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('approved post %(target)s'),
        "icon":"fa-check",
        "color": "bg-success",
        "title": N_('%(who)s approved post %(title)s')
    },    
    "yank_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('yanked post %(target)s'),
        "icon":"fa-hand-lizard",
        "color": "bg-muted",
        "title": N_('%(who)s yanked post %(title)s')
    },
    "exile_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('exiled user %(target)s'),
        "icon":"fa-user-slash",
        "color": "bg-danger",
        "title": N_('%(who)s exiled user %(name)s')
    },
    "unexile_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-exiled user %(target)s'),
        "icon": "fa-user-slash",
        "color": "bg-muted",
        "title": N_('%(who)s un-exiled user %(name)s')
    },
    "chatban_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('chatbanned user %(target)s'),
        "icon":"fa-comments-alt",
        "color": "bg-danger",
        "title": N_('%(who)s chatbanned user %(name)s')
    },
    "unchatban_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-chatbanned user %(target)s'),
        "icon": "fa-comments-alt",
        "color": "bg-muted",
        "title": N_('%(who)s un-chatbanned user %(name)s')
    },
    "contrib_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('added contributor %(target)s'),
        "icon": "fa-user-check",
        "color": "bg-info",
        "title": N_('%(who)s added contributor %(name)s')
    },
    "uncontrib_user":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('removed contributor %(target)s'),
        "icon": "fa-user-check",
        "color": "bg-muted",
        "title": N_('%(who)s removed user %(name)s')
    },
    "herald_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('heralded their %(target)s'),
        "icon": "fa-crown",
        "color": "bg-warning",
        "title": N_('%(who)s heralded their comment')
    },
    "herald_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('heralded their post %(target)s'),
        "icon": "fa-crown",
        "color": "bg-warning",
        "title": N_('%(who)s heralded their post %(title)s')
    },
    "unherald_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-heralded their %(target)s'),
        "icon": "fa-crown",
        "color": "bg-muted",
        "title": N_('%(who)s un-heralded their comment')
        },
    "unherald_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-heralded their post %(target)s'),
        "icon": "fa-crown",
        "color": "bg-muted",
        "title": N_('%(who)s un-heralded their post %(title)s')
    },
    "pin_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('pinned a %(target)s'),
        "icon":"fa-thumbtack fa-rotate--45",
        "color": "bg-info",
        "title": N_('%(who)s pinned a comment')
    },
    "unpin_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-pinned a %(target)s'),
        "icon":"fa-thumbtack fa-rotate--45",
        "color": "bg-muted",
        "title": N_('%(who)s un-pinned a comment')
    },
    # guild flair (helpers/guild_flair.py); the note is the flair's name, escaped when it was written
    "flair_create":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('added a flair'),
        "icon":"fa-tag",
        "color": "bg-primary",
        "title": N_('%(who)s added a flair')
    },
    "flair_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed a flair'),
        "icon":"fa-tag",
        "color": "bg-primary",
        "title": N_('%(who)s changed a flair')
    },
    "flair_delete":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('deleted a flair'),
        "icon":"fa-tag",
        "color": "bg-danger",
        "title": N_('%(who)s deleted a flair')
    },
    "flair_settings":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed whether forwards need a flair'),
        "icon":"fa-tag",
        "color": "bg-primary",
        "title": N_('%(who)s changed whether forwards need a flair')
    },
    # guild channels (helpers/guild_channels.py); the note is the channel's name, escaped. A removed message's text is
    # not logged: the entry names the channel and the writer
    "channel_create":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('made a channel'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s made a channel')
    },
    "channel_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed a channel'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s changed a channel')
    },
    "channel_delete":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('deleted a channel'),
        "icon":"fa-comments",
        "color": "bg-danger",
        "title": N_('%(who)s deleted a channel')
    },
    "channel_pin":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('pinned a message in a channel'),
        "icon":"fa-thumbtack",
        "color": "bg-primary",
        "title": N_('%(who)s pinned a message in a channel')
    },
    "channel_unpin":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('took a pin off a channel message'),
        "icon":"fa-thumbtack",
        "color": "bg-muted",
        "title": N_('%(who)s took a pin off a channel message')
    },
    "category_create":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('made a channel category'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s made a channel category')
    },
    "category_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('renamed a channel category'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s renamed a channel category')
    },
    "category_delete":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('deleted a channel category'),
        "icon":"fa-comments",
        "color": "bg-danger",
        "title": N_('%(who)s deleted a channel category')
    },
    "role_create":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('made a role'),
        "icon":"fa-user-group",
        "color": "bg-primary",
        "title": N_('%(who)s made a role')
    },
    "role_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed a role'),
        "icon":"fa-user-group",
        "color": "bg-primary",
        "title": N_('%(who)s changed a role')
    },
    "role_delete":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('deleted a role'),
        "icon":"fa-user-group",
        "color": "bg-danger",
        "title": N_('%(who)s deleted a role')
    },
    "role_assign":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('gave a role to %(target)s'),
        "icon":"fa-user-group",
        "color": "bg-primary",
        "title": N_('%(who)s gave a role to %(target)s')
    },
    "role_unassign":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('took a role from %(target)s'),
        "icon":"fa-user-group",
        "color": "bg-muted",
        "title": N_('%(who)s took a role from %(target)s')
    },
    "automod_settings":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed the auto-moderation of the channels'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s changed the auto-moderation of the channels')
    },
    "channel_feed_add":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('added a bot feed %(target)s to a channel'),
        "icon":"fa-comments",
        "color": "bg-primary",
        "title": N_('%(who)s added a bot feed %(target)s to a channel')
    },
    "channel_feed_remove":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('took a bot feed %(target)s off a channel'),
        "icon":"fa-comments",
        "color": "bg-muted",
        "title": N_('%(who)s took a bot feed %(target)s off a channel')
    },
    "channel_message_remove":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('removed a message by %(target)s from a channel'),
        "icon":"fa-comment-slash",
        "color": "bg-danger",
        "title": N_('%(who)s removed a channel message')
    },
    # guild wiki and resources (helpers/guild_wiki.py); the note is the page's address, escaped when it was written
    "wiki_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('edited a wiki page'),
        "icon":"fa-book",
        "color": "bg-primary",
        "title": N_('%(who)s edited a wiki page')
    },
    "wiki_restore":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('restored a wiki page revision'),
        "icon":"fa-book",
        "color": "bg-primary",
        "title": N_('%(who)s restored a wiki page revision')
    },
    "wiki_settings":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed who may edit a wiki page'),
        "icon":"fa-book",
        "color": "bg-primary",
        "title": N_('%(who)s changed who may edit a wiki page')
    },
    "wiki_delete":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('deleted a wiki page'),
        "icon":"fa-book",
        "color": "bg-danger",
        "title": N_('%(who)s deleted a wiki page')
    },
    "wiki_toggle":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('switched the wiki'),
        "icon":"fa-book",
        "color": "bg-primary",
        "title": N_('%(who)s switched the wiki')
    },
    "resources_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('changed the pinned resources'),
        "icon":"fa-thumbtack",
        "color": "bg-primary",
        "title": N_('%(who)s changed the pinned resources')
    },
    # guild events (helpers/guild_events.py); the note is the event's title, escaped when it was written
    "event_create":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('created an event'),
        "icon":"fa-calendar-days",
        "color": "bg-primary",
        "title": N_('%(who)s created an event')
    },
    "event_edit":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('edited an event'),
        "icon":"fa-calendar-days",
        "color": "bg-primary",
        "title": N_('%(who)s edited an event')
    },
    "event_cancel":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('cancelled an event'),
        "icon":"fa-calendar-xmark",
        "color": "bg-danger",
        "title": N_('%(who)s cancelled an event')
    },
    "set_post_flair":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('set the flair of post %(target)s'),
        "icon":"fa-tag",
        "color": "bg-primary",
        "title": N_('%(who)s set the flair of post %(title)s')
    },
    "pin_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('pinned post %(target)s'),
        "icon":"fa-thumbtack fa-rotate--45",
        "color": "bg-success",
        "title": N_('%(who)s pinned post %(title)s')
    },
    "unpin_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-pinned post %(target)s'),
        "icon":"fa-thumbtack fa-rotate--45",
        "color": "bg-muted",
        "title": N_('%(who)s un-pinned post %(title)s')
    },
    "invite_mod":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('invited guildmaster %(target)s'),
        "icon":"fa-user-crown",
        "color": "bg-info",
        "title": N_('%(who)s invited guildmaster @%(name)s')
    },
    "uninvite_mod":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('rescinded guildmaster invitation to %(target)s'),
        "icon":"fa-user-crown",
        "color": "bg-muted",
        "title": N_('%(who)s rescinded guildmaster invitation to @%(name)s')
    },
    "accept_mod_invite":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('accepted guildmaster invitation'),
        "icon":"fa-user-crown",
        "color": "bg-warning",
        "title": N_('%(who)s accepted guildmaster invitation')
    },
    "remove_mod":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('removed guildmaster %(target)s'),
        "icon":"fa-user-crown",
        "color": "bg-danger",
        "title": N_('%(who)s removed guildmaster @%(name)s')
    },
    "dethrone_self":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('stepped down as guildmaster'),
        "icon":"fa-user-crown",
        "color": "bg-danger",
        "title": N_('%(who)s stepped down as guildmaster')
    },
    "add_mod":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('added guildmaster %(target)s'),
        "icon":"fa-user-crown",
        "color": "bg-success",
        "title": N_('%(who)s added guildmaster @%(name)s')
    },
    "update_settings":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('updated setting'),
        "icon":"fa-cog",
        "color": "bg-info",
        "title": N_('%(who)s updated settings')
    },
    "update_appearance":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('updated appearance'),
        "icon":"fa-palette",
        "color": "bg-info",
        "title": N_('%(who)s updated appearance')
    },
    "set_nsfw":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('set nsfw on post %(target)s'),
        "icon":"fa-eye-evil",
        "color": "bg-danger",
        "title": N_('%(who)s set nsfw on post %(title)s')
    },
    "unset_nsfw":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-set nsfw on post %(target)s'),
        "icon":"fa-eye-evil",
        "color": "bg-muted",
        "title": N_('%(who)s un-set nsfw on post %(title)s')
    },
    "set_sensitive":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('marked post %(target)s as sensitive content'),
        "icon":"fa-eye-slash",
        "color": "bg-warning",
        "title": N_('%(who)s marked post %(title)s as sensitive content')
    },
    "unset_sensitive":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-marked post %(target)s as sensitive content'),
        "icon":"fa-eye-slash",
        "color": "bg-muted",
        "title": N_('%(who)s un-marked post %(title)s as sensitive content')
    },
    "set_sensitive_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('marked comment %(target)s as sensitive content'),
        "icon":"fa-eye-slash",
        "color": "bg-warning",
        "title": N_('%(who)s marked a comment as sensitive content')
    },
    "unset_sensitive_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('un-marked comment %(target)s as sensitive content'),
        "icon":"fa-eye-slash",
        "color": "bg-muted",
        "title": N_('%(who)s un-marked a comment as sensitive content')
    },
    "ban_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('removed post %(target)s'),
        "icon":"fa-feather-alt",
        "color": "bg-danger",
        "title": N_("%(who)s removed post %(title)s")
    },
    "unban_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('reinstated post %(target)s'),
        "icon":"fa-feather-alt",
        "color": "bg-muted",
        "title": N_("%(who)s reinstated post %(title)s")
    },
    "ban_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('removed %(target)s'),
        "icon":"fa-comment",
        "color": "bg-danger",
        "title": N_("%(who)s removed comment")
    },
    "unban_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('reinstated %(target)s'),
        "icon":"fa-comment",
        "color": "bg-muted",
        "title": N_("%(who)s reinstated comment")
    },
    "purge_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('permanently purged post %(target)s'),
        "icon":"fa-fire",
        "color": "bg-danger",
        "title": N_("%(who)s permanently purged a post")
    },
    "purge_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('permanently purged %(target)s'),
        "icon":"fa-fire",
        "color": "bg-danger",
        "title": N_("%(who)s permanently purged a comment")
    },
    "obliterate_post":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('permanently obliterated post %(target)s'),
        "icon":"fa-radiation",
        "color": "bg-danger",
        "title": N_("%(who)s permanently obliterated a post")
    },
    "obliterate_comment":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('permanently obliterated %(target)s'),
        "icon":"fa-radiation",
        "color": "bg-danger",
        "title": N_("%(who)s permanently obliterated a comment")
    },
    "change_perms":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('changed permissions on guildmaster %(target)s'),
        "icon":"fa-user-cog",
        "color": "bg-info",
        "title": N_("%(who)s changed permissions on %(name)s")
    },
    "change_invite":{
        # Translators: follows the moderator's name in a guild's moderation log; %(target)s is a link
        "str": N_('changed  permissions on guildmaster invitation to %(target)s'),
        "icon":"fa-user-cog",
        "color": "bg-muted",
        "title": N_("%(who)s changed permissions on invitation to %(name)s")
    },
    "create_guild":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('created +%(guild)s'),
        "icon": "fa-chess-rook",
        "color": "bg-primary",
        "title": N_("%(who)s created +%(guild)s")
    },
    "update_stylesheet":{
        # Translators: after the moderator's name in a guild's moderation log
        "str": N_('updated the stylesheet'),
        "icon": "fa-pen",
        "color": "bg-info",
        "title": N_('%(who)s updated the stylesheet')
    }
}


# the name of each kind as a guild's insights count them (helpers/guild_insights.py: "What the guildmasters did"):
# in English exactly the kind with spaces, capitalised, as the page always wrote it
# What the Log (helpers/action_log.py) records beyond the guild log's own kinds: an account's suspension or ban, a guild's ban,
# a hidden revision. Public ones are drawn on /log (helpers/log_view.py `sentence`); the staff-only kinds (a display name
# cleared, a forced sensitive mark, illegal content) are read on /admin/log by their action name.
ACTIONTYPES.update({
    # an author's own delete is no decision and has no entry; the log of a post or a profile lists it for its author and the admins
    "author_delete_post": {
        # Translators: follows the author's name in the Log; %(target)s is a link to a post
        "str": N_('deleted post %(target)s'), "icon": "fa-trash-alt", "color": "bg-muted",
        "title": N_('%(who)s deleted post %(title)s')},
    "author_delete_comment": {
        # Translators: follows the author's name in the Log; %(target)s is a link to a comment
        "str": N_('deleted a %(target)s'), "icon": "fa-trash-alt", "color": "bg-muted",
        "title": N_('%(who)s deleted a comment')},
    "suspend_user": {
        # Translators: follows the moderator's name in the Log; %(target)s is a link to an account
        "str": N_('suspended %(target)s'), "icon": "fa-user-slash", "color": "bg-danger",
        "title": N_('%(who)s suspended %(name)s')},
    "unsuspend_user": {
        # Translators: follows the moderator's name in the Log; %(target)s is a link to an account
        "str": N_('lifted the suspension of %(target)s'), "icon": "fa-user-check", "color": "bg-muted",
        "title": N_('%(who)s lifted the suspension of %(name)s')},
    "ban_user": {
        # Translators: follows the moderator's name in the Log; %(target)s is a link to an account
        "str": N_('banned %(target)s'), "icon": "fa-user-slash", "color": "bg-danger",
        "title": N_('%(who)s banned %(name)s')},
    "unban_user": {
        # Translators: follows the moderator's name in the Log; %(target)s is a link to an account
        "str": N_('lifted the ban on %(target)s'), "icon": "fa-user-check", "color": "bg-muted",
        "title": N_('%(who)s lifted the ban on %(name)s')},
    "ban_guild": {
        # Translators: follows the moderator's name in the Log; %(guild)s is a guild's name
        "str": N_('banned the guild +%(guild)s'), "icon": "fa-ban", "color": "bg-danger",
        "title": N_('%(who)s banned the guild +%(guild)s')},
    "unban_guild": {
        # Translators: follows the moderator's name in the Log; %(guild)s is a guild's name
        "str": N_('lifted the ban on the guild +%(guild)s'), "icon": "fa-ban", "color": "bg-muted",
        "title": N_('%(who)s lifted the ban on the guild +%(guild)s')},
    "remove_wiki_revision": {
        # Translators: follows the moderator's name in the Log
        "str": N_("removed a revision of a guild's wiki"), "icon": "fa-book", "color": "bg-danger",
        "title": N_("%(who)s removed a revision of a guild's wiki")},
    "hide_revision": {
        # Translators: follows the moderator's name in the Log; %(target)s is a link to a post or a comment
        "str": N_('hid a revision of %(target)s'), "icon": "fa-eye-slash", "color": "bg-danger",
        "title": N_('%(who)s hid a revision of %(title)s')},
})


MOD_ACTION_LABELS = {
    # Translators: the kinds of action a guild's guildmasters take, as a guild's insights count them
    "kick_post": LazyProxy(lambda: trp("guildmaster action", "Kick post"), enable_cache=False),
    "hide_post_from_guild": LazyProxy(lambda: trp("guildmaster action", "Hide post from guild"), enable_cache=False),
    "unhide_post_from_guild": LazyProxy(lambda: trp("guildmaster action", "Unhide post from guild"), enable_cache=False),
    "hide_comment_from_guild": LazyProxy(lambda: trp("guildmaster action", "Hide comment from guild"), enable_cache=False),
    "unhide_comment_from_guild": LazyProxy(lambda: trp("guildmaster action", "Unhide comment from guild"), enable_cache=False),
    "approve_post": LazyProxy(lambda: trp("guildmaster action", "Approve post"), enable_cache=False),
    "yank_post": LazyProxy(lambda: trp("guildmaster action", "Yank post"), enable_cache=False),
    "exile_user": LazyProxy(lambda: trp("guildmaster action", "Exile user"), enable_cache=False),
    "unexile_user": LazyProxy(lambda: trp("guildmaster action", "Unexile user"), enable_cache=False),
    "chatban_user": LazyProxy(lambda: trp("guildmaster action", "Chatban user"), enable_cache=False),
    "unchatban_user": LazyProxy(lambda: trp("guildmaster action", "Unchatban user"), enable_cache=False),
    "contrib_user": LazyProxy(lambda: trp("guildmaster action", "Contrib user"), enable_cache=False),
    "uncontrib_user": LazyProxy(lambda: trp("guildmaster action", "Uncontrib user"), enable_cache=False),
    "herald_comment": LazyProxy(lambda: trp("guildmaster action", "Herald comment"), enable_cache=False),
    "herald_post": LazyProxy(lambda: trp("guildmaster action", "Herald post"), enable_cache=False),
    "unherald_comment": LazyProxy(lambda: trp("guildmaster action", "Unherald comment"), enable_cache=False),
    "unherald_post": LazyProxy(lambda: trp("guildmaster action", "Unherald post"), enable_cache=False),
    "pin_comment": LazyProxy(lambda: trp("guildmaster action", "Pin comment"), enable_cache=False),
    "unpin_comment": LazyProxy(lambda: trp("guildmaster action", "Unpin comment"), enable_cache=False),
    "flair_create": LazyProxy(lambda: trp("guildmaster action", "Flair create"), enable_cache=False),
    "flair_edit": LazyProxy(lambda: trp("guildmaster action", "Flair edit"), enable_cache=False),
    "flair_delete": LazyProxy(lambda: trp("guildmaster action", "Flair delete"), enable_cache=False),
    "flair_settings": LazyProxy(lambda: trp("guildmaster action", "Flair settings"), enable_cache=False),
    "channel_create": LazyProxy(lambda: trp("guildmaster action", "Channel create"), enable_cache=False),
    "channel_edit": LazyProxy(lambda: trp("guildmaster action", "Channel edit"), enable_cache=False),
    "channel_delete": LazyProxy(lambda: trp("guildmaster action", "Channel delete"), enable_cache=False),
    "channel_pin": LazyProxy(lambda: trp("guildmaster action", "Channel pin"), enable_cache=False),
    "channel_unpin": LazyProxy(lambda: trp("guildmaster action", "Channel unpin"), enable_cache=False),
    "category_create": LazyProxy(lambda: trp("guildmaster action", "Category create"), enable_cache=False),
    "category_edit": LazyProxy(lambda: trp("guildmaster action", "Category edit"), enable_cache=False),
    "category_delete": LazyProxy(lambda: trp("guildmaster action", "Category delete"), enable_cache=False),
    "role_create": LazyProxy(lambda: trp("guildmaster action", "Role create"), enable_cache=False),
    "role_edit": LazyProxy(lambda: trp("guildmaster action", "Role edit"), enable_cache=False),
    "role_delete": LazyProxy(lambda: trp("guildmaster action", "Role delete"), enable_cache=False),
    "role_assign": LazyProxy(lambda: trp("guildmaster action", "Role assign"), enable_cache=False),
    "role_unassign": LazyProxy(lambda: trp("guildmaster action", "Role unassign"), enable_cache=False),
    "automod_settings": LazyProxy(lambda: trp("guildmaster action", "Automod settings"), enable_cache=False),
    "channel_feed_add": LazyProxy(lambda: trp("guildmaster action", "Channel feed add"), enable_cache=False),
    "channel_feed_remove": LazyProxy(lambda: trp("guildmaster action", "Channel feed remove"), enable_cache=False),
    "channel_message_remove": LazyProxy(lambda: trp("guildmaster action", "Channel message remove"), enable_cache=False),
    "wiki_edit": LazyProxy(lambda: trp("guildmaster action", "Wiki edit"), enable_cache=False),
    "wiki_restore": LazyProxy(lambda: trp("guildmaster action", "Wiki restore"), enable_cache=False),
    "wiki_settings": LazyProxy(lambda: trp("guildmaster action", "Wiki settings"), enable_cache=False),
    "wiki_delete": LazyProxy(lambda: trp("guildmaster action", "Wiki delete"), enable_cache=False),
    "wiki_toggle": LazyProxy(lambda: trp("guildmaster action", "Wiki toggle"), enable_cache=False),
    "resources_edit": LazyProxy(lambda: trp("guildmaster action", "Resources edit"), enable_cache=False),
    "event_create": LazyProxy(lambda: trp("guildmaster action", "Event create"), enable_cache=False),
    "event_edit": LazyProxy(lambda: trp("guildmaster action", "Event edit"), enable_cache=False),
    "event_cancel": LazyProxy(lambda: trp("guildmaster action", "Event cancel"), enable_cache=False),
    "set_post_flair": LazyProxy(lambda: trp("guildmaster action", "Set post flair"), enable_cache=False),
    "pin_post": LazyProxy(lambda: trp("guildmaster action", "Pin post"), enable_cache=False),
    "unpin_post": LazyProxy(lambda: trp("guildmaster action", "Unpin post"), enable_cache=False),
    "invite_mod": LazyProxy(lambda: trp("guildmaster action", "Invite mod"), enable_cache=False),
    "uninvite_mod": LazyProxy(lambda: trp("guildmaster action", "Uninvite mod"), enable_cache=False),
    "accept_mod_invite": LazyProxy(lambda: trp("guildmaster action", "Accept mod invite"), enable_cache=False),
    "remove_mod": LazyProxy(lambda: trp("guildmaster action", "Remove mod"), enable_cache=False),
    "dethrone_self": LazyProxy(lambda: trp("guildmaster action", "Dethrone self"), enable_cache=False),
    "add_mod": LazyProxy(lambda: trp("guildmaster action", "Add mod"), enable_cache=False),
    "update_settings": LazyProxy(lambda: trp("guildmaster action", "Update settings"), enable_cache=False),
    "update_appearance": LazyProxy(lambda: trp("guildmaster action", "Update appearance"), enable_cache=False),
    "set_nsfw": LazyProxy(lambda: trp("guildmaster action", "Set nsfw"), enable_cache=False),
    "unset_nsfw": LazyProxy(lambda: trp("guildmaster action", "Unset nsfw"), enable_cache=False),
    "set_sensitive": LazyProxy(lambda: trp("guildmaster action", "Set sensitive"), enable_cache=False),
    "unset_sensitive": LazyProxy(lambda: trp("guildmaster action", "Unset sensitive"), enable_cache=False),
    "set_sensitive_comment": LazyProxy(lambda: trp("guildmaster action", "Set sensitive comment"), enable_cache=False),
    "unset_sensitive_comment": LazyProxy(lambda: trp("guildmaster action", "Unset sensitive comment"), enable_cache=False),
    "ban_post": LazyProxy(lambda: trp("guildmaster action", "Ban post"), enable_cache=False),
    "unban_post": LazyProxy(lambda: trp("guildmaster action", "Unban post"), enable_cache=False),
    "ban_comment": LazyProxy(lambda: trp("guildmaster action", "Ban comment"), enable_cache=False),
    "unban_comment": LazyProxy(lambda: trp("guildmaster action", "Unban comment"), enable_cache=False),
    "purge_post": LazyProxy(lambda: trp("guildmaster action", "Purge post"), enable_cache=False),
    "purge_comment": LazyProxy(lambda: trp("guildmaster action", "Purge comment"), enable_cache=False),
    "obliterate_post": LazyProxy(lambda: trp("guildmaster action", "Obliterate post"), enable_cache=False),
    "obliterate_comment": LazyProxy(lambda: trp("guildmaster action", "Obliterate comment"), enable_cache=False),
    "change_perms": LazyProxy(lambda: trp("guildmaster action", "Change perms"), enable_cache=False),
    "change_invite": LazyProxy(lambda: trp("guildmaster action", "Change invite"), enable_cache=False),
    "create_guild": LazyProxy(lambda: trp("guildmaster action", "Create guild"), enable_cache=False),
    "update_stylesheet": LazyProxy(lambda: trp("guildmaster action", "Update stylesheet"), enable_cache=False),
}
