from .get import *

from mistletoe import block_token, block_tokenizer
from mistletoe.block_token import BlockToken
from mistletoe.span_token import SpanToken
from mistletoe.html_renderer import HTMLRenderer
from .post_formatting import TEXT_COLORS, HIGHLIGHT_COLORS, ALIGNMENTS, BUTTON_CLASS
import os.path
import re

from flask import g

#preprocess re

enter_re = re.compile(r"(\n\r?\w+){3,}")

# fenced code (``` or ~~~ at the start of a line, closed or not) is never touched
fence_re = re.compile(r"^(?:```|~~~).*?(?:^(?:```|~~~)|\Z)", re.S | re.M)



# add token/rendering for @username mentions


class UserMention(SpanToken):

    pattern = re.compile(r"(^|\s|\n)@(\w{3,25})")
    parse_inner = False

    def __init__(self, match_obj):
        self.target = (match_obj.group(1), match_obj.group(2))


class BoardMention(SpanToken):

    pattern = re.compile(r"(^|\s|\n)\+(\w{3,25})")
    parse_inner = False

    def __init__(self, match_obj):

        self.target = (match_obj.group(1), match_obj.group(2))


class CurationMention(SpanToken):

    pattern = re.compile(r"(^|\s|\n)&(\w{3,25})")
    parse_inner = False

    def __init__(self, match_obj):

        self.target = (match_obj.group(1), match_obj.group(2))


class ChatMention(SpanToken):

    pattern = re.compile(r"(^|\s|\n)#(\w{3,25})")
    parse_inner = False

    def __init__(self, match_obj):

        self.target = (match_obj.group(1), match_obj.group(2))
        
class Emoji(SpanToken):
    
    pattern = re.compile(r":([A-Za-z0-9_-]+):")
    parse_inner = False
    
    def __init__(self, match_obj):
        self.target = match_obj.group(1)


class Spoiler(SpanToken):

    pattern = re.compile(r"(>!|<s>|\|\|)(.+?)(\|\||</s>|!<)")
    parse_inner = True

    def __init__(self, match_obj):

        self.target = match_obj.group(2)





# Formatting extras. Authors only ever pick from fixed names (see
# helpers/post_formatting.py): the tokens below accept nothing else, and the
# sanitizer drops every CSS class that is not on its list.

def _names(names):
    return "|".join(names)


class TextColor(SpanToken):
    """{c:red}coloured text{/c}"""

    pattern = re.compile(r"\{c:(" + _names(TEXT_COLORS) + r")\}(.+?)\{/c\}", re.DOTALL)
    parse_group = 2

    def __init__(self, match_obj):
        self.color = match_obj.group(1)


class Highlight(SpanToken):
    """{h:yellow}highlighted text{/h}"""

    pattern = re.compile(r"\{h:(" + _names(HIGHLIGHT_COLORS) + r")\}(.+?)\{/h\}", re.DOTALL)
    parse_group = 2

    def __init__(self, match_obj):
        self.color = match_obj.group(1)


class Mark(SpanToken):
    """==highlighted text== - the default (yellow) highlight. Needs text
    right against both pairs of equals signs, so "a == b == c" is untouched."""

    pattern = re.compile(r"(?<!\\)==(?=\S)(.+?)(?<=\S)==", re.DOTALL)


class AlignBlock(BlockToken):
    """A block of paragraphs, lists, quotes... aligned left, centre or right:

        ::: center
        Any markdown, parsed as usual.
        :::

    An alignment block that is never closed runs to the end of the text."""

    _open = re.compile(r"^ {0,3}:::[ \t]*(" + _names(ALIGNMENTS) + r")[ \t]*$")
    _close = re.compile(r"^ {0,3}:::[ \t]*$")

    def __init__(self, result):
        self.align, parse_buffer = result
        self.children = block_tokenizer.make_tokens(parse_buffer)

    @classmethod
    def start(cls, line):
        return bool(cls._open.match(line.rstrip("\r\n")))

    @classmethod
    def check_interrupts_paragraph(cls, lines):
        return cls.start(lines.peek())

    @classmethod
    def read(cls, lines):
        align = cls._open.match(next(lines).rstrip("\r\n")).group(1)
        start_line = lines.line_number()
        inner = []
        while lines.peek() is not None:
            line = next(lines)
            if cls._close.match(line.rstrip("\r\n")):
                break
            inner.append(line)
        return align, block_tokenizer.tokenize_block(inner, block_token._token_types, start_line=start_line)


# a link followed by {.button} becomes a button; the link itself was already
# parsed (and its address escaped) by the renderer, so this only adds the class
button_re = re.compile(r'<a href="([^"]*)"((?: title="[^"]*")?)>(.*?)</a>\{\.button\}', re.DOTALL)


# class OpMention(SpanToken):

#     pattern = re.compile("(^|\W|\s)@([Oo][Pp])\b")
#     parse_inner = False

