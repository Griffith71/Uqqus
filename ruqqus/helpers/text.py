def split_title_body(text, max_title=280):
    """Hard-split raw text where the title reaches `max_title`, counted as members are (helpers/text_length.py: CJK
    characters and emoji count 2) and never inside a character - no word-boundary awareness. Returns (title_part,
    body_part); body_part is "" if text fits entirely within max_title."""
    from ruqqus.helpers.text_length import clip, weighted_length
    text = text.strip()
    if weighted_length(text) <= max_title:
        return text, ""
    return text[:max_title], text[max_title:].lstrip()
