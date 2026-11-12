#!/usr/bin/env python3
"""
vox-pdf2rst.py - Convert the VOX programme "Es sentido común - Programa
electoral, 29 de noviembre de 2026" (PDF, InDesign, A5) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph; a hyphen at a line end inside a block is joined like
   plain pdftotext joins it.
2. Write RST:
     * the title page ("PROGRAMA ELECTORAL", "29 de noviembre de 2026") ->
       document title and subtitle; the cover text -> a line block (the
       slogan and logos are images)
     * ÍNDICE -> a table (chapter | page), without the dot leaders
     * chapter titles ("CEUTA Y MELILLA") -> level 1
     * the 115 measures ("1. Recuperar Ceuta...") -> bullet items keeping
       their number and bold title; their "•" points -> nested bullets (glyph
       dropped)
     * a measure continued on the next page stays one item; bold kept
     * running head ("ES SENTIDO COMÚN") and page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the bullet glyphs and the index's
   dot leaders are the only text set aside. pandoc must read the same words,
   without warnings.

Usage:  python3 vox-pdf2rst.py Programa-completo-A5-1.pdf -o vox.rst
        python3 vox-pdf2rst.py Programa-completo-A5-1.pdf --verify-only vox.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
BULLET = "•"
HEADING_COLOR = "#2a6342"
SUB_X = 90                    # "•" texts start at x 96; measure texts at 57 / 82
LEADER = re.compile(r"\s*\.{4,}\s*")


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic and not f.bold)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def is_furniture(b):
    """Running head ("ES SENTIDO COMÚN", small, on the green band) and page number."""
    return b.y0 < 30 and b.font.size == 17 or b.y0 > 565 and re.fullmatch(r"\d{1,2}", b.text)


def is_heading(b):
    return b.font.size == 22 and b.font.color == HEADING_COLOR


def page_index(bs):
    title = next(b for b in bs if is_heading(b))
    entries = []                                           # one per "•", text maybe on two lines
    for b in sorted((b for b in bs if b is not title and b.text != BULLET), key=lambda b: b.y0):
        if entries and not LEADER.search(entries[-1][-1].text):
            entries[-1].append(b)
        else:
            entries.append([b])
    table = []
    for e in entries:
        text = " ".join(b.text for b in e)
        m = re.fullmatch(r"(.*?)\s*\.{4,}\s*(\d+)", text)
        if not m:
            raise C.ConvertError(f"index entry without page: {text!r}")
        table.append([C.esc_start(C.esc_inline(m.group(1))), C.esc_inline(m.group(2))])
    bullets = sum(b.text == BULLET for b in bs)
    if bullets != len(table):
        raise C.ConvertError(f"index: {bullets} bullets but {len(table)} entries")
    return heading(title.text, "=") + C.list_table(table, widths=[90, 10])


class Writer:
    """Measures with their points, joined across page breaks."""

    def __init__(self):
        self.out, self.item, self.pending = [], None, []   # item: list of ("p"|"sub", chars)

    def flush(self):
        if self.item:
            (_, first), rest = self.item[0], self.item[1:]
            self.out += C.wrap(C.esc_start(style(first)), "  ", "* ") + [""]
            for kind, chars in rest:
                txt = C.esc_start(style(chars))
                self.out += (C.wrap(txt, "    ", "  * ") if kind == "sub" else C.wrap(txt, "  ")) + [""]
            self.item = None
        for c in self.pending:
            self.out += c
        self.pending = []

    def emit(self, lines):
        self.flush()
        self.out += lines

    def comment(self, blocks):
        c = C.comment_blocks(blocks)
        if self.item:
            self.pending.append(c)
        else:
            self.out += c


def join(chars, more):
    """Next line of the same paragraph: like pdftotext, a hyphen ending the
    line is dropped and the halves joined; otherwise a space."""
    if chars and chars[-1][0] == "-":
        chars.pop()
    else:
        chars.append(SPACE)
    chars += more


def flow(w, bs, counts):
    first = True
    skip = set()
    prev = None                       # last block added to the open measure
    for b in bs:
        if b in skip:
            continue
        t = b.text
        if w.item and prev is not None and b.x0 > 75 and t != BULLET and 0 <= b.y0 - prev.y1 < 5:
            join(w.item[-1][1], b.chars())                                # next line, same paragraph
            prev = b
        elif is_heading(b):
            w.emit(heading(t, "="))
            counts["chapters"] += 1
        elif re.fullmatch(r"\d{1,3}\.", t):                               # "12." + its text
            mate = next((o for o in bs if o is not b and abs(o.y0 - b.y0) < 3 and o.x0 > b.x1), None)
            if mate is None:
                raise C.ConvertError(f"p{b.page}: measure {t} without its text")
            skip.add(mate)
            w.flush()
            w.item = [("p", b.chars() + [SPACE] + mate.chars())]
            counts["measures"] += 1
            prev = mate
        elif re.match(r"\d{1,3}\. \S", t) and b.x0 < 70:                  # "95. Cuidado..." in one block
            w.flush()
            w.item = [("p", b.chars())]
            counts["measures"] += 1
            prev = b
        elif t == BULLET:                                                  # "•" + its text
            mate = next((o for o in bs if o is not b and abs(o.y0 - b.y0) < 3 and o.x0 > b.x1), None)
            if mate is None or not w.item:
                raise C.ConvertError(f"p{b.page}: bullet without text or outside a measure")
            skip.add(mate)
            w.item.append(("sub", mate.chars()))
            counts["points"] += 1
            prev = mate
        elif first and w.item and b.x0 > 75:                               # the measure goes on here
            last = w.item[-1][1]
            ends = "".join(c for c, _ in last).rstrip().endswith(C.TERMINAL)
            if not ends or t[:1].islower():
                join(last, b.chars())
            else:
                w.item.append(("p", b.chars()))                            # a new paragraph of the measure
            prev = b
        else:
            w.emit(C.wrap(C.esc_start(style(b.chars()))) + [""])
            counts["paragraphs"] += 1
            prev = None
        first = False


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = {}
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = {"chapters": 0, "measures": 0, "points": 0, "paragraphs": 0}
    # title page: "PROGRAMA ELECTORAL" and the date -> document title and subtitle
    tp = next(p for p in sorted(pages) if p > 1 and any(b.font.size >= 31 for b in pages[p]))
    title = next(b for b in pages[tp] if b.font.size >= 31)
    date = next(b for b in pages[tp] if b is not title)
    w = Writer()
    w.emit(heading(title.text, "#", over=True) + heading(date.text, "*", over=True))
    for p in sorted(pages):
        bs = [b for b in pages[p] if b not in (title, date)]
        furniture = [b for b in bs if is_furniture(b)]
        body = [b for b in bs if b not in furniture]
        if p == 1:                                                         # cover
            w.emit(["| " + C.esc_inline(b.text) for b in sorted(body, key=lambda b: b.y0)] + [""])
        elif any(is_heading(b) and b.text == "ÍNDICE" for b in body):
            w.emit(page_index(body))
        elif body:
            flow(w, body, counts)
        w.comment(sorted(furniture, key=lambda b: b.y0))
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=BULLET, leaders=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the VOX programme", default_output="vox.rst"))
