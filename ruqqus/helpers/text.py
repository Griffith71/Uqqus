def split_title_body(text, max_title=280):
    """Hard-split raw text where the title reaches `max_title`, counted as members are (helpers/text_length.py: CJK
    characters and emoji count 2) and never inside a character - no word-boundary awareness. Returns (title_part,
    body_part); body_part is "" if text fits entirely within max_title."""
    from ruqqus.helpers.text_length import clip, weighted_length
    text = text.strip()
    if weighted_length(text) <= max_title:
        return text, ""
    title = clip(text, max_title)
    return title, text[len(title):].lstrip()


def word_list(items, english_last=" and "):
    """Several names or phrases as one, in the reader's language: in English "a, b and c" as the site always wrote it
    (`english_last=", "` for "a, b, c"); in another language CLDR's own list pattern (babel.lists.format_list), so a
    sentence that takes the list as one value (`%(names)s`) never glues words of its own around it."""
    from ruqqus.helpers import i18n
    from ruqqus.helpers.i18n_runtime import babel_locale, current_locale
    items = [str(item) for item in items]
    if len(items) < 2:
        return items[0] if items else ""
    if current_locale() == i18n.ENGLISH:
        return ", ".join(items[:-1]) + english_last + items[-1]
    from babel.lists import format_list
    return format_list(items, locale=babel_locale())


def _cldr_locale():
    from babel import Locale
    from ruqqus.helpers.i18n_runtime import babel_locale
    try:
        return Locale.parse(babel_locale())
    except (ValueError, TypeError, LookupError):
        return None


def date_pattern(skeleton, fallback):
    """CLDR's own date pattern in the reader's language for a skeleton ("MMMd": "d MMM" in Spanish, "M月d日" in
    Japanese), to give date_text as its `cldr_format` (English never reads it: it keeps its strftime format)."""
    found = getattr(_cldr_locale(), "datetime_skeletons", {}).get(skeleton)
    return found.pattern if found is not None else fallback


def datetime_pattern(date_skeleton, fallback):
    """The same for a date and a time to the minute, joined as the reader's language joins them, for datetime_text."""
    found = _cldr_locale()
    try:
        date = found.datetime_skeletons[date_skeleton].pattern
        return found.datetime_formats["medium"].replace("{1}", date).replace("{0}", found.time_formats["short"].pattern)
    except (AttributeError, KeyError):
        return fallback
