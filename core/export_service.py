"""Turning a client's own data into files they can keep: Excel, PDF, Google Doc.

## Why there is no PDF library here

Nothing in this repo generated files before this module — `requirements.txt`
carries no reportlab, no openpyxl, no fpdf (verified by grep, not assumed).
Rather than add two dependencies, both formats are produced from the stdlib
and the browser:

- **.xlsx is written here, by hand.** An xlsx file is a zip of five small XML
  parts, and `zipfile` is stdlib. Cell text goes in as `inlineStr`, which
  sidesteps the shared-strings table entirely and keeps Hebrew, leading-zero
  phone numbers and long free text intact. Real numbers still go in as real
  numbers so sums work.

- **PDF is produced by the browser, not the server.** `print_html()` returns a
  standalone, print-styled page that opens in a new tab and calls
  `window.print()`; the client picks "Save as PDF". This is a deliberate
  choice, not a shortcut: a server-side PDF of HEBREW text needs an embedded
  Hebrew TTF plus bidi shaping, neither of which exists in the container, and
  getting that wrong produces a PDF full of boxes or reversed words. The
  browser already solves both, correctly, for the exact locale the client is
  reading in. The trade-off is that "export PDF" opens a print dialog.

- **Google Docs** are real Docs, created through the existing
  `drive_service.upload_google_doc()` (the same call `content_docs_agent`
  uses). See `doc_html()` and the endpoint's own comment for the ownership
  caveat: they land in uallak's Drive shared with the client, NOT in the
  client's own Drive, because no client-side Google OAuth carries a Drive
  scope today.

All three renderers take the same shape — `title`, `columns`
(`[(key, header), ...]`) and `rows` (list of dicts) — so a new exportable
dataset is a dict, never three new renderers.
"""
import io
import re
import zipfile
from datetime import datetime, timezone

# XML 1.0 forbids most control characters outright; a stray one makes the whole
# workbook unopenable. Client free text (lead messages, notes) reaches these
# renderers, so strip rather than trust.
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_escape(value) -> str:
    text = "" if value is None else str(value)
    text = _ILLEGAL_XML.sub("", text)
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))


def _cell_ref(col_index: int, row_index: int) -> str:
    """0-based column -> A1-style reference. Handles beyond Z (AA, AB, ...)."""
    letters = ""
    n = col_index
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return f"{letters}{row_index + 1}"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── Excel (.xlsx) ────────────────────────────────────────────────────────────

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _sheet_name(title: str) -> str:
    """Excel rejects a sheet name over 31 chars or containing : \\ / ? * [ ]."""
    cleaned = re.sub(r"[:\\/?*\[\]]", " ", title or "Sheet1").strip()
    return (cleaned[:31] or "Sheet1")


