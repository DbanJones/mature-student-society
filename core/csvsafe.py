"""Text that is safe in a CSV file a spreadsheet will open.

A spreadsheet runs a cell that starts with =, +, -, @, a tab or a carriage
return as a formula, so a name typed as "=HYPERLINK(...)" could do harm to
whoever opens a download. A leading apostrophe keeps it plain text.
"""


def cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
