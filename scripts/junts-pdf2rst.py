#!/usr/bin/env python3
"""
junts-pdf2rst.py - Convert the Junts per Catalunya programme "Catalunya al
centre - Programa electoral 29N" (PDF, InDesign, two columns, in Catalan) to
reStructuredText, repeatably, and verify that no text was altered, omitted or
added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). pdftotext
   cuts the paragraphs into blocks where a big number sits ("5. Requisit del
   català ... obtenir la" / "facultat legal ..."), gives the numbers as
   blocks of their own or inside the text, and some words of a justified line
   as blocks of their own; so the text is rebuilt line by line, column by
   column: a line starts a new paragraph after a gap, or when it starts with
   a point's number or its "·". The text is not hyphenated: a hyphen at a
   line end is real ("aprovar-" / "ne") and stays.
2. Write RST:
     * "Catalunya al centre" (under "Índex") and "Programa electoral" (the
       cover) -> document title and subtitle; the other lines of the cover
       ("Junts per Catalunya", "29N", "Eleccions generals ...") -> paragraphs
     * "Índex" -> a bold paragraph; its parts -> bullets, their sections ("•")
       -> nested bullets
     * the big titles ("Introducció", "Bloc I. ...", "Annex. ...") -> level 1;
       the other big titles ("Retiment de comptes: ...") and the sections of
       a block ("Els nostres drets i llibertats nacionals i la justícia al
       centre.") -> level 2, and so are the numbered parts of the
       introduction ("1. Catalunya i els catalans al centre."); the green
       headings in a section ("2. Competències", "Fiscalitat, autònoms i
       activitat econòmica.") and those of "Catalunya al centre" ("Un país que
       s'ha fet a si mateix.") -> level 3; "Competències a protegir.",
       "Objectiu 1: ..." -> level 4
     * the numbered points ("1.", "1.1.", the number big and green, or in
       plain type after a sentence's end: p20, p47, p59) -> bullets keeping
       their number; the "•" points and the annex's "·" points ->
       bullets (the glyph dropped)
     * "Catalunya al centre. Tu, al centre." -> a bold paragraph
     * paragraphs joined across columns and pages; bold kept
     * running head ("PROGRAMA ELECTORAL 29-N"), footer ("Junts per
       Catalunya") and page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the "•" and "·" of the lists are
   the only text set aside, and the line-end hyphens plain pdftotext drops
   are checked against the lines' positions. pandoc must read the same
   words, without warnings.

Usage:  python3 junts-pdf2rst.py PROGRAMA-ELECTORAL_JUNTS_29N.pdf -o junts.rst
        python3 junts-pdf2rst.py PROGRAMA-ELECTORAL_JUNTS_29N.pdf --verify-only junts.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
BULLET, DOT = "•", "·"
COLUMN_X = 318                     # left column from 55, right from 330
PARA_GAP = 3                       # a line more than this below the previous one: a new paragraph
NUMBER = re.compile(r"\d+(\.\d+)*\.")
LEVEL1 = ("Introducció", "Bloc ", "Annex")


class Block(C.Block):
    """Line-end hyphens stay: the text is not hyphenated."""

    def chars(self, keep_hyphens=True):
        return super().chars(keep_hyphens)


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def paragraph(chars):
    return C.wrap(C.esc_start(style(chars))) + [""]


def strip_glyph(chars):
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


def is_furniture(b):
    """Running head, footer and page number."""
    return b.y0 < 56 or b.y0 > 825


def is_body(b):
    return b.font.name.startswith("Archivo") and not b.font.name.startswith("ArchivoExtra") and b.font.size == 10


def is_number(b):
    """A point's number, big and green, as a block of its own ("1.", "1.3.")."""
    return b.font.name.startswith("ArchivoExtraCondensed-ExtraBold") and NUMBER.fullmatch(b.text)


def heading_level(b):
    f, t = b.font, b.text
    if f.size >= 100:
        return "=" if t.startswith(LEVEL1) else "-"
    if f.name.startswith("ArchivoExtraCondensed") and f.size == 28:
        return "-"
    if f.name.startswith("ArchivoExtraCondensed-ExtraBold") and f.size == 21:
        return "-"                                    # the numbered parts of the introduction
    if f.name.startswith("ArchivoExtraCondensed-ExtraBold") and f.size == 22:
        return "~"
    if f.name.startswith("ArchivoExtraCondensed") and f.size == 16:
        return "~"
    if f.name.startswith("ArchivoExtraCondensed") and f.size == 17:
        return "^"
    return None


