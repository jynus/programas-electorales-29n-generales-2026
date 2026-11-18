#!/usr/bin/env python3
"""
podemos-pdf2rst.py - Convert the Podemos programme "Así de básico: Paz, casa y
trabajo." (PDF, InDesign, A4; version 2 of the programme: version 1 was the
web page, converted by podemos-html2rst.py) to reStructuredText, repeatably,
and verify that no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph, a measure or a point; the text is hyphenated, and a
   hyphen at a line end inside a block is joined like plain pdftotext joins
   it (also across a page break).
2. Write RST:
     * the cover ("Así de básico:" / "Paz, casa y trabajo.", and "PROGRAMA
       ELECTORAL") -> document title and subtitle
     * "ÍNDICE" (a bold paragraph) -> a table (chapter | section | page),
       without the dot leaders; the "•" entries are the sections
     * the 7 chapters ("LAS CASAS SON PARA VIVIR.", orange) -> level 1; the 3
       parts of "VIDA SOLO HAY UNA..." ("VIDAS QUE MEREZCAN LA PENA SER
       VIVIDAS") -> level 2
     * the big title of the page before a chapter ("Las casas son para
       vivir") -> a bold paragraph right after the chapter's heading; the
       back cover -> paragraphs at the end
     * the 443 numbered measures ("1. Bajar los alquileres un 40% por
       ley. ...") -> RST bullets keeping their number; their "•" points ->
       nested bullets and the "⮡" points under those -> bullets nested once
       more (the glyph becomes the list marker)
     * paragraphs, measures and points joined across page breaks; bold and
       italics kept
     * running head ("PROGRAMA ELECTORAL") and page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list glyphs and the index's
   dot leaders are the only text set aside, and a word cut at a page break
   is joined (plain pdftotext leaves its hyphen: it joins only the lines
   inside a block). pandoc must read the same words, without warnings.

Usage:  python3 podemos-pdf2rst.py programa-electoral-paz-casa-y-trabajo.pdf -o podemos.rst
        python3 podemos-pdf2rst.py programa-electoral-paz-casa-y-trabajo.pdf --verify-only podemos.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
BULLET, SUBBULLET = "•", "⮡"
MEASURE = re.compile(r"\d+\.\s")
INDEX_ENTRY = re.compile(r"(.*?)\s*\.{4,}\s*(\d+)")
SHORT_ENTRY = re.compile(r"(.*?\.\.)\s+(\d+)")     # "...VIDAS DIGNAS.. 78": two dots, kept as text
BODY_X = 73.7                       # paragraphs
MEASURE_X = (115, 140)              # "1." .. "443.": the number hangs left of the text
TEXT_X = (143, 150)                 # text of a measure
POINT_X = (158, 166)                # "•" points
POINT_TEXT_X = (169, 175)           # text of a "•" point
SUBPOINT_X = (175.5, 180)           # "⮡" points


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def within(x, rng):
    return rng[0] <= x <= rng[1]


def is_furniture(b):
    """Running head (small, at the top) and page number (at the bottom)."""
    f = b.font
    return (b.y0 < 50 and f.name.startswith("BarlowCondensed") and f.size == 12
            or b.y0 > 780 and f.name.startswith("Barlow") and f.size == 12 and b.text.isdigit())


def strip_glyph(chars):
    """The point's text without its glyph and the spaces after it."""
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


def join(chars, more):
    """Next block of the same paragraph (after a page break): like pdftotext,
    a hyphen ending the line is dropped and the halves joined; otherwise a
    space."""
    if chars and chars[-1][0] == "-" and len(chars) > 1 and chars[-2][0].isalpha():
        chars.pop()
    else:
        chars.append(SPACE)
    chars += more


def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def index_table(bs, report):
    """Index entries ("LAS CASAS SON PARA VIVIR....... 7", "• VIDAS QUE ...
    ....51") -> rows chapter | section | page."""
    rows = []
    for b in sorted(bs, key=lambda b: b.y0):
        pattern = INDEX_ENTRY if INDEX_ENTRY.fullmatch(b.text) else SHORT_ENTRY
        m = pattern.fullmatch(b.text)
        if not m:
            raise C.ConvertError(f"index p{b.page}: entry not understood: {b.text!r}")
        chars = b.chars()
        section = chars[0][0] == BULLET
        if section:
            chars = strip_glyph(chars)
        name = "".join(c for c, _ in chars)
        name = C.esc_start(C.esc_inline(pattern.fullmatch(name).group(1)))
        rows.append(["", name, m.group(2)] if section else [name, "", m.group(2)])
    report.append(f"index: {sum(1 for r in rows if r[0])} chapters, {sum(1 for r in rows if r[1])} sections")
    return C.list_table(rows, widths=[45, 45, 10])


