"""Limits on the Markdown the site will render.

Python-Markdown is super-linear on a few shapes of input: a few thousand
backticks, brackets or angle brackets hold a worker for half a minute.
Content is refused at the door (the live preview, every editor's save)
rather than rendered, so a stored page can never stall every view of it
and the preview cannot be used to tie up the server.
"""

MAX_CHARS = 60000
# (character, how many of it a page may hold)
HEAVY = (("`", 300), ("[", 800), ("<", 800))


def richtext_problem(text):
    """Why ``text`` must not be rendered, or None."""
    if len(text) > MAX_CHARS:
        return f"it is too long ({len(text):,} characters; the limit is {MAX_CHARS:,})"
    for char, limit in HEAVY:
        count = text.count(char)
        if count > limit:
            return f"it has too many “{char}” characters ({count:,}; the limit is {limit:,})"
    return None
