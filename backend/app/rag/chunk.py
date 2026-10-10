"""Heading-aware chunking (BUILD.md §9.2 step 4): split on headings first, then by size
(`chunk_tokens`, `chunk_overlap`). Each chunk keeps its heading path, page and a token estimate.
Tables are kept whole when they fit, so a row is never separated from its header."""
from __future__ import annotations

from dataclasses import dataclass

from ..heavy import count_tokens
from .parse import Block


@dataclass
class Chunk:
    ord: int
    text: str
    heading_path: str
    page: int | None
    token_estimate: int


def chunk_blocks(blocks: list[Block], chunk_tokens: int = 500, overlap: int = 60) -> list[Chunk]:
    sections: list[tuple[str, list[Block]]] = []
    for b in blocks:
        if b.kind == "heading" or not sections or sections[-1][0] != b.heading_path:
            sections.append((b.heading_path, []))
        if b.kind != "heading":
            sections[-1][1].append(b)
    chunks: list[Chunk] = []
    for heading, items in sections:
        if not items:
            continue
        title = heading.split(" > ")[-1] if heading else ""
        buf: list[str] = []
        page = items[0].page
        for b in items:
            pieces = _split(b.text, chunk_tokens, overlap) if b.kind != "table" or count_tokens(b.text) > chunk_tokens \
                else [b.text]
            for piece in pieces:
                if buf and count_tokens("\n".join(buf) + "\n" + piece) > chunk_tokens:
                    chunks.append(_mk(len(chunks), title, buf, heading, page))
                    tail = _tail(buf[-1], overlap)
                    buf = [tail] if tail else []
                    page = b.page
                buf.append(piece)
        if buf:
            chunks.append(_mk(len(chunks), title, buf, heading, page))
    return chunks


def _mk(i: int, title: str, buf: list[str], heading: str, page: int | None) -> Chunk:
    body = "\n".join(buf)
    text = f"{title}\n{body}" if title and not body.startswith(title) else body
    return Chunk(i, text, heading, page, count_tokens(text))


def _split(text: str, size: int, overlap: int) -> list[str]:
    """Split by the same token estimate the chunks report (~4 characters per token), with overlap."""
    words = text.split()
    budget, keep = size * 4, overlap * 4
    pieces: list[str] = []
    cur: list[str] = []
    length = 0
    for wd in words:
        if cur and length + len(wd) + 1 > budget:
            pieces.append(" ".join(cur))
            tail: list[str] = []
            tail_len = 0
            for t in reversed(cur):                       # carry ~`overlap` tokens into the next piece
                if tail_len + len(t) + 1 > keep:
                    break
                tail.insert(0, t)
                tail_len += len(t) + 1
            cur, length = tail, tail_len
        cur.append(wd)
        length += len(wd) + 1
    if cur:
        pieces.append(" ".join(cur))
    return pieces or [text]


def _tail(text: str, overlap: int) -> str:
    words = text.split()
    out: list[str] = []
    for wd in reversed(words):
        if sum(len(x) + 1 for x in out) + len(wd) + 1 > overlap * 4:
            break
        out.insert(0, wd)
    return " ".join(out) if len(out) * 2 < len(words) else ""
