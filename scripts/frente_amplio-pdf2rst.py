#!/usr/bin/env python3
"""
frente_amplio-pdf2rst.py - Convert the Frente Amplio programme "Un programa
como una casa - Elecciones generales 29N 2026" (PDF, InDesign, A4) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph or a point; the text is hyphenated, and a hyphen at a
   line end inside a block is joined like plain pdftotext joins it (also
   across a page break).
2. Write RST:
     * the cover and the introduction's opening page are images: the
       document has no title of its own; the copyright line comes first
     * "Contenido" (a bold paragraph, so that the introduction that follows
       is not inside it) -> a table (number | section | page), with the
       chapters as rows of their own
     * version 3 (23 November 2026) adds section 2.9 ("El deporte como
       derecho"): its index number has no final dot ("2.9") and one of its
       subheadings is set in Inter 12 (red) instead of MarrSans-Medium
     * the 18 chapters (a page with a big number and title: "01" +
       "Acabar con la especulación...") -> level 1; "1.1. ..." -> level 2;
       the red subheadings -> level 3
     * "●" points -> RST bullets, their "▶" points -> nested bullets and
       the "▷" points under those ("4 niños y niñas entre 0 y 1 año") ->
       bullets nested once more (glyphs dropped; the text is a block of its
       own beside the glyph)
     * paragraphs and points joined across page breaks; bold and italics
       kept
     * running head ("PROGRAMA ELECTORAL FRENTE AMPLIO", the chapter's
       title) and page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list glyphs are the only
   text set aside, and a word cut at a page break is joined (plain pdftotext
   leaves its hyphen: it joins only the lines inside a block). pandoc must
   read the same words, without warnings.

Usage:  python3 frente_amplio-pdf2rst.py ProgramaFrenteAmplio.pdf -o frente_amplio.rst
        python3 frente_amplio-pdf2rst.py ProgramaFrenteAmplio.pdf --verify-only frente_amplio.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
RED = "#ef3b12"
BULLET, SUBBULLET, SUBSUBBULLET = "●", "▶", "▷"
ITEM_X, SUB_X = 124.7, 136.1                       # text of "●" points, of "▶" points


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def near(x, y, tol=3):
    return abs(x - y) < tol


def is_furniture(b):
    """Running head and page number (small red, at the top)."""
    return b.y0 < 80 and b.font.name.startswith("MarrSansCond-Semibold") and b.font.size == 9


def join(chars, more):
    """Next line of the same paragraph: like pdftotext, a hyphen ending the
    line is dropped and the halves joined; otherwise a space."""
    if chars and chars[-1][0] == "-":
        chars.pop()
    else:
        chars.append(SPACE)
    chars += more


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def index_rows(bs, report):
    """Index pages: chapters (red, left column) and sections (number, title,
    page in three columns; the page beside one of the title's lines, mostly
    the last)."""
    small = [b for b in bs if b.font.size == 9 and not b.font.color == RED]
    nums = [b for b in small if re.fullmatch(r"\d+\.\d+\.?", b.text)]    # "2.9" (version 3) without its dot
    pages = [b for b in bs if re.fullmatch(r"\d{1,3}", b.text)]
    chapters = [b for b in bs if b.font.color == RED and b.font.size == 11 and b not in pages]
    titles = [b for b in small if b not in nums + pages]
    rows = []
    used = set()
    for t in titles + chapters:
        num = next((n for n in nums if n.page == t.page and abs(n.y0 - t.y0) < 2 and n.x1 < t.x0), None)
        page = min((p for p in pages if p.page == t.page and p.x0 > t.x1 and p not in used
                    and t.y0 - 2 <= p.y0 and p.y1 <= t.y1 + 3),
                   key=lambda p: abs(p.y1 - t.y1), default=None)      # on the last line, mostly
        if t in titles and page is None:
            raise C.ConvertError(f"index p{t.page}: {t.text!r} without its page")
        used.update(x for x in (t, num, page) if x is not None)
        rows.append((t.page, t.y0, t in chapters, num, t, page))
    left = [b for b in bs if b not in used]
    if left:
        raise C.ConvertError(f"index: blocks left over {[b.text for b in left]}")
    table = []
    for _, _, chapter, num, t, page in sorted(rows, key=lambda r: (r[0], r[1], not r[2])):
        name = C.esc_start(style(t.chars()))
        table.append([C.esc_inline(num.text) if num else "",
                      name,                                    # chapters: bold by their font
                      C.esc_inline(page.text) if page else ""])
    report.append(f"index: {sum(not r[2] for r in rows)} sections, {sum(r[2] for r in rows)} chapters, "
                  f"{sum(r[3] is None and not r[2] for r in rows)} section(s) printed without a number")
    return table


class Writer:
    """Paragraphs and points, joined across page breaks."""

    def __init__(self):
        self.out, self.item, self.para, self.pending = [], None, None, []   # item: list of [kind, chars]

    def flush(self):
        if self.para is not None:
            self.out += C.wrap(C.esc_start(style(self.para))) + [""]
            self.para = None
        if self.item is not None:
            self.out += C.wrap(C.esc_start(style(self.item[0][1])), "  ", "* ") + [""]
            for kind, chars in self.item[1:]:
                txt = C.esc_start(style(chars))
                if kind == "sub":
                    self.out += C.wrap(txt, "    ", "  * ") + [""]
                elif kind == "subsub":
                    self.out += C.wrap(txt, "      ", "    * ") + [""]
                else:
                    self.out += C.wrap(txt, "  ") + [""]
            self.item = None
        for c in self.pending:
            self.out += c
        self.pending = []

    def emit(self, lines):
        self.flush()
        self.out += lines

    def comment(self, blocks):
        c = C.comment_blocks(blocks)
        if self.para is not None or self.item is not None:
            self.pending.append(c)       # after the paragraph that runs across the page
        else:
            self.out += c

    def open_chars(self):
        if self.para is not None:
            return self.para
        return self.item[-1][1] if self.item is not None else None


def flow(w, bs, counts):
    glyphs = [b for b in bs if b.text in (BULLET, SUBBULLET, SUBSUBBULLET)]
    mates = {}
    for g in glyphs:                      # the point's text: the block beside its glyph
        m = min((b for b in bs if b not in glyphs and b.x0 > g.x1 and abs(b.y0 + 3 - g.y0) < 5),
                key=lambda b: abs(b.y0 + 3 - g.y0), default=None)
        if m is None or m in mates:
            raise C.ConvertError(f"p{g.page} y{g.y0:.0f}: {g.text} without its text")
        mates[m] = g.text
    first = True
    for b in sorted((b for b in bs if b not in glyphs), key=lambda b: b.y0):
        f, t = b.font, b.text
        if f.name.startswith("MarrSansCond") and f.size >= 60:
            num = next((o for o in bs if o.font.size == 32 and o.y0 < b.y0), None)
            if num is None:
                raise C.ConvertError(f"p{b.page}: chapter title without its number")
            w.emit(heading(f"{num.text} {t}", "="))
            counts["chapters"] += 1
        elif f.name.startswith("MarrSansCond") and f.size == 32:
            pass                                                     # with the chapter's title
        elif f.name.startswith("MarrSans-Medium") and f.size == 15:
            w.emit(heading(t, "-"))
            counts["sections"] += 1
        elif f.name.startswith("MarrSans-Medium") and f.size == 12 \
                or f.name.startswith("Inter") and f.size == 12 and f.color == RED:   # p43 of version 3
            w.emit(heading(t, "~"))
            counts["subheadings"] += 1
        elif mates.get(b) == BULLET:
            w.flush()
            w.item = [["p", b.chars()]]
            counts["● points"] += 1
        elif mates.get(b) == SUBBULLET:
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: ▶ point outside a point: {t[:50]!r}")
            w.item.append(["sub", b.chars()])
            counts["▶ points"] += 1
        elif mates.get(b) == SUBSUBBULLET:
            if w.item is None or w.item[-1][0] not in ("sub", "subsub"):
                raise C.ConvertError(f"p{b.page}: ▷ point outside a ▶ point: {t[:50]!r}")
            w.item.append(["subsub", b.chars()])
            counts["▷ points"] += 1
        elif first and w.open_chars() is not None and (ends_open(w.open_chars()) or t[:1].islower()):
            join(w.open_chars(), b.chars())                          # continued from the previous page
            counts["joined across pages"] += 1
        elif w.item is not None and (near(b.x0, ITEM_X) or near(b.x0, SUB_X)):
            kind = "sub" if near(b.x0, SUB_X) else "p"
            w.item.append([kind if kind == "p" else "sub", b.chars()])     # a new paragraph of the point
            counts["further paragraphs in a point"] += 1
        elif f.name.startswith("Inter") and f.size in (8, 10) and b.x0 < SUB_X - 3:     # also text at a
            # point's indentation with no point open (p140, after a heading): a paragraph
            w.flush()
            w.para = b.chars()
            counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        first = False


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    ip = [p for p in pages if any(b.text == "Contenido" for b in pages[p])]
    if len(ip) != 1:
        raise C.ConvertError("index not found")
    ip = [p for p in pages if ip[0] <= p and all(b.font.size in (9, 11, 20) for b in pages[p])]
    w = Writer()
    for p in sorted(pages):
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.x0)
        body = [b for b in bs if b not in furniture]
        if p == ip[0]:
            title = next(b for b in body if b.text == "Contenido")
            rows = index_rows([b for q in ip for b in pages[q] if b is not title and not is_furniture(b)], report)
            w.emit(C.wrap(f"**{C.esc_inline(title.text)}**") + [""] + C.list_table(rows, widths=[8, 82, 10]))
        elif p in ip:
            pass
        else:
            flow(w, body, counts)
        w.comment(furniture)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=(BULLET, SUBBULLET, SUBSUBBULLET),
                           block_end_hyphens=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Frente Amplio programme", default_output="frente_amplio.rst"))