class Unit:
    """A heading block, or one text line (maybe with its number before it)."""

    def __init__(self, page, y0, y1, chars, kind, level=None, starts=False, col=0):
        self.page, self.y0, self.y1, self.chars, self.kind, self.level = page, y0, y1, chars, kind, level
        self.col = col
        self.starts = starts                          # a point's first line
        # a number in plain type (p20, p47, p59): a point's first line if the line before ended a sentence
        self.maybe_starts = kind == "line" and bool(re.match(r"\d+(\.\d+)*\. \S", self.text))

    @property
    def text(self):
        return "".join(c for c, _ in self.chars)


def page_units(p, bs, counts):
    """The page's headings and text lines, column by column, top to bottom."""
    columns = ([], [])
    numbers = [b for b in bs if is_number(b)]
    lines = []                                        # (column, y0, y1, x0, [words' line])
    for b in bs:
        if b in numbers:
            continue
        col = 0 if b.x0 < COLUMN_X else 1
        lvl = heading_level(b)
        if lvl:
            columns[col].append(Unit(p, b.y0, b.y1, b.chars(), "heading", lvl, col=col))
            continue
        if not (is_body(b) or b.font.name.startswith("ArchivoExtraCondensed") and b.font.size == 24):
            raise C.ConvertError(f"p{p} x{b.x0:.0f} y{b.y0:.0f}: block not understood ({b.font}): {b.text[:60]!r}")
        if b.font.size == 24:                         # "Catalunya al centre. Tu, al centre."
            columns[col].append(Unit(p, b.y0, b.y1, b.chars(), "slogan", col=col))
            continue
        for ln in b.lines:
            one = Block(p, [ln])
            lvl = heading_level(one)
            if lvl and not NUMBER.fullmatch(one.text):      # p81: "3. Objectius" in a block of text
                columns[col].append(Unit(p, one.y0, one.y1, one.chars(), "heading", lvl, col=col))
                counts["headings inside a text block"] += 1
            else:
                lines.append([col, ln])
    # words of one justified line that pdftotext put in blocks of their own (p23, p25)
    merged = []
    for col, ln in sorted(lines, key=lambda t: (t[0], t[1].y1, t[1].x0)):
        prev = merged[-1] if merged else None
        if prev and prev[0] == col and abs(prev[1][-1].y1 - ln.y1) < 1.5 and ln.x0 > prev[1][-1].x1:
            prev[1].append(ln)
            counts["pieces of a line put back"] += 1
        else:
            merged.append([col, [ln]])
    for col, parts in merged:
        chars = []
        for ln in parts:
            if chars:
                chars.append(SPACE)
            chars += Block(p, [ln]).chars()
        first = parts[0]
        y0, y1 = min(l.y0 for l in parts), max(l.y1 for l in parts)
        num = [n for n in numbers if (0 if n.x0 < COLUMN_X else 1) == col
               and n.y0 - 2 <= y0 <= n.y1 and first.x0 >= n.x1 - 1]
        starts = False
        if num:
            n = min(num, key=lambda n: abs(n.y0 - y0))
            numbers.remove(n)
            chars = n.chars() + [SPACE] + chars
            starts = True
            counts["numbers apart, put before their text"] += 1
        else:
            words = "".join(c for c, _ in chars).split()
            lead = chars[0][1] if chars else None
            if words and NUMBER.fullmatch(words[0]) and lead and lead.name.startswith("ArchivoExtraCondensed"):
                starts = True                         # the number inside the line
            elif chars and chars[0][0] in (DOT, BULLET):
                starts = True                         # the annex's "·" points, the "•" points (p15, p23)
        columns[col].append(Unit(p, y0, y1, chars, "line", starts=starts, col=col))
    if numbers:
        raise C.ConvertError(f"p{p}: numbers without their text: {[n.text for n in numbers]}")
    return [u for col in columns for u in sorted(col, key=lambda u: u.y0)]


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def add_line(chars, more):
    """Next line of the same paragraph: a space, or nothing after a hyphen,
    which stays; a dash with a space before it ("Services Act -" / "DSA)")
    keeps a space after it too."""
    if not (chars and chars[-1][0] == "-" and not (len(chars) > 1 and chars[-2][0].isspace())):
        chars.append(SPACE)
    chars += more


