#!/usr/bin/env python3
"""
compromis_front_valencia-pdf2rst.py - Convert the Compromís-Front Valencià
programme "Programa electoral 29N" (PDF, Word, A4, in Valencian) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for words and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Word
   exported the lines in blocks of one or two, so the lines are rebuilt from
   the words' positions and the paragraphs from the line spacing (16.7 pt
   inside a paragraph, 33 between paragraphs).
2. Write RST:
     * "PROGRAMA ELECTORAL 29N" and "COMPROMÍS-FRONT VALENCIÀ" -> document
       title and subtitle
     * the index that follows them -> a table (section | page), without the
       dot leaders; entries that wrap to a second line are joined; the
       chapters (orange and larger there) in bold
     * the 8 chapters ("1. UNA VIDA SEGURA: HABITATGE, SALARIS I PENSIONS",
       orange) -> level 1; their sections ("HABITATGE") -> level 2
     * the "-" points -> RST bullets and the "-" points under a point ->
       nested bullets (the dash becomes the list marker)
     * paragraphs and points joined across page breaks; a hyphen at a line
       end is real ("eliminant-" / "la": the text is not hyphenated) and
       stays. The text has no bold or italics (the body font is
       Manrope-Medium) and the pages no numbers or running heads
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list dashes and the index's
   dot leaders are the only text set aside, and the line-end hyphen plain
   pdftotext drops is checked against ``pdftotext -layout``. pandoc must
   read the same words, without warnings.

Usage:  python3 compromis_front_valencia-pdf2rst.py programa-compromis-front-valencia.pdf -o compromis_front_valencia.rst
        python3 compromis_front_valencia-pdf2rst.py programa-compromis-front-valencia.pdf --verify-only compromis_front_valencia.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
ORANGE = "#ff5500"
DASH = "-"
PARA_GAP = 25.0               # line pitch 16.7 inside a paragraph, 33 between paragraphs
BODY_X, DASH_X, TEXT_X, SUBDASH_X, SUBTEXT_X = 72.0, 90.0, 108.0, 126.0, 144.0
INDEX_ENTRY = re.compile(r"(.*?)\s*\.{4,}\s*(\d+)")


def plain(chars):
    """The text as it is: no bold or italics in this programme."""
    return C.inline(chars, strong=lambda f: False, emph=lambda f: False)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def near(x, y, tol=3):
    return abs(x - y) < tol


def page_lines(blocks):
    """Lines rebuilt from the words of each page: same baseline, left to right."""
    words = collections.defaultdict(list)
    for b in blocks:
        for ln in b.lines:
            words[b.page] += ln.words
    pages = {}
    for p, ws in sorted(words.items()):
        lines = []
        for w in sorted(ws, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
            mid = (w.y0 + w.y1) / 2
            if lines and abs(mid - lines[-1][0]) < 3:
                lines[-1][1].append(w)
            else:
                lines.append([mid, [w]])
        pages[p] = [C.Block(p, [C.Line(sorted(lw, key=lambda w: w.x0))]) for _, lw in lines]
    return pages


def add_line(chars, more):
    """Next line of the same paragraph: a space, or nothing after a hyphen
    ("eliminant-" + "la"), which stays."""
    if not (chars and chars[-1][0] == "-"):
        chars.append(SPACE)
    chars += more


def strip_dash(chars):
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


def index_table(lines, report):
    """Entries with dot leaders and page numbers -> rows; an entry may wrap."""
    rows, entry = [], []
    for b in lines:
        entry += ([SPACE] if entry else []) + b.chars()
        m = INDEX_ENTRY.fullmatch("".join(c for c, _ in entry))
        if not m:
            continue
        name = C.esc_start(plain(entry[:len(m.group(1))]))
        if entry[0][1].name.startswith("Bacalar"):               # a chapter (orange, larger)
            name = f"**{name}**"
        rows.append([name, C.esc_inline(m.group(2))])
        entry = []
    if entry:
        raise C.ConvertError(f"index entry without page: {''.join(c for c, _ in entry)!r}")
    report.append(f"index: {len(rows)} entries")
    return C.list_table(rows, widths=[90, 10])


class Writer:
    """Paragraphs and points (with their own points), joined across pages."""

    def __init__(self):
        self.out, self.item, self.para = [], None, None     # item: list of [kind, chars]

    def flush(self):
        if self.para is not None:
            self.out += C.wrap(C.esc_start(plain(self.para))) + [""]
            self.para = None
        if self.item is not None:
            self.out += C.wrap(C.esc_start(plain(self.item[0][1])), "  ", "- ") + [""]
            for kind, chars in self.item[1:]:
                txt = C.esc_start(plain(chars))
                self.out += (C.wrap(txt, "    ", "  - ") if kind == "sub" else C.wrap(txt, "  ")) + [""]
            self.item = None

    def emit(self, lines):
        self.flush()
        self.out += lines


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def flow(w, bs, counts):
    prev = None                       # previous line on this page
    i = 0
    while i < len(bs):
        b = bs[i]
        f, t = b.font, b.text
        gap = b.y0 - prev.y0 if prev is not None else None
        if f.name.startswith("Bacalar") and f.size in (20, 16):
            parts = [b]                                          # a heading, maybe on several lines
            while i + 1 < len(bs) and bs[i + 1].font == f and bs[i + 1].y0 - parts[-1].y1 < f.size:
                i += 1
                parts.append(bs[i])
            w.emit(heading(" ".join(x.text for x in parts), "=" if f.size == 20 else "-"))
            counts["chapters" if f.size == 20 else "sections"] += 1
            b = parts[-1]
        elif near(b.x0, DASH_X) and t.startswith(DASH + " "):
            w.flush()
            w.item = [["p", strip_dash(b.chars())]]
            counts["points"] += 1
        elif near(b.x0, SUBDASH_X) and t.startswith(DASH + " "):
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: nested point outside a point: {t[:50]!r}")
            w.item.append(["sub", strip_dash(b.chars())])
            counts["nested points"] += 1
        elif w.item is not None and (near(b.x0, TEXT_X) or near(b.x0, SUBTEXT_X)):
            kind = "sub" if near(b.x0, SUBTEXT_X) else "p"
            if w.item[-1][0] != kind or gap is not None and gap > PARA_GAP:
                w.item.append([kind, b.chars()])                 # a new paragraph of the point
                counts["further paragraphs in a point"] += 1
            else:
                add_line(w.item[-1][1], b.chars())
        elif near(b.x0, BODY_X) and f.name.startswith("Manrope"):
            if w.para is not None and (gap is not None and gap < PARA_GAP
                                       or gap is None and (ends_open(w.para) or t[:1].islower())):
                add_line(w.para, b.chars())
            else:
                w.flush()
                w.para = b.chars()
                counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} y{b.y0:.0f}: line not understood ({f}): {t[:60]!r}")
        prev = b
        i += 1


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = page_lines(blocks)
    counts = collections.Counter()
    w = Writer()
    first = pages[min(pages)]
    title = [b for b in first if b.font.size == 21]
    sub = [b for b in first if b.font.name.startswith("Bacalar-UltraExpanded")]
    if len(title) != 1 or len(sub) != 1:
        raise C.ConvertError("title and subtitle not found on the first page")
    w.emit(heading(title[0].text, "#", over=True) + heading(sub[0].text, "*", over=True))
    ip = [p for p in pages if any(INDEX_ENTRY.fullmatch(b.text) for b in pages[p])]
    if not ip or ip != list(range(ip[0], ip[-1] + 1)) or ip[0] != min(pages):
        raise C.ConvertError(f"index pages not found or not consecutive: {ip}")
    w.emit(index_table([b for p in ip for b in pages[p] if b not in title + sub], report))
    for p in sorted(pages):
        if p not in ip:
            flow(w, pages[p], counts)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report, pdf=None):
    breaks = C.layout_hyphen_breaks(pdf) if pdf else []
    return C.verify_blocks(rst, raw, report, hyphen_breaks=breaks, strip_start=DASH, leaders=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Compromís-Front Valencià programme",
                    default_output="compromis_front_valencia.rst"))
