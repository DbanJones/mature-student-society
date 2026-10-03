"""Line-by-line comparison of two revisions, for the diff pages."""

import difflib


def line_diff(old, new):
    """Rows of (kind, text) where kind is 'same', 'del' or 'ins'."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    rows = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows += [("same", line) for line in old_lines[i1:i2]]
        else:
            rows += [("del", line) for line in old_lines[i1:i2]]
            rows += [("ins", line) for line in new_lines[j1:j2]]
    return rows


def summary(rows):
    return {
        "added": sum(1 for kind, _ in rows if kind == "ins"),
        "removed": sum(1 for kind, _ in rows if kind == "del"),
    }