def index_lines(bs):
    """The index: parts (green) and their sections ("• ...", maybe wrapping)."""
    out = []
    items = None
    for b in sorted(bs, key=lambda b: b.y0):
        if b.font.size == 15:
            if items is not None:
                out += items
            out += C.wrap(C.esc_start(style(b.chars())), "  ", "* ") + [""]
            items = []
            continue
        sections = []
        for ln in b.lines:
            ch = Block(b.page, [ln]).chars()
            if ch and ch[0][0] == BULLET:
                sections.append(strip_glyph(ch))
            elif sections:
                add_line(sections[-1], ch)
            else:
                raise C.ConvertError(f"index: line not understood: {ln.text!r}")
        for s in sections:
            items += C.wrap(C.esc_start(style(s)), "    ", "  * ") + [""]
    return out + (items or [])


def convert(pdf, report):
    blocks = [Block(b.page, b.lines, b.index, b.role, b.data) for b in C.extract(pdf)]
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    first = min(pages)
    cover = sorted((b for b in pages[first] if not is_furniture(b)), key=lambda b: b.y0)
    sub = [b for b in cover if b.text == "Programa electoral"]
    ip = [p for p in pages if any(b.text == "Índex" for b in pages[p])]
    if len(sub) != 1 or len(ip) != 1:
        raise C.ConvertError("cover or index not found")
    title = [b for b in pages[ip[0]] if not is_furniture(b) and b.text != "Índex"]
    if len(title) != 1:
        raise C.ConvertError("title (under 'Índex') not found")
    out = heading(title[0].text, "#", over=True) + heading(sub[0].text, "*", over=True)
    for b in cover:
        if b is not sub[0]:
            out += paragraph(b.chars())
    entries = ip[0] + 1                               # the index's entries: the next page
    para = None                                       # [kind, chars]
    pending = []

    def flush():
        nonlocal para, pending, out
        if para is not None:
            kind, chars = para
            if kind == "point":
                out += C.wrap(C.esc_start(style(chars)), "  ", "* ") + [""]
                counts["points"] += 1
            else:
                out += paragraph(chars)
                counts["paragraphs"] += 1
            para = None
        for c in pending:
            out += c
        pending = []

    for p in sorted(pages):
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: (b.y0, b.x0))
        body = [b for b in bs if b not in furniture]
        if p == first:
            pass
        elif p == ip[0]:
            out += C.wrap(f"**{C.esc_inline('Índex')}**") + [""]
        elif p == entries:
            out += index_lines(body)
        else:
            prev = None
            for u in page_units(p, body, counts):
                if prev is not None and u.col != prev.col:
                    prev = None                       # a new column: nothing above to measure from
                if u.kind == "heading":
                    flush()
                    out += heading(u.text, u.level)
                    counts["headings"] += 1
                    prev = None
                elif u.kind == "slogan":
                    flush()
                    out += C.wrap(f"**{C.esc_start(C.esc_inline(u.text))}**") + [""]
                elif u.starts or u.maybe_starts and para is not None and not ends_open(para[1]):
                    if not u.starts:
                        counts["points numbered in plain type"] += 1
                    flush()
                    chars = strip_glyph(u.chars) if u.chars[0][0] in (DOT, BULLET) else u.chars
                    para = ["point", chars]
                    prev = u
                elif para is not None and (prev is not None and u.y0 - prev.y1 <= PARA_GAP
                                           or prev is None and (ends_open(para[1]) or u.text[:1].islower())):
                    if prev is None:
                        counts["paragraphs joined across columns and pages"] += 1
                    add_line(para[1], u.chars)
                    prev = u
                else:
                    flush()
                    para = ["para", list(u.chars)]
                    prev = u
        c = C.comment_blocks(furniture)
        if para is not None:
            pending.append(c)
        else:
            out += c
    flush()
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report, pdf=None):
    # a spaced dash ("Act -" / "DSA)") counts as the dash alone: lines compare without spaces
    breaks = [(left.split()[-1], right) for left, right in C.line_hyphen_breaks(pdf)] if pdf else []
    return C.verify_blocks(rst, raw, report, hyphen_breaks=breaks, strip_start=(BULLET, DOT))


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Junts programme", default_output="junts.rst"))
