#!/usr/bin/env python3
"""
compromis_front_valencia-pdf2rst.py - Convert the Compromís-Front Valencià
programme "Programa electoral 29N" (PDF, A4, in Valencian; version 2, of 18
November 2026: the first version, from Word, had another layout and is
converted by this script as it was in git before version 2) to
reStructuredText, repeatably, and verify that no text was altered, omitted or
added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph or a point; the text is not hyphenated, so a hyphen at
   a line end is real ("incloent-" / "hi") and stays.
2. Write RST:
     * the cover is an image: the document has no title of its own; "ÍNDEX"
       comes first (a bold paragraph)
     * the index -> a table (section | page), without the dot leaders; the
       chapters (orange, their page number apart) in bold
     * the 8 chapters ("1. UNA VIDA SEGURA: HABITATGE, SALARIS I PENSIONS",
       orange, maybe on two lines) -> level 1; their sections ("HABITATGE")
       -> level 2
     * the "-" points -> RST bullets and the "-" points under a point ->
       nested bullets (the dash, a glyph of its own beside the text, becomes
       the list marker)
     * a block that pdftotext read across two points (a dash beside its
       first line and another beside a later one: p12, p22, p41...) -> two
       points
     * paragraphs and points joined across page breaks. The text has no bold
       or italics (the body fonts are Manrope and Manrope-Medium, which look
       the same)
     * page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list dashes and the index's
   dot leaders are the only text set aside, and the line-end hyphens plain
   pdftotext drops are checked against ``pdftotext -layout``. pandoc must
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
BODY_X, DASH_X, TEXT_X, SUBDASH_X, SUBTEXT_X = 72.0, 90.0, 108.0, 126.0, 144.0
INDEX_ENTRY = re.compile(r"(.*?)\s*\.{4,}\s*(\d+)")


class Block(C.Block):
    """A text block whose line-end hyphens stay: the text is not hyphenated."""

    def chars(self, keep_hyphens=True):
        return super().chars(keep_hyphens)


def plain(chars):
    """The text as it is: no bold or italics in this programme."""
    return C.inline(chars, strong=lambda f: False, emph=lambda f: False)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def near(x, y, tol=3):
    return abs(x - y) < tol


def is_furniture(b):
    """Page number, at the bottom."""
    return b.y0 > 780 and b.font.name.startswith("ArialMT") and b.text.isdigit()


def join(chars, more):
    """Next block of the same paragraph (after a page break): a space, or
    nothing after a hyphen, which stays."""
    if not (chars and chars[-1][0] == "-"):
        chars.append(SPACE)
    chars += more


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def index_table(bs, report):
    """Chapters (orange, the page number a block of its own beside them) and
    sections (with dot leaders and the page number on the same line)."""
    chapters = [b for b in bs if b.font.color == ORANGE]
    nums = [b for b in chapters if b.text.isdigit()]
    rows = []
    for b in sorted((b for b in bs if b not in nums), key=lambda b: (b.page, b.y0)):
        if b in chapters:
            page = min((n for n in nums if n.page == b.page and abs(n.y0 - b.y0) < 2), key=lambda n: n.x0,
                       default=None)
            if page is None:
                raise C.ConvertError(f"index p{b.page}: chapter without page: {b.text!r}")
            nums.remove(page)
            rows.append([f"**{C.esc_start(plain(b.chars()))}**", C.esc_inline(page.text)])
            continue
        m = INDEX_ENTRY.fullmatch(b.text)
        if not m:
            raise C.ConvertError(f"index p{b.page}: entry not understood: {b.text!r}")
        rows.append([C.esc_start(plain(b.chars()[:len(m.group(1))])), C.esc_inline(m.group(2))])
    if nums:
        raise C.ConvertError(f"index page numbers without entry: {[n.text for n in nums]}")
    report.append(f"index: {len(rows)} entries")
    return C.list_table(rows, widths=[90, 10])


class Writer:
    """Paragraphs and points (with their own points), joined across pages."""

    def __init__(self):
        self.out, self.item, self.para, self.pending = [], None, None, []   # item: list of [kind, chars]

    def flush(self):
        if self.para is not None:
            self.out += C.wrap(C.esc_start(plain(self.para))) + [""]
            self.para = None
        if self.item is not None:
            self.out += C.wrap(C.esc_start(plain(self.item[0][1])), "  ", "- ") + [""]
            for kind, chars in self.item[1:]:
                txt = C.esc_start(plain(chars))
                self.out += {
                    "p": lambda: C.wrap(txt, "  "),
                    "sub": lambda: C.wrap(txt, "    ", "  - "),
                    "subp": lambda: C.wrap(txt, "    "),
                }[kind]() + [""]
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


def split_at_glyphs(bs, glyphs, counts):
    """pdftotext may read two points in a row as one block (p12, p22...): split
    a block before each of its lines that has a dash of its own beside it."""
    out = []
    for b in bs:
        if b in glyphs:
            out.append(b)
            continue
        cuts = [k for k, ln in enumerate(b.lines) if k
                and any(g.x1 < ln.x0 and abs(g.y0 - ln.y0) < 6 for g in glyphs)]
        start = 0
        for k in cuts + [len(b.lines)]:
            out.append(Block(b.page, b.lines[start:k], b.index, b.role, b.data))
            start = k
        counts["blocks split: two points read as one"] += len(cuts)
    return out


def flow(w, bs, counts):
    dashes = [b for b in bs if b.font.name.startswith("OpenSymbol") and all(l.text == DASH for l in b.lines)]
    glyphs = [Block(b.page, [ln], b.index, b.role, b.data) for b in dashes for ln in b.lines]  # p41: two in one block
    bs = split_at_glyphs([b for b in bs if b not in dashes] + glyphs, glyphs, counts)
    mates = {}
    for g in glyphs:                      # the point's text: the block beside its glyph
        m = min((b for b in bs if b not in glyphs and b.x0 > g.x1 and abs(b.y0 - g.y0) < 6),
                key=lambda b: (abs(b.y0 - g.y0), b.x0), default=None)
        if m is None or m in mates:
            raise C.ConvertError(f"p{g.page} y{g.y0:.0f}: {g.text} without its text")
        mates[m] = "sub" if near(g.x0, SUBDASH_X) else "point"
    first = True
    done = set()
    order = sorted((b for b in bs if b not in glyphs), key=lambda b: (b.y0, b.x0))
    for i, b in enumerate(order):
        if b in done:
            continue
        f, t, x = b.font, b.text, b.x0
        if f.name.startswith("Bacalar-Expanded") and f.size == 14 and f.color == ORANGE:
            parts = [b]                   # a title on two lines may be two blocks (p50)
            for o in order[i + 1:]:
                if o.font != f or o.y0 - parts[-1].y1 > f.size:
                    break
                parts.append(o)
            done.update(parts)
            w.emit(heading(" ".join(o.text for o in parts), "="))
            counts["chapters"] += 1
        elif f.name.startswith("Bacalar-SemiExpanded") and f.size == 12:
            w.emit(heading(t, "-"))
            counts["sections"] += 1
        elif not f.name.startswith("Manrope"):
            raise C.ConvertError(f"p{b.page} x{x:.0f} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        elif mates.get(b) == "point":
            w.flush()
            w.item = [["point", b.chars()]]
            counts["points"] += 1
        elif mates.get(b) == "sub":
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: nested point outside a point: {t[:50]!r}")
            w.item.append(["sub", b.chars()])
            counts["nested points"] += 1
        elif first and w.open_chars() is not None and (ends_open(w.open_chars()) or t[:1].islower()):
            join(w.open_chars(), b.chars())                          # continued from the previous page
            counts["joined across pages"] += 1
        elif w.item is not None and near(x, TEXT_X):
            w.item.append(["p", b.chars()])                          # a new paragraph of the point
            counts["further paragraphs in a point"] += 1
        elif w.item is not None and near(x, SUBTEXT_X) and w.item[-1][0] in ("sub", "subp"):
            w.item.append(["subp", b.chars()])
            counts["further paragraphs in a nested point"] += 1
        elif near(x, BODY_X):
            w.flush()
            w.para = b.chars()
            counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} x{x:.0f} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        first = False


def convert(pdf, report):
    blocks = [Block(b.page, b.lines, b.index, b.role, b.data) for b in C.extract(pdf)]
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    w = Writer()
    ip = [p for p in pages if any(b.text == "ÍNDEX" for b in pages[p])]
    if len(ip) != 1:
        raise C.ConvertError("index not found")
    ip = [p for p in pages if p >= ip[0] and all(is_furniture(b) or b.font.name.startswith("Bacalar")
                                                  for b in pages[p])]
    if ip != list(range(ip[0], ip[-1] + 1)):
        raise C.ConvertError(f"index pages not consecutive: {ip}")
    if any(p < ip[0] for p in pages):
        raise C.ConvertError("text before the index: the cover was an image")
    title = next(b for b in pages[ip[0]] if b.text == "ÍNDEX")
    w.emit(C.wrap(f"**{C.esc_inline(title.text)}**") + [""]
           + index_table([b for p in ip for b in pages[p] if b is not title and not is_furniture(b)], report)
           + [""])
    w.comment(sorted((b for p in ip for b in pages[p] if is_furniture(b)), key=lambda b: b.page))
    for p in sorted(pages):
        if p in ip:
            continue
        bs = pages[p]
        furniture = [b for b in bs if is_furniture(b)]
        flow(w, [b for b in bs if b not in furniture], counts)
        w.comment(furniture)
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
