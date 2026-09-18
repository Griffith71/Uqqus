def split_title_body(text, max_title=280):
    """Hard-split raw text at exactly `max_title` characters - no
    word-boundary awareness, matching a literal character-count cutoff.
    Returns (title_part, body_part); body_part is "" if text fits
    entirely within max_title."""
    text = text.strip()
    if len(text) <= max_title:
        return text, ""
    return text[:max_title], text[max_title:].lstrip()