def _workbook_xml(title: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{_xml_escape(_sheet_name(title))}" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>')


def _sheet_xml(columns: list, rows: list) -> str:
    def cell(col_index: int, row_index: int, value) -> str:
        ref = _cell_ref(col_index, row_index)
        # Real numbers stay numeric so the client can sum a column. bool is a
        # subclass of int and must not become 1/0 in a report - checked first.
        if isinstance(value, bool) or value is None:
            value = "" if value is None else ("כן" if value else "לא")
        elif isinstance(value, (int, float)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return (f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f'{_xml_escape(value)}</t></is></c>')

    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>']

    parts.append('<row r="1">')
    for index, (_key, header) in enumerate(columns):
        parts.append(cell(index, 0, header))
    parts.append("</row>")

    for row_number, row in enumerate(rows, start=1):
        parts.append(f'<row r="{row_number + 1}">')
        for index, (key, _header) in enumerate(columns):
            parts.append(cell(index, row_number, row.get(key)))
        parts.append("</row>")

    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def to_xlsx(title: str, columns: list, rows: list) -> bytes:
    """A minimal but genuinely valid .xlsx workbook. columns is
    [(row_key, header_text), ...]; rows is a list of dicts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", _workbook_xml(title))
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(columns, rows))
    return buffer.getvalue()


# ─── Shared HTML table (PDF print view + Google Doc source) ───────────────────

def _table_html(columns: list, rows: list) -> str:
    head = "".join(f"<th>{_xml_escape(header)}</th>" for _key, header in columns)
    if not rows:
        body = (f'<tr><td colspan="{len(columns)}" class="empty">'
                "אין נתונים להצגה בטווח שנבחר</td></tr>")
    else:
        body = "".join(
            "<tr>" + "".join(
                f"<td>{_xml_escape(row.get(key)) or '—'}</td>" for key, _header in columns
            ) + "</tr>"
            for row in rows
        )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def print_html(title: str, subtitle: str, columns: list, rows: list,
               rtl: bool = True, lang: str = "he") -> str:
    """A standalone page styled for paper, which prints itself on load. The
    client's browser turns this into the PDF — see the module docstring for
    why the PDF is not rendered server-side."""
    direction = "rtl" if rtl else "ltr"
    return f"""<!DOCTYPE html>
<html lang="{_xml_escape(lang)}" dir="{direction}">
<head>
<meta charset="UTF-8">
<title>{_xml_escape(title)}</title>
<style>
  @page {{ size: A4 landscape; margin: 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Heebo', 'Segoe UI', Arial, sans-serif; color: #14110f;
         background: #fff; margin: 0; padding: 24px; }}
  header {{ display: flex; align-items: baseline; justify-content: space-between;
            border-bottom: 3px solid #FF4C1F; padding-bottom: 10px; margin-bottom: 18px; }}
  .brand {{ font-size: 20px; font-weight: 900; letter-spacing: -0.5px; }}
  .brand span {{ color: #FF4C1F; }}
  h1 {{ font-size: 19px; margin: 0 0 4px 0; }}
  .subtitle {{ font-size: 12px; color: #666; }}
  .generated {{ font-size: 11px; color: #888; white-space: nowrap; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 11.5px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: start;
            vertical-align: top; word-break: break-word; }}
  th {{ background: #f4f2ef; font-weight: 700; }}
  tbody tr:nth-child(even) {{ background: #fbfaf9; }}
  td.empty {{ text-align: center; color: #888; padding: 22px; }}
  thead {{ display: table-header-group; }}  /* repeat headers on every printed page */
  tr {{ break-inside: avoid; }}
  footer {{ margin-top: 16px; font-size: 10.5px; color: #999; }}
  .toolbar {{ margin-bottom: 16px; }}
  .toolbar button {{ font: inherit; font-size: 13px; padding: 8px 18px; cursor: pointer;
                     border: 0; border-radius: 8px; background: #FF4C1F; color: #fff;
                     font-weight: 700; }}
  @media print {{ .toolbar {{ display: none; }} body {{ padding: 0; }} }}
</style>
</head>
<body>
<div class="toolbar"><button onclick="window.print()">🖨 שמירה כ-PDF / הדפסה</button></div>
<header>
  <div>
    <h1>{_xml_escape(title)}</h1>
    <div class="subtitle">{_xml_escape(subtitle)}</div>
  </div>
  <div>
    <div class="brand">u<span>allak</span></div>
    <div class="generated">הופק ב-{_stamp()}</div>
  </div>
</header>
{_table_html(columns, rows)}
<footer>הופק אוטומטית ממערכת uallak · הנתונים נכונים לרגע ההפקה</footer>
<script>
  // Let the layout settle before the dialog steals the thread, and don't
  // re-open it if the client returns to the tab with the back button.
  window.addEventListener('load', function () {{ setTimeout(function () {{ window.print(); }}, 350); }});
</script>
</body>
</html>"""


def doc_html(title: str, subtitle: str, columns: list, rows: list) -> str:
    """HTML that Drive imports INTO a native Google Doc. Deliberately plainer
    than print_html: Drive's importer keeps headings, bold and table structure
    and throws most CSS away, so anything clever here is wasted work."""
    return (
        "<html><body>"
        f"<h1>{_xml_escape(title)}</h1>"
        f"<p><i>{_xml_escape(subtitle)}</i></p>"
        f"<p><small>הופק ב-{_stamp()} · uallak</small></p>"
        + _table_html(columns, rows).replace(
            "<table>", '<table border="1" cellpadding="4" cellspacing="0">')
        + "</body></html>"
    )
