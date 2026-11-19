"""The parts of a notice that machine translation breaks, kept out of the translated sentence (the cleanup of 2026-11-19).

A notice that put a guildmaster's reason in a fenced block and its links in markdown inside ONE message came back from the machine translator with the fence collapsed or the
`[text](address)` mangled, so the catalogue checks refused it and 14 core moderation notices were English in every language (the others that carry a link, 29 to 39 of 45).
Now the translated message holds plain words and NAMED PLACEHOLDERS (`%(why_block)s`, `%(log_link)s`, `%(post_link)s`...), and this module builds each placeholder from the
notice's stored values **when the notice is drawn**, in the reader's language: the fence round the reason, and each link with its own short, separately translated text
("See it in the Log", "Your post"). English is exactly what it was.

A guildmaster's free-text reason is still only ever inside the fence (`log_notices.plain` has taken the backticks out), and a translated link text can't carry brackets.
Pure: no I/O. `derive(values, translate)`: `translate` is the identity for the English that is stored and the reader's catalogue for what is drawn."""
import re

from ruqqus.helpers.i18n_runtime import N_

SEE_LOG = N_("See it in the Log")
APPEAL = N_("Appeal this decision")
THIS_DECISION = N_("this decision")
YOUR_POST = N_("Your post")
YOUR_COMMENT = N_("Your comment")
THIS_ITEM = N_("this item")
ASK_REVIEW = N_("Ask for a review")
SEE_WHICH = N_("See which, and appeal")

_BRACKETS = re.compile(r"[\[\]()`]")


def _text(translate, msgid):
    """A link's words in the reader's language, without anything that could end the link or open another thing."""
    out = _BRACKETS.sub("", str(translate(msgid) or msgid)).strip()
    return out or str(msgid)


def _link(translate, msgid, address):
    return f"[{_text(translate, msgid)}]({address})"


def derive(values, translate=str):
    """The placeholders a notice's message may use, from its stored `values` (only those whose sources are there)."""
    out = {}
    if values.get("why") is not None:
        out["why_block"] = f"```\n{values['why']}\n```"
    if values.get("note") is not None:
        out["note_block"] = f"```\n{values['note']}\n```"
    if values.get("log"):
        out["log_link"] = _link(translate, SEE_LOG, values["log"])
        out["decision_link"] = _link(translate, THIS_DECISION, values["log"])
    if values.get("appeal"):
        out["appeal_link"] = _link(translate, APPEAL, values["appeal"])
        out["review_link"] = _link(translate, ASK_REVIEW, values["appeal"])
        out["which_link"] = _link(translate, SEE_WHICH, values["appeal"])
    if values.get("item"):
        out["post_link"] = _link(translate, YOUR_POST, values["item"])
        out["comment_link"] = _link(translate, YOUR_COMMENT, values["item"])
    if values.get("link"):
        out["this_item_link"] = _link(translate, THIS_ITEM, values["link"])
    return out


# which stored value each placeholder is built from (so a test, or a caller, can say what a message needs from its builder)
SOURCES = {"why_block": "why", "note_block": "note", "log_link": "log", "decision_link": "log", "appeal_link": "appeal", "review_link": "appeal",
           "which_link": "appeal", "post_link": "item", "comment_link": "item", "this_item_link": "link"}


def needs(message):
    """The stored values a message needs from whoever sends it: the placeholders it names, each derived one replaced by the value it is built from."""
    names = set(re.findall(r"%\((\w+)\)s", str(message)))
    return {SOURCES.get(n, n) for n in names}


def rendered(message, values, translate=str):
    """The text of a notice as it is stored (English) or drawn (`translate` = the reader's catalogue): the message filled in from `values` and what `derive` builds."""
    values = dict(values)
    return str(message) % {**values, **derive(values, translate)}