class Writer:
    """Paragraphs, measures and points, joined across page breaks."""

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
                self.out += {
                    "p": lambda: C.wrap(txt, "  "),
                    "point": lambda: C.wrap(txt, "    ", "  * "),
                    "point-p": lambda: C.wrap(txt, "    "),
                    "subpoint": lambda: C.wrap(txt, "      ", "    * "),
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


def flow(w, bs, counts, state):
    first = True
    for b in sorted(bs, key=lambda b: b.y0):
        f, t, x = b.font, b.text, b.x0
        if f.name.startswith("BarlowCondensed") and f.size >= 40:
            # the big title of the page before a chapter, or the back cover
            state["separator"].append(b)
            counts["chapter title pages"] += 1
        elif f.name.startswith("BarlowCondensed") and f.size in (26, 28):
            w.emit(heading(t, "="))
            counts["chapters"] += 1
            for s in state["separator"]:
                w.emit(C.wrap(f"**{C.esc_inline(s.text)}**") + [""])
            state["separator"] = []
        elif f.name.startswith("BarlowCondensed-ExtraBold") and f.size == 16:
            w.emit(heading(t, "-"))
            counts["parts"] += 1
        elif first and w.open_chars() is not None and (ends_open(w.open_chars()) or t[:1].islower()) \
                and not MEASURE.match(t) and t[:1] not in (BULLET, SUBBULLET):
            join(w.open_chars(), b.chars())                          # continued from the previous page
            counts["joined across pages"] += 1
        elif within(x, MEASURE_X) and MEASURE.match(t):
            w.flush()
            w.item = [["measure", b.chars()]]
            counts["measures"] += 1
        elif within(x, POINT_X) and t.startswith(BULLET):
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: • point outside a measure: {t[:50]!r}")
            w.item.append(["point", strip_glyph(b.chars())])
            counts["• points"] += 1
        elif within(x, SUBPOINT_X) and t.startswith(SUBBULLET):
            if w.item is None or w.item[-1][0] not in ("point", "point-p", "subpoint"):
                raise C.ConvertError(f"p{b.page}: ⮡ point outside a • point: {t[:50]!r}")
            w.item.append(["subpoint", strip_glyph(b.chars())])
            counts["⮡ points"] += 1
        elif w.item is not None and within(x, TEXT_X):
            w.item.append(["p", b.chars()])                          # a new paragraph of the measure
            counts["further paragraphs in a measure"] += 1
        elif w.item is not None and within(x, POINT_TEXT_X) and w.item[-1][0] in ("point", "point-p"):
            w.item.append(["point-p", b.chars()])                    # a new paragraph of the point
            counts["further paragraphs in a point"] += 1
        elif abs(x - BODY_X) < 2 and f.name.startswith("Barlow") and f.size == 13:
            w.flush()
            w.para = b.chars()
            counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} x{x:.0f} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        first = False


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    w = Writer()
    first = min(pages)
    cover = sorted((b for b in pages[first] if not is_furniture(b)), key=lambda b: b.y0)
    title = [b for b in cover if b.font.size == 85]
    sub = [b for b in cover if b.font.size == 24]
    if len(title) != 2 or len(sub) != 1 or len(cover) != 3:
        raise C.ConvertError("cover: title (two lines) and subtitle not found")
    w.emit(heading(" ".join(b.text for b in title), "#", over=True) + heading(sub[0].text, "*", over=True))
    w.comment(sorted((b for b in pages[first] if is_furniture(b)), key=lambda b: b.y0))
    ip = [p for p in pages if any(b.text == "ÍNDICE" for b in pages[p])]
    if len(ip) != 1:
        raise C.ConvertError("index not found")
    state = {"separator": []}
    for p in sorted(pages):
        if p == first:
            continue
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.y0)
        body = [b for b in bs if b not in furniture]
        if p == ip[0]:
            title = next(b for b in body if b.text == "ÍNDICE")
            w.emit(C.wrap(f"**{C.esc_inline(title.text)}**") + [""]
                   + index_table([b for b in body if b is not title], report) + [""])
        else:
            flow(w, body, counts, state)
        w.comment(furniture)
    for s in state["separator"]:                 # the back cover
        w.emit(C.wrap(C.esc_start(C.esc_inline(s.text))) + [""])
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    # plain pdftotext glues a page number to a last line cut by a hyphen
    # ("dificul-" + "10" -> "dificul10"): split again (C.split_glued_page_numbers)
    raw, n = C.split_glued_page_numbers(raw)
    report.append(f"  page numbers glued by pdftotext to a hyphenated last line, split again: {n}")
    return C.verify_blocks(rst, raw, report, strip_start=(BULLET + " ", SUBBULLET + " "),
                           leaders=True, block_end_hyphens=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Podemos programme", default_output="podemos.rst"))