#     def __init__(self, match_obj):
#         self.target = (match_obj.group(1), match_obj.group(2))


class CustomRenderer(HTMLRenderer):

    def __init__(self, **kwargs):
        super().__init__(UserMention,
                         BoardMention,
                         CurationMention,
                         #ChatMention,
                         Emoji,
                         Spoiler,
                         TextColor,
                         Highlight,
                         Mark,
                         AlignBlock #,
                         #OpMention
                         )

        for i in kwargs:
            self.__dict__[i] = kwargs[i]

    def render_user_mention(self, token):
        space = token.target[0]
        target = token.target[1]

        user = get_user(target, graceful=True)


        try:
            if g.v.admin_level == 0 and g.v.any_block_exists(user):
                return f"{space}@{target}"
        except BaseException:
            pass

        if (not user or (user.is_banned and not user.unban_utc) or user.is_deleted):
            return f"{space}@{target}"

        return f'{space}<a href="{user.permalink}" class="d-inline-block mention-user" data-original-name="{user.original_username}"><img src="/uid/{user.base36id}/pic/profile" class="profile-pic-20 mr-1">@{user.username}</a>'

    def render_board_mention(self, token):
        space = token.target[0]
        target = token.target[1]

        board = get_guild(target, graceful=True)

        if not board or board.is_banned:
            return f"{space}+{target}"
        else:
            return f'{space}<a href="{board.permalink}" class="d-inline-block"><img src="/+{board.name}/pic/profile" class="profile-pic-20 mr-1">+{board.name}</a>'

    def render_curation_mention(self, token):
        space = token.target[0]
        target = token.target[1]

        curation = get_curation(target, graceful=True)

        if not curation:
            return f"{space}&{target}"

        try:
            if curation.is_private and g.v.id != curation.owner_id:
                return f"{space}&{target}"
        except BaseException:
            if curation.is_private:
                return f"{space}&{target}"

        return f'{space}<a href="{curation.permalink}" class="d-inline-block">&{curation.slug}</a>'

    def render_chat_mention(self, token):
        space = token.target[0]
        target = token.target[1]

        board = get_guild(target, graceful=True)

        if not board or board.is_banned:
            return f"{space}#{target}"
        else:
            return f'{space}<a href="{board.permalink}/chat" class="d-inline-block"><img src="/+{board.name}/pic/profile" class="profile-pic-20 mr-1">#{board.name}</a>'

    def render_emoji(self, token):
        
        name=token.target
        
        if os.path.isfile(f"{app.config['RUQQUSPATH']}/assets/images/emojis/{name}"):
            
            return f'<span data-toggle="tooltip" title=":{name}:"><img class="emoji" src="/assets/images/emojis/{name}"></span>'
        
        elif g.v.has_premium and os.path.isfile(f"{app.config['RUQQUSPATH']}/assets/images/primojis/{name}"):
            
            return f'<span data-toggle="tooltip" title=":{name}:"><img class="emoji" src="/assets/images/primojis/{name}"></span>'

        else:
            return f":{name}:"
        
    def render_spoiler(self, token):

        return f'<span class="spoiler">{token.target}</span>'

    def render_text_color(self, token):

        return f'<span class="tc-{token.color}">{self.render_inner(token)}</span>'

    def render_highlight(self, token):

        return f'<mark class="hl-{token.color}">{self.render_inner(token)}</mark>'

    def render_mark(self, token):

        return f'<mark class="hl-yellow">{self.render_inner(token)}</mark>'

    def render_align_block(self, token):

        return f'<div class="ta-{token.align}">\n{self.render_inner(token)}</div>'

    def render_document(self, token):

        html = super().render_document(token)
        return button_re.sub(
            lambda m: f'<a class="{BUTTON_CLASS}" href="{m.group(1)}"{m.group(2)}>{m.group(3)}</a>', html)

    # def render_op_mention(self, token):

    #     space = token.target[0]
    #     target = token.target[1]

    #     print(self.__dict__)

    #     if "post_id" not in self.__dict__:
    #         return "[no op found]"

    #     post = get_submission(self.post_id)
    #     user = post.author
    #     return f'{space}<a href="{user.permalink}" class="d-inline-block"><img src="/@{user.username}/pic/profile" class="profile-pic-20 mr-1">@{user.username}</a>'

    
def _stack_to_paragraph(match):
    # three or more one-word lines in a row would make a tall column of single
    # words: put them on one line, as their own paragraph, and keep the words
    return "\n\n" + " ".join(match.group(0).split())


def _outside_fences(text, fn):
    out, last = [], 0
    for m in fence_re.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def preprocess(text):

    text=text.lstrip().rstrip()

    text=_outside_fences(text, lambda part: enter_re.sub(_stack_to_paragraph, part))

    text=re.sub("(\u200b|\u200c|\u200d)",'', text)

    return text

