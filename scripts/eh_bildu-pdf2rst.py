#!/usr/bin/env python3
"""
eh_bildu-pdf2rst.py - Convert the EH Bildu programme "Posible egin. Garantía y
derechos" (PDF, InDesign, A4) to reStructuredText, repeatably, and verify that
no text was altered, omitted or added.

One converter for every language edition of the programme (castellano,
euskera): it reads only fonts and positions, never the words, so the same
layout in another language converts the same way. build.py runs it for
"eh_bildu.rst" while there is one edition, and for "eh_bildu-es.rst",
"eh_bildu-eu.rst"... when latest.json lists several (a converter named
"eh_bildu-<language>-pdf2rst.py" would take precedence; there is none).

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph or a point; a hyphen at a line end inside a block is
   joined like plain pdftotext joins it (also across a page break).
2. Write RST:
     * the cover (the small title "Garantía y derechos" and the big vertical
       "programa"; "POSIBLE EGIN." and the logos are images) -> document
       title and subtitle
     * the index ("Índice", big) -> a bold paragraph and a table (entry |
       page), entries joined line by line, without the dot leaders (some
       printed with a leader glyph that has no Unicode mapping: pdftotext
       prints U+FFFD)
     * the big titles ("Nuestro compromiso", "1. Derechos y libertades")
       -> level 1; "1.1. Vivienda" -> level 2; "1.5.1 SALUD, DERECHOS..."
       -> level 3 (the number and the title may be two blocks on one line)
     * "•" points -> RST bullets, their "»" points -> nested bullets (glyphs
       dropped: the text is a block of its own beside the glyph); text at a
       point's indentation without a glyph -> a further paragraph of it
     * paragraphs and points joined across page breaks; bold ("Ciudadanía")
       and italics kept
     * running head ("PRO GRAMA") and page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the list glyphs and the index's
   dot leaders are the only text set aside, and a word cut at a page break
   is joined (plain pdftotext leaves its hyphen: it joins only the lines
   inside a block). pandoc must read the same words, without warnings.

Usage:  python3 eh_bildu-pdf2rst.py PROGRAMA-CAS.pdf -o eh_bildu.rst
        python3 eh_bildu-pdf2rst.py PROGRAMA-CAS.pdf --verify-only eh_bildu.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
BULLET, SUBBULLET = "•", "»"
BODY_X, POINT_X, SUBPOINT_X = 56.7, 74.7, 92.7        # text of paragraphs, "•" points, "»" points
INDEX_ENTRY = re.compile(r"(.*?)\s*(?:\.{3,}|�{4,})\s*(\d+)")


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def near(x, y, tol=3):
    return abs(x - y) < tol


def is_furniture(b):
    """Running head (small, at the top) and page number (small, at the bottom)."""
    return b.font.size == 7 and (b.y0 < 80 or b.y0 > 760)


def is_body(b):
    return b.font.name.startswith("TiemposText") and b.font.size == 10


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
    """Index lines ("1.1. Vivienda.........5"); an entry may wrap."""
    lines = sorted((C.Block(b.page, [ln]) for b in bs for ln in b.lines), key=lambda l: (l.page, l.y0))
    rows, entry = [], []
    for ln in lines:
        entry += ([SPACE] if entry else []) + ln.chars()
        m = INDEX_ENTRY.fullmatch("".join(c for c, _ in entry))
        if not m:
            continue
        rows.append([C.esc_start(style(entry[:len(m.group(1))])), C.esc_inline(m.group(2))])
        entry = []
    if entry:
        raise C.ConvertError(f"index entry without page: {''.join(c for c, _ in entry)!r}")
    report.append(f"index: {len(rows)} entries")
    return C.list_table(rows, widths=[92, 8])


class Writer:
    """Paragraphs and points (with their own points), joined across pages."""

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
                    "sub": lambda: C.wrap(txt, "    ", "  * "),
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


def heading_lines(bs, used):
    """Headings: a number and a title may be two blocks on one line."""
    out = []
    for b in sorted(bs, key=lambda b: (b.y0, b.x0)):
        if b in used:
            continue
        if b.font.name.startswith("BilduSans") and b.font.size in (10, 16):
            same = sorted((o for o in bs if o not in used and o.font == b.font and abs(o.y0 - b.y0) < 2),
                          key=lambda o: o.x0)
            used.update(same)
            out.append((b.y0, b.font.size, " ".join(o.text for o in same), same))
    return out


def flow(w, bs, counts):
    glyphs = [b for b in bs if b.text in (BULLET, SUBBULLET)]
    mates = {}
    for g in glyphs:                      # the point's text: the block beside its glyph
        m = min((b for b in bs if b not in glyphs and b.x0 > g.x1 and abs(b.y0 - g.y0) < 3),
                key=lambda b: (abs(b.y0 - g.y0), b.x0), default=None)
        if m is None or m in mates:
            raise C.ConvertError(f"p{g.page} y{g.y0:.0f}: {g.text} without its text")
        mates[m] = g.text
    used = set()
    heads = {id(parts[0]): (size, text) for _, size, text, parts in heading_lines(bs, used)}
    first = True
    for b in sorted((b for b in bs if b not in glyphs), key=lambda b: (b.y0, b.x0)):
        f, t = b.font, b.text
        if b in used:
            if id(b) in heads:
                size, text = heads[id(b)]
                w.emit(heading(text, "-" if size == 16 else "~"))
                counts["sections" if size == 16 else "subsections"] += 1
            continue
        if f.name.startswith("BilduCircular") and f.size == 28:
            w.emit(heading(t, "="))
            counts["chapters"] += 1
        elif mates.get(b) == BULLET:
            w.flush()
            w.item = [["point", b.chars()]]
            counts["• points"] += 1
        elif mates.get(b) == SUBBULLET:
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: » point outside a • point: {t[:50]!r}")
            w.item.append(["sub", b.chars()])
            counts["» points"] += 1
        elif not is_body(b):
            raise C.ConvertError(f"p{b.page} x{b.x0:.0f} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        elif first and w.open_chars() is not None and (ends_open(w.open_chars()) or t[:1].islower()):
            join(w.open_chars(), b.chars())                          # continued from the previous page
            counts["joined across pages"] += 1
        elif w.item is not None and near(b.x0, POINT_X):
            w.item.append(["p", b.chars()])                          # a new paragraph of the point
            counts["further paragraphs in a point"] += 1
        elif w.item is not None and near(b.x0, SUBPOINT_X) and w.item[-1][0] in ("sub", "subp"):
            w.item.append(["subp", b.chars()])
            counts["further paragraphs in a » point"] += 1
        elif near(b.x0, BODY_X):
            w.flush()
            w.para = b.chars()
            counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} x{b.x0:.0f} y{b.y0:.0f}: block not understood ({f}): {t[:60]!r}")
        first = False


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    w = Writer()
    cover = min(pages)
    big = [b for b in pages[cover] if b.font.size > 100]
    small = [b for b in pages[cover] if b not in big]
    if len(big) != 1 or len(small) != 1:
        raise C.ConvertError("cover: title and the big 'programa' not found")
    w.emit(heading(small[0].text, "#", over=True) + heading(big[0].text, "*", over=True))
    ip = [p for p in pages if p != cover and pages[p]
          and all(is_furniture(b) or b.font.size == 28 or all(INDEX_ENTRY.fullmatch(C.Block(p, [ln]).text)
                                                               for ln in b.lines) for b in pages[p])]
    if not ip or ip != list(range(ip[0], ip[-1] + 1)) or ip[0] != cover + 1:
        raise C.ConvertError(f"index pages not found or not consecutive: {ip}")
    title = [b for p in ip for b in pages[p] if b.font.size == 28]
    if len(title) != 1:
        raise C.ConvertError("index title not found")
    entries = [b for p in ip for b in pages[p] if b not in title and not is_furniture(b)]
    w.emit(C.wrap(f"**{C.esc_inline(title[0].text)}**") + [""] + index_table(entries, report) + [""])
    w.comment(sorted((b for p in ip for b in pages[p] if is_furniture(b)), key=lambda b: (b.page, b.y0)))
    for p in sorted(pages):
        if p == cover or p in ip:
            continue
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.y0)
        flow(w, [b for b in bs if b not in furniture], counts)
        w.comment(furniture)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=(BULLET, SUBBULLET), leaders=True,
                           block_end_hyphens=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the EH Bildu programme", default_output="eh_bildu.rst"))
