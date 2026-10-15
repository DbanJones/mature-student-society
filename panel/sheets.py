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
import zlib
from xml.etree import ElementTree as ET

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from accounts.models import COLLEGES

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# An address starts at the start of a cell or after a separator, so
# "sean.o'brien@x.org" is read whole (apostrophes are legal before the @).
EMAIL = re.compile(r"""(?<![^\s<>,;:"()\[\]])[^\s@<>,;:"()\[\]]+@[^\s@<>,;:"'()\[\]]+\.[A-Za-z]{2,}""")
MAX_ROWS = 20000
MAX_XML_BYTES = 16 * 1024 * 1024  # one part unpacked; a mailing list never needs more
MAX_COLUMNS = 256  # cells further right than this are ignored
_COLLEGE_BY_NORM = {re.sub(r"[^a-z]", "", label.lower()): label for _key, label in COLLEGES}
_TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "rev", "sir", "dame"}


class SheetError(ValueError):
    """The upload is not a spreadsheet this site can read."""


OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # old .xls, or a password-protected .xlsx


def read_rows(upload):
    """Rows of cell strings from an .xlsx or .csv upload (first sheet only)."""
    data = upload.read()
    if data[:2] == b"PK":
        return _xlsx_rows(data)
    if data[:8] == OLE2:
        raise SheetError(
            "That workbook is password-protected or an old .xls: open it in Excel, "
            "remove the password and save it as .xlsx or .csv."
        )
    return _csv_rows(data)


def _delimiter(text):
    """The separator that splits the first lines into the same number of
    fields (more than one) most often; a tie goes to tab, then comma, as
    commas and semicolons turn up inside cells far more often than tabs."""
    lines = [line for line in text.splitlines() if line.strip()][:50]
    best, best_score = ",", 0
    for candidate in ("\t", ",", ";"):
        try:
            widths = [len(row) for row in csv.reader(lines, delimiter=candidate)]
        except csv.Error:
            continue
        common = max(set(widths), key=widths.count) if widths else 1
        score = widths.count(common) if common > 1 else 0
        if score > best_score:
            best, best_score = candidate, score
    return best


def _csv_rows(data):
    """Excel writes CSV as UTF-8, cp1252, Mac line endings or, as "Unicode
    text", tab-separated UTF-16; all of them are read."""
    unicode_text = data[:2] in (b"\xff\xfe", b"\xfe\xff")
    if unicode_text:
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise SheetError("That file is not readable text.") from exc
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
            odd = sum(1 for ch in text[:8192] if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\r\n"))
            if odd > len(text[:8192]) // 100:
                raise SheetError("That file is not readable text: save the sheet as .csv or .xlsx.")
    text = text.replace("\x00", "")
    # Excel's "Unicode text" is always tab-separated.
    delimiter = "\t" if unicode_text else _delimiter(text)
    try:
        rows = [
            [cell.strip() for cell in row]
            for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        ]
    except csv.Error as exc:
        raise SheetError("That file is not a CSV this site can read.") from exc
    return rows[:MAX_ROWS]


def _column(ref):
    """0-based column index of a cell reference such as ``C12``."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _text(node):
    """The text of a shared or inline string: a plain <t>, or rich-text runs
    (phonetic guides, <rPh>, are not part of the text)."""
    parts = [t.text or "" for t in node.findall(NS + "t")]
    parts += [t.text or "" for run in node.findall(NS + "r") for t in run.findall(NS + "t")]
    return "".join(parts)


def _read(book, name):
    """One part of the workbook, refused if it unpacks too large."""
    if book.getinfo(name).file_size > MAX_XML_BYTES:
        raise SheetError("That workbook is too big to read here: save the sheet as .csv and upload that.")
    return book.read(name)


def _first_sheet(book, names):
    """The workbook's first sheet as Excel orders them, not by file name."""
    try:
        workbook = ET.fromstring(_read(book, "xl/workbook.xml"))
        first = workbook.find(f"{NS}sheets/{NS}sheet")
        rel_id = first.get(REL_NS + "id")
        rels = ET.fromstring(_read(book, "xl/_rels/workbook.xml.rels"))
        for rel in rels.iter(PKG_NS + "Relationship"):
            if rel.get("Id") == rel_id:
                target = rel.get("Target", "").lstrip("/")
                target = target if target.startswith("xl/") else "xl/" + target
                if target in names:
                    return target
    except (KeyError, AttributeError, ET.ParseError):
        pass
    sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
    return "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in names else (sheets[0] if sheets else None)


def _xlsx_rows(data):
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        names = book.namelist()
        sheet = _first_sheet(book, names)
        if sheet is None:
            raise SheetError("That workbook has no sheets.")
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_text(item) for item in ET.fromstring(_read(book, "xl/sharedStrings.xml")).iter(NS + "si")]
        root = ET.fromstring(_read(book, sheet))
        rows = []
        for row in root.iter(NS + "row"):
            cells = {}
            for cell in row.findall(NS + "c"):
                value = cell.find(NS + "v")
                if value is None:
                    inline = cell.find(NS + "is")
                    text = _text(inline) if inline is not None else ""
                elif cell.get("t") == "s":
                    text = shared[int(value.text)]
                else:
                    text = value.text or ""
                column = _column(cell.get("r", ""))
                if 0 <= column < MAX_COLUMNS:
                    cells[column] = text.strip()
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
            if len(rows) >= MAX_ROWS:
                break
    except SheetError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, KeyError, IndexError, ValueError,
            TypeError, zlib.error, NotImplementedError, RuntimeError, EOFError, OSError) as exc:
        raise SheetError("That file is not a spreadsheet this site can read.") from exc
    return rows


