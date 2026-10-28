"""The first lines of a post's body, as plain text, for its card in a feed (the UI modernisation, 2026-10). Pure.

The card shows them under the title (a few lines, clamped by the stylesheet: _feed.scss .post-snippet) instead of
nothing until "Show more" is pressed. Only text goes in: pictures, code blocks and tables are left to the full body,
which the card still holds for "Show more" and the post page. The text is escaped when it is drawn; nothing here is
HTML.

`excerpt(body_html, shows=...)` -> (text, more): `more` says whether the full body holds more than the card shows,
so the card draws its "Show more" link only then. The stylesheet decides how many lines show (4 on a text post, 2
above a picture, fewer in compact density); `shows` is about how many characters those lines hold on a wide feed
(SHOWS_TEXT, SHOWS_WITH_MEDIA). On a phone the lines hold fewer and the card's own link opens the whole post.
"""
import re

from bs4 import BeautifulSoup

LIMIT = 600            # characters sent to the card: more than four lines of the widest feed hold
SHOWS_TEXT = 300      # about what four lines of a wide feed hold
SHOWS_WITH_MEDIA = 150  # and two
_BLOCKS = ("p", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "div", "tr", "td", "th", "hr")
_LEFT_OUT = ("img", "pre", "table", "audio", "video", "figure", "script", "style")
_SPACE = re.compile(r"\s+")


def excerpt(body_html, limit=LIMIT, shows=SHOWS_TEXT):
    if not body_html or not body_html.strip():
        return "", False
    soup = BeautifulSoup(body_html, "html.parser")
    left_out = False
    for tag in soup.find_all(_LEFT_OUT):
        tag.decompose()
        left_out = True
    for tag in soup.find_all(_BLOCKS):
        tag.insert_after(" ")
    text = _SPACE.sub(" ", soup.get_text()).strip()
    cut = len(text) > limit
    if cut:
        text = text[:limit]
        space = text.rfind(" ")
        if space > limit * 0.6:              # end on a whole word unless that would lose too much
            text = text[:space]
        text = text.rstrip(" ,.;:-") + "…"
    return text, cut or left_out or len(text) > shows
