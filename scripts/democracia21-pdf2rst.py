#!/usr/bin/env python3
"""
democracia21-pdf2rst.py - Convert the Democracia 21 document "España Limpia"
(PDF, A4) to reStructuredText, repeatably, and verify that no text was
altered, omitted or added. It is the party's standing programme on
democratic regeneration, not one written for the general election of 29
November 2026 (latest.json carries a note saying so).

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph; a hyphen at a line end inside a block is joined like
   plain pdftotext joins it (the text has none).
2. Write RST:
     * the cover's "ESPAÑA LIMPIA" -> document title
     * the black banners ("NO MÁS REDES CLIENTELARES") -> level 1; the
       headings with an icon ("PUNTO Y FINAL AL TSUNAMI DE ASESORES") ->
       level 2
     * the centred list of page 2 ("Creemos en una política basada en / el
       servicio público, / el mérito, ...") -> a line block, line by line
     * "●" and "-" points -> RST bullet lists (the glyph becomes the list
       marker); the points printed indented without a glyph (page 4) ->
       indented paragraphs (a block quote)
     * paragraphs joined across page breaks; bold and italics kept
     * running head ("España Limpia") and footer ("www.democracia21.es") ->
       RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list glyphs are the only
   text set aside. pandoc must read the same words, without warnings.

Usage:  python3 democracia21-pdf2rst.py espana-limpia-democracia21.pdf -o democracia21.rst
        python3 democracia21-pdf2rst.py espana-limpia-democracia21.pdf --verify-only democracia21.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
YELLOW = "#fffd38"
BODY_X, INDENT_X = 72.0, 108.0
GLYPH = re.compile(r"([●-]) ")


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def is_furniture(b):
    """Running head (Ibrand 18, top) and footer (the web address, bottom)."""
    return b.y0 < 80 and b.font.name.startswith("Ibrand") and b.font.size < 30 \
        or b.y0 > 780 and b.text == "www.democracia21.es"


def line_chars(lines):
    """Characters of some lines of a block, joined like Block.chars joins them."""
    out = []
    for ln in lines:
        if out:
            if out[-1][0] == "-":
                out.pop()
            else:
                out.append(SPACE)
        out += C.Block(0, [ln]).chars()
    return out


def strip_glyph(chars):
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


class Writer:
    def __init__(self):
        self.out, self.para, self.pending = [], None, []   # para: [chars, kind]

    def flush(self):
        if self.para:
            chars, kind = self.para
            txt = C.esc_start(style(chars))
            marker = {"●": "* ", "-": "- "}.get(kind)
            if marker:
                self.out += C.wrap(txt, "  ", marker) + [""]
            elif kind == "indented":
                self.out += C.wrap(txt, "   ") + [""]
            else:
                self.out += C.wrap(txt) + [""]
            self.para = None
        for c in self.pending:
            self.out += c
        self.pending = []

    def emit(self, lines):
        self.flush()
        self.out += lines

    def comment(self, blocks):
        c = C.comment_blocks(blocks)
        if self.para:
            self.pending.append(c)       # after the paragraph that runs across the page
        else:
            self.out += c

    def start(self, chars, kind):
        self.flush()
        self.para = [list(chars), kind]


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def flow(w, bs, counts):
    first = True
    for b in bs:
        f, t = b.font, b.text
        if f.color == YELLOW and f.size == 18:
            w.emit(heading(t, "="))
            counts["banners"] += 1
        elif f.size == 13 and b.x0 > 100:
            w.emit(heading(t, "-"))
            counts["headings"] += 1
        elif f.size == 16 and b.x0 > 150 and len(b.lines) > 3:            # centred list, line by line
            w.emit(["| " + C.esc_inline(ln.text) for ln in b.lines] + [""])
            counts["line blocks"] += 1
        elif any(GLYPH.match(ln.text) for ln in b.lines):                 # points, maybe after a lead-in
            groups, cur = [], None
            for ln in b.lines:
                m = GLYPH.match(ln.text)
                if m or cur is None:
                    cur = [m.group(1) if m else "para", [ln]]
                    groups.append(cur)
                else:
                    cur[1].append(ln)
            for kind, lines in groups:
                chars = line_chars(lines)
                w.start(strip_glyph(chars) if kind != "para" else chars, kind)
                counts[f"{kind} points" if kind != "para" else "paragraphs"] += 1
        elif first and w.para and w.para[1] == "para" and abs(b.x0 - BODY_X) < 3 and (
                ends_open(w.para[0]) or t[:1].islower()):
            w.para[0] += [SPACE] + b.chars()                               # continued from the previous page
            counts["joined across pages"] += 1
        elif abs(b.x0 - INDENT_X) < 3:
            w.start(b.chars(), "indented")
            counts["indented paragraphs"] += 1
        else:
            w.start(b.chars(), "para")
            counts["paragraphs"] += 1
        first = False


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    cover = [b for b in pages[1] if b.font.size >= 100]
    if len(cover) != 1:
        raise C.ConvertError("cover title not found")
    w = Writer()
    w.emit(heading(cover[0].text, "#", over=True))
    for p in sorted(pages):
        bs = [b for b in pages[p] if b not in cover]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.y0)
        body = [b for b in bs if b not in furniture]
        if p == 1:
            furniture = sorted(body + furniture, key=lambda b: b.y0)    # the cover's web address
            body = []
        flow(w, body, counts)
        w.comment(furniture)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=("●", "- "))


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Democracia 21 document", default_output="democracia21.rst"))
