#!/usr/bin/env python3
"""
pacma-pdf2rst.py - Convert the PACMA (Partido Animalista Con el Medio
Ambiente) programme "Compromiso electoral - Elecciones Generales 2026" (PDF,
Pages, A4) to reStructuredText, repeatably, and verify that no text was
altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block of this PDF is one paragraph or list item; a hyphen at a line end
   inside a block is joined like plain pdftotext joins it.
2. Write RST:
     * the cover is an image (no text), so the document has no title of its
       own: it starts with the index
     * ÍNDICE -> a table (number | section | page), without the dot leaders;
       entries that wrap to a second line are joined
     * green headings by size: "Presentación" and the 3 chapters ("1.
       Nuestro compromiso con los Animales") -> level 1; "1.1. ..." -> level
       2 (joined when they wrap); the bold "1.1.1. ..." -> level 3 and
       "1.1.8.1. ..." -> level 4 (number and title may be separate blocks)
     * "•" and "- " lists -> RST bullet lists ("*" and "-": the printed
       glyph becomes the list marker)
     * paragraphs and list items joined across page breaks; bold and
       italics kept; the PDF is not hyphenated, so a hyphen at a line end is
       kept ("higiénico-sanitarias", which plain pdftotext glues)
     * page numbers -> RST comments; so are an invisible "ff" at the bottom
       edge of page 6 and the slogan of page 5, whose font has no Unicode
       mapping (pdftotext reads it as "%&'( ..."): kept, not rendered
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list glyphs and the index's
   dot leaders are the only text set aside, and the line-end hyphens plain
   pdftotext drops are checked against ``pdftotext -layout``. pandoc must
   read the same words, without warnings.

Usage:  python3 pacma-pdf2rst.py PE_GENERALES_29N26.pdf -o pacma.rst
        python3 pacma-pdf2rst.py PE_GENERALES_29N26.pdf --verify-only pacma.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
GREEN = "#22d65d"
BULLET, DASH = "•", "-"
ITEM_X = 65                   # list items start at x 70.9; paragraphs at 49.6
NUMBER = r"\d+(?:\.\d+)+\."   # "1.1.8." / "1.1.8.1."
INDEX_ENTRY = re.compile(r"(.*?)\s*\.{3,}\s*(\d+)")
LEVEL = {2: "-", 3: "~", 4: "^"}


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic and not f.bold)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def is_furniture(b):
    """Page number, and the invisible text at the bottom edge of page 6."""
    return b.y0 > 810 and re.fullmatch(r"\d{1,2}", b.text) or b.font.size == 0


def unreadable(b):
    """Slogan in a font without Unicode mapping: pdftotext reads other glyphs."""
    return b.font.name.startswith("AcuminVariableConcept")


def text_of(b):
    """The block's characters; this PDF is not hyphenated, so a hyphen ending
    a line is real ("higiénico-" / "sanitarias") and stays."""
    return b.chars(keep_hyphens=True)


def first_green(b):
    ch = next((f for c, f in b.chars() if not c.isspace()), C.NOFONT)
    return ch.color == GREEN


def strip_glyph(chars):
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


class Writer:
    """Paragraphs and list items, joined across page breaks."""

    def __init__(self):
        self.out, self.para, self.pending = [], None, []      # para: [chars, kind]

    def flush(self):
        if self.para:
            chars, kind = self.para
            txt = C.esc_start(style(chars))
            marker = {"bullet": "* ", "dash": "- "}.get(kind)
            self.out += (C.wrap(txt, "  ", marker) if marker else C.wrap(txt)) + [""]
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


def index_table(pages, ip):
    """The index pages: lines with dot leaders and page numbers -> rows."""
    title = next(b for b in pages[ip[0]] if b.font.size == 16)
    lines = [ln for p in ip for b in pages[p] if b is not title and not is_furniture(b) for ln in b.lines]
    rows, entry = [], []
    for ln in lines:
        entry.append(ln.text)
        m = INDEX_ENTRY.fullmatch(" ".join(entry))
        if not m:
            continue
        text, page = m.groups()
        n = re.match(r"(\d+(?:\.\d+)*) (.*)", text)
        num, name = (n.groups() if n else ("", text))
        rows.append([C.esc_inline(num), C.esc_start(C.esc_inline(name)), C.esc_inline(page)])
        entry = []
    if entry:
        raise C.ConvertError(f"index entry without page: {' '.join(entry)!r}")
    return heading(title.text, "=") + C.list_table(rows, widths=[10, 80, 10]), title, len(rows)


def flow(w, bs, counts):
    first = True
    skip = set()
    prev = None
    for b in bs:
        if b in skip:
            continue
        t = b.text
        green = b.font.color == GREEN
        if green and b.font.size in (14, 16):                            # Presentación, chapters
            w.emit(heading(t, "="))
            counts["level 1"] += 1
        elif green and b.font.size == 12:                                # "1.1. ..." (may wrap)
            parts = [b]
            for o in bs:
                if o.font.color == GREEN and o.font.size == 12 and 0 <= o.y0 - parts[-1].y1 < 5:
                    parts.append(o)
            skip.update(parts)
            text = " ".join(o.text for o in parts)
            if not re.match(r"\d+\.\d+\.", text):           # "2.10.Contención": as printed
                raise C.ConvertError(f"p{b.page}: level 2 heading without its number: {text!r}")
            w.emit(heading(text, "-"))
            counts["level 2"] += 1
        elif green and re.fullmatch(NUMBER, t):                          # "1.1.1." + title block
            mate = next((o for o in bs if o is not b and abs(o.y0 - b.y0) < 3 and o.x0 > b.x1), None)
            if mate is None:
                raise C.ConvertError(f"p{b.page}: heading number {t} without its title")
            skip.add(mate)
            depth = t.count(".")
            w.emit(heading(f"{t} {mate.text}", LEVEL[depth]))
            counts[f"level {depth}"] += 1
        elif first_green(b) and re.match(NUMBER + " ", t):              # "1.1.10. Creación..." in one block
            depth = t.split(" ", 1)[0].count(".")
            w.emit(heading(t, LEVEL[depth]))
            counts[f"level {depth}"] += 1
        elif b.x0 > ITEM_X and t.startswith(BULLET + " "):
            w.start(strip_glyph(text_of(b)), "bullet")
            counts["• items"] += 1
        elif b.x0 > ITEM_X and t.startswith(DASH + " "):
            w.start(strip_glyph(text_of(b)), "dash")
            counts["- items"] += 1
        elif first and w.para and (b.x0 > ITEM_X) == (w.para[1] != "para") and (
                not "".join(c for c, _ in w.para[0]).rstrip().endswith(C.TERMINAL) or t[:1].islower()):
            w.para[0] += [SPACE] + text_of(b)                              # continued from the previous page
            counts["joined across pages"] += 1
        elif b.x0 > ITEM_X:
            raise C.ConvertError(f"p{b.page}: indented block that is not a list item: {t[:60]!r}")
        else:
            w.start(text_of(b), "para")
            counts["paragraphs"] += 1
        first = False
        prev = b
    return prev


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    ip = [p for p in pages if any(INDEX_ENTRY.fullmatch(ln.text) for b in pages[p] for ln in b.lines)]
    if not ip or ip != list(range(ip[0], ip[-1] + 1)):
        raise C.ConvertError(f"index pages not found or not consecutive: {ip}")
    w = Writer()
    unread = []
    for p in sorted(pages):
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: (b.font.size == 0, b.y0))
        odd = sorted((b for b in bs if unreadable(b)), key=lambda b: b.y0)
        body = [b for b in bs if b not in furniture and b not in odd]
        if p == ip[0]:
            lines, title, n = index_table(pages, ip)
            w.emit(lines)
            counts["index entries"] = n
        elif p in ip:
            pass
        elif body:
            flow(w, body, counts)
        if odd:
            unread.append(f"p{p}: {' '.join(b.text for b in odd)!r}")
            w.emit(C.comment_blocks(odd))
        w.comment(furniture)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    if unread:
        report.append("text in a font without Unicode mapping, kept in RST comments: " + "; ".join(unread))
    return "\n".join(out) + "\n"


def verify(rst, raw, report, pdf=None):
    breaks = C.layout_hyphen_breaks(pdf) if pdf else []
    return C.verify_blocks(rst, raw, report, hyphen_breaks=breaks,
                           strip_start=(BULLET, DASH + " "), leaders=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the PACMA programme", default_output="pacma.rst"))
