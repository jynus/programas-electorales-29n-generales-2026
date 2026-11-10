#!/usr/bin/env python3
"""
partido_humanista-pdf2rst.py - Convert the Partido Humanista programme
"Elecciones Generales 2026" (PDF, LibreOffice) to reStructuredText,
repeatably, and verify that no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py).
2. Write RST, keeping pdftotext's order:
     * cover -> document title
     * chapters (Arial Black, 16-22 pt) -> level 1; blue 13 pt subheadings
       -> level 2; "Propuesta N: ..." entries -> level 3
     * bullets (•, ●, ○ and the Symbol font bullet U+F0B7) -> RST bullet
       lists: the glyph becomes the list marker; "○" items that the PDF
       keeps in one text block are split at each marker
     * the two tables (pages 29-30) -> list-tables
     * paragraphs re-flowed and joined across page breaks; bold and italic
       kept
3. Verify word by word against plain pdftotext. The only text set aside is
   the bullet glyphs (replaced by RST list markers); pandoc must read the
   same words, without warnings.

Usage:  python3 partido_humanista-pdf2rst.py "Programa Generales 2026.pdf" -o partido_humanista.rst
        python3 partido_humanista-pdf2rst.py "Programa Generales 2026.pdf" --verify-only partido_humanista.rst
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

BULLETS = "•●○"
BLUE = ("#2c5282", "#1a365d")
FURNITURE = C.Furniture(tokens=f"[{BULLETS}]")


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic and not f.bold)


def is_chapter(b):
    return b.font.color in BLUE and b.h >= 17 and b.x0 < 70


def bold(b):
    cs = [f for c, f in b.chars() if not c.isspace()]
    return bool(cs) and sum(f.bold for f in cs) > len(cs) / 2


def is_sub(b):
    return b.font.color == "#2c5282" and 13 <= b.h < 20 and bold(b) and b.x0 < 70


def split_at(block, ys):
    """Split a block's lines into items starting at the given y positions."""
    items, cur = [], []
    for ln in block.lines:
        if cur and any(abs(ln.y0 - y) <= 3 for y in ys):
            items.append(C.Block(block.page, cur))
            cur = []
        cur.append(ln)
    if cur:
        items.append(C.Block(block.page, cur))
    return items


def table_rows(bs, start):
    """From bs[start], collect rows of a table: blocks sharing a top edge,
    at least 3 per row (columns). Returns (rows, next index)."""
    rows, i = [], start
    while i < len(bs):
        row = [bs[i]]
        j = i + 1
        while j < len(bs) and bs[j].page == bs[i].page and abs(bs[j].y0 - bs[i].y0) <= 3:
            row.append(bs[j])
            j += 1
        if len(row) < 3:
            break
        rows.append(sorted(row, key=lambda b: b.x0))
        i = j
    return rows, i


def convert(pdf, report):
    blocks = C.extract(pdf)
    out = []
    # cover: every block of page 1 is the title
    cover = [b for b in blocks if b.page == 1]
    out += C.heading(" ".join(b.text for b in cover), "#", over=True)
    bs = [b for b in blocks if b.page > 1]
    counts = {"chapters": 0, "sections": 0, "propuestas": 0, "bullets": 0, "tables": 0}
    para = None            # open paragraph: [chars, kind] (kind: body/item), joined across pages
    item_indent = "  "

    def flush():
        nonlocal para
        if para:
            chars, kind = para
            txt = style(chars)
            if kind == "item":
                out.extend(C.wrap(txt, item_indent, "* ") + [""])
            else:
                out.extend(C.wrap(C.esc_start(txt)) + [""])
            para = None

    def add(chars, kind, page_break_ok):
        """Start a paragraph or continue the open one across a page break."""
        nonlocal para
        if para and page_break_ok and para[1] == kind:
            prev = "".join(c for c, _ in para[0]).rstrip()
            nxt = "".join(c for c, _ in chars).lstrip()
            if not prev.endswith(C.TERMINAL) or nxt[:1].islower():
                para[0] += [(" ", C.NOFONT)] + chars
                return
        flush()
        para = [chars, kind]

    i, last_page = 0, 0
    while i < len(bs):
        b = bs[i]
        new_page = b.page != last_page
        last_page = b.page
        first_on_page = new_page
        rows, j = table_rows(bs, i)
        if len(rows) >= 2:
            flush()
            cells = [[C.esc_start(style(c.chars())) for c in r] for r in rows]
            width = max(len(r) for r in cells)
            cells = [r + [""] * (width - len(r)) for r in cells]
            out += C.list_table(cells, header=True)
            counts["tables"] += 1
            i = j
            continue
        t = b.text
        if is_chapter(b) or is_sub(b):
            # a title wrapped over two blocks: join them
            kind = is_chapter(b)
            while i + 1 < len(bs) and bs[i + 1].page == b.page and is_chapter(bs[i + 1]) == kind \
                    and (is_chapter(bs[i + 1]) or is_sub(bs[i + 1])) and bs[i + 1].y0 - b.y1 < 0.6 * b.h:
                i += 1
                t += " " + bs[i].text
                b = bs[i]
            flush()
            out += C.heading(t, "=" if kind else "-")
            counts["chapters" if kind else "sections"] += 1
        elif t in tuple(BULLETS) and b.x0 > 60:
            # bullet glyph(s): the item text is the next block
            marks = [b]
            while i + 1 < len(bs) and bs[i + 1].text in tuple(BULLETS) and bs[i + 1].page == b.page:
                i += 1
                marks.append(bs[i])
            i += 1
            text = bs[i]
            if text.font.color == "#2c5282" and text.text.startswith("Propuesta"):
                flush()
                out += C.heading(text.text, "~")
                counts["propuestas"] += 1
            else:
                for it in split_at(text, [m.y0 for m in marks]):
                    flush()
                    para = [it.chars(), "item"]
                    counts["bullets"] += 1
        elif t[:1] in BULLETS and b.x0 > 60:
            # glyph and text in one block: " Propuesta 2: ..." or a plain item
            chars = b.chars()
            k = 1
            while k < len(chars) and chars[k][0] == " ":
                k += 1
            chars = chars[k:]
            plain = "".join(c for c, _ in chars)
            if b.font.color == "#2c5282" and plain.startswith("Propuesta"):
                flush()
                out += C.heading(plain, "~")
                counts["propuestas"] += 1
            else:
                flush()
                para = [chars, "item"]
                counts["bullets"] += 1
        else:
            kind = "item" if (b.x0 > 85 and para and para[1] == "item") else "body"
            add(b.chars(), kind, first_on_page)
        i += 1
    flush()
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify(rst, raw, FURNITURE, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Partido Humanista programme"))
