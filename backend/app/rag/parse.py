"""Document parsing (BUILD.md §9.2 step 3): PDF (pypdf), DOCX (python-docx), HTML (BeautifulSoup),
Markdown, plain text and CSV (each row a record). Returns blocks that keep heading and page context."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass


@dataclass
class Block:
    text: str
    heading_path: str = ""
    page: int | None = None
    kind: str = "para"            # para | heading | table


class ParseError(ValueError):
    pass


def detect_mime(filename: str, data: bytes) -> str:
    name = filename.lower()
    for ext, mime in ((".pdf", "application/pdf"),
                      (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                      (".html", "text/html"), (".htm", "text/html"), (".md", "text/markdown"),
                      (".markdown", "text/markdown"), (".csv", "text/csv"), (".txt", "text/plain")):
        if name.endswith(ext):
            return mime
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] == b"PK":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "text/plain"


def parse(data: bytes, mime: str, title: str = "") -> list[Block]:
    try:
        if mime == "application/pdf":
            return _pdf(data)
        if "wordprocessingml" in mime:
            return _docx(data)
        if mime == "text/html":
            return _html(data.decode("utf-8", "replace"))
        if mime == "text/markdown":
            return _markdown(data.decode("utf-8", "replace"))
        if mime == "text/csv":
            return _csv(data.decode("utf-8", "replace"), title)
        return _plain(data.decode("utf-8", "replace"))
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as the document's error status
        raise ParseError(f"could not parse {mime}: {exc}") from exc


_HEADING_LIKE = re.compile(r"^[A-Z][A-Za-z ,&/-]{2,60}$")


def _pdf(data: bytes) -> list[Block]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    blocks: list[Block] = []
    heading = ""
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for line in (x.strip() for x in text.splitlines()):
            if not line:
                continue
            if _HEADING_LIKE.match(line) and not line.endswith("."):
                heading = line
                blocks.append(Block(line, heading, i, "heading"))
            elif blocks and blocks[-1].kind == "para" and blocks[-1].page == i and not blocks[-1].text.endswith(".") \
                    and blocks[-1].heading_path == heading:
                blocks[-1].text += " " + line          # re-join wrapped lines
            else:
                blocks.append(Block(line, heading, i))
    if not blocks:
        raise ParseError("no extractable text in PDF")
    return blocks


def _docx(data: bytes) -> list[Block]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    blocks: list[Block] = []
    path: list[str] = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name or "") if p.style is not None else ""
            if style.startswith("Heading") or style == "Title":
                level = 0 if style == "Title" else int(re.sub(r"\D", "", style) or 1)
                path = path[:level] + [text]
                blocks.append(Block(text, " > ".join(path), None, "heading"))
            else:
                blocks.append(Block(text, " > ".join(path)))
        elif tag == "tbl":
            t = Table(el, d)
            rows = [" | ".join(c.text.strip() for c in r.cells) for r in t.rows]
            blocks.append(Block("\n".join(rows), " > ".join(path), None, "table"))
    return blocks


def _html(html: str) -> list[Block]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "nav", "footer"]):
        bad.decompose()
    blocks: list[Block] = []
    path: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if el.name == "table":
            rows = [" | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"]))
                    for tr in el.find_all("tr")]
            blocks.append(Block("\n".join(rows), " > ".join(path), None, "table"))
            continue
        if el.find_parent("table"):
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            level = int(el.name[1]) - 1
            path = path[:level] + [text]
            blocks.append(Block(text, " > ".join(path), None, "heading"))
        else:
            blocks.append(Block(text, " > ".join(path)))
    return blocks


def _markdown(md: str) -> list[Block]:
    blocks: list[Block] = []
    path: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block(" ".join(para).strip(), " > ".join(path)))
            para.clear()

    for line in md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1)) - 1
            path = path[:level] + [m.group(2).strip()]
            blocks.append(Block(m.group(2).strip(), " > ".join(path), None, "heading"))
        elif line.strip().startswith("|"):
            flush()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            row = " | ".join(cells)
            if blocks and blocks[-1].kind == "table":
                blocks[-1].text += "\n" + row
            else:
                blocks.append(Block(row, " > ".join(path), None, "table"))
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    return blocks


def _csv(text: str, title: str) -> list[Block]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    out = []
    for r in rows[1:]:
        out.append(Block("; ".join(f"{h}: {v}" for h, v in zip(header, r, strict=False)), title, None, "para"))
    return out


def _plain(text: str) -> list[Block]:
    return [Block(p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]