def _valid(address):
    try:
        validate_email(address)
    except ValidationError:
        return False
    return True


def _is_name(cell):
    """A cell that can be part of someone's name: not a number, not a title."""
    plain = cell.strip(" .")
    return bool(plain) and not re.fullmatch(r"[\d\s.,/-]+", plain) and plain.lower() not in _TITLES


def _college(cell):
    return _COLLEGE_BY_NORM.get(re.sub(r"[^a-z]", "", cell.lower()))


def parse_old_list(rows):
    """Entries from an old mailing-list export, one per distinct address.

    The first valid address in a row is the person's; up to two text cells
    to its left are the name (numbers and titles skipped; "Ann" beside
    "Ann Smith" becomes Ann / Smith), text beside the address in its own
    cell ("Ann Smith <ann@x.org>") is the name when there is no other, a
    college named right of the address is the college (one on the left only
    when there is none on the right, it doesn't start a name beside it, and
    the row keeps a name without it), and the rest are notes. Rows without a valid address (a header, say) are
    counted and skipped; an address repeated in the file fills in what its
    first row left blank. Returns ``(entries, stats)``.
    """
    entries = {}
    stats = {"rows": 0, "no_address": 0, "invalid": 0, "duplicates": 0}
    for row in rows:
        if not any(row):
            continue
        stats["rows"] += 1
        found = [(i, match.group(0).strip("'")) for i, cell in enumerate(row) for match in EMAIL.finditer(cell)]
        good = [(i, address.lower()) for i, address in found if _valid(address)]
        if not good:
            stats["invalid" if found else "no_address"] += 1
            continue
        col, email = good[0]
        email_cols = {i for i, _ in found}
        left = [cell for i, cell in enumerate(row[:col]) if cell and i not in email_cols]
        # "Clare" beside "Clare Smith" is a first name, not Clare College
        paired = {cell for cell in left if any(o.lower().startswith(cell.lower() + " ") for o in left)}
        # a college right of the address is the college; one on the left only
        # when there is none on the right and the row keeps a name without it
        right_college = next(
            (i for i, cell in enumerate(row) if i > col and cell and i not in email_cols and _college(cell)), None)
        name_like = [cell for cell in left if _is_name(cell)]
        names, college, notes = [], "", []
        for i, cell in enumerate(row):
            if not cell or i in email_cols:
                continue
            if i == right_college:
                college = _college(cell)
            elif (i < col and right_college is None and not college and _college(cell)
                  and cell not in paired and len(name_like) > 1):
                college = _college(cell)
            elif i < col and len(names) < 2 and _is_name(cell):
                names.append(cell)
            elif _is_name(cell) or i > col:
                notes.append(cell)
        if not names:
            beside = EMAIL.sub("", row[col]).strip(" <>\"'(),;:[]")  # "Ann Smith <ann@x.org>"
            if _is_name(beside):
                names = beside.split(None, 1)
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
