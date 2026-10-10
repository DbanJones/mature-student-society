"""Reading the committee's spreadsheets without a spreadsheet library.

An .xlsx file is a zip of XML and a .csv is text; both come back as rows of
strings. ``parse_old_list`` then turns the rows of an old mailing-list export
(addresses with whatever names, colleges and notes sit beside them) into
entries ready to store.
"""

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

from accounts.models import COLLEGES

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
EMAIL = re.compile(r"""[^\s@<>,;:"']+@[^\s@<>,;:"']+\.[A-Za-z]{2,}""")
MAX_ROWS = 20000
_COLLEGE_BY_NORM = {re.sub(r"[^a-z]", "", label.lower()): label for _key, label in COLLEGES}


class SheetError(ValueError):
    """The upload is not a spreadsheet this site can read."""


def read_rows(upload):
    """Rows of cell strings from an .xlsx or .csv upload (first sheet only)."""
    data = upload.read()
    if data[:2] == b"PK":
        return _xlsx_rows(data)
    return _csv_rows(data)


def _csv_rows(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SheetError("That file is not readable text.")
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    return rows[:MAX_ROWS]


def _column(ref):
    """0-based column index of a cell reference such as ``C12``."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _xlsx_rows(data):
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        names = book.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")).iter(NS + "si"):
                shared.append("".join(t.text or "" for t in item.iter(NS + "t")))
        sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
        first = "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in names else (sheets[0] if sheets else None)
        if first is None:
            raise SheetError("That workbook has no sheets.")
        root = ET.fromstring(book.read(first))
        rows = []
        for row in root.iter(NS + "row"):
            cells = {}
            for cell in row.findall(NS + "c"):
                value = cell.find(NS + "v")
                if value is None:
                    inline = cell.find(NS + "is")
                    text = "".join(t.text or "" for t in inline.iter(NS + "t")) if inline is not None else ""
                elif cell.get("t") == "s":
                    text = shared[int(value.text)]
                else:
                    text = value.text or ""
                cells[_column(cell.get("r", ""))] = text.strip()
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
            if len(rows) >= MAX_ROWS:
                break
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError) as exc:
        raise SheetError("That file is not a spreadsheet this site can read.") from exc
    return rows


def parse_old_list(rows):
    """Entries from an old mailing-list export, one per distinct address.

    The address is found wherever it sits in the row; the text cells to its
    left are taken as the name, a cell naming a college as the college, and
    the rest kept as notes. Rows without an address (a header, say) are
    counted and skipped; a repeated address fills in what the first row
    left blank. Returns ``(entries, stats)``.
    """
    entries = {}
    stats = {"rows": 0, "no_address": 0, "duplicates": 0}
    for row in rows:
        if not any(row):
            continue
        stats["rows"] += 1
        found = [(i, EMAIL.search(cell)) for i, cell in enumerate(row)]
        found = [(i, match.group(0).lower()) for i, match in found if match]
        if not found:
            stats["no_address"] += 1
            continue
        col, email = found[0]
        email_cols = {i for i, _ in found}
        names, college, notes = [], "", []
        for i, cell in enumerate(row):
            if not cell or i in email_cols:
                continue
            label = _COLLEGE_BY_NORM.get(re.sub(r"[^a-z]", "", cell.lower()))
            if label and not college:
                college = label
            elif i < col and len(names) < 2:
                names.append(cell)
            else:
                notes.append(cell)
        first = names[0] if names else ""
        last = names[1] if len(names) > 1 else ""
        if first and last.lower().startswith(first.lower() + " "):  # "Ann", "Ann Smith"
            last = last[len(first):].strip()
        entry = entries.get(email)
        if entry is None:
            entries[email] = {
                "email": email, "first_name": first[:80], "last_name": last[:80],
                "college": college[:80], "notes": " · ".join(notes)[:300],
            }
            continue
        stats["duplicates"] += 1
        for key, value in (("first_name", first), ("last_name", last), ("college", college)):
            if value and not entry[key]:
                entry[key] = value[:80]
        extra = " · ".join(note for note in notes if note not in entry["notes"])
        if extra:
            entry["notes"] = (entry["notes"] + (" · " if entry["notes"] else "") + extra)[:300]
    return list(entries.values()), stats
