#!/usr/bin/env python3
"""
teruel_existe-pdf2rst.py - Convert the Teruel Existe programme "Faltaría
más. - Programa electoral completo 29N" (PDF, A4, two columns) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

0. Pages 2-15 carry a copy of an older page drawn through a small window
   (only its logo shows), but pdftotext prints all of that hidden page's
   text, mixed into the real lines. ``visible_pdf`` (pdf2rst_common.py) sets
   it aside: a temporary copy of the PDF with the window emptied, checked to
   show the same text inside the window; conversion and verification use
   that copy, and the report counts what was set aside.
1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph or a bullet point.
2. Write RST:
     * the cover's "PROGRAMA ELECTORAL" and slogan ("Vamos a seguir
       defendiendo nuestra tierra.") -> document title and subtitle (the big
       "FALTARÍA MÁS." is an image)
     * "Un programa para transformar Teruel" and "Propuestas" -> level 1
       (the repeated "Propuestas" of the next pages -> RST comments); the
       kickers above headings ("TERUEL EXISTE. DESDE TERUEL...", "PRIORIDAD
       1") -> a paragraph right after their heading; the tagline "Ser pocos
       no resta derechos" -> a paragraph
     * "Nuestras cinco prioridades" -> level 2, its five numbered boxes
       ("01" + title + text) -> bullet items, in number order
     * the 12 chapters ("1. Un cambio de rumbo...") -> level 2; their
       sections ("Poner orden en el descontrol", "4.1. ...") -> level 3
     * "•" points -> RST bullets (glyph dropped); a point continued at the
       top of the next column or page stays one item; bold kept
     * running head ("P R O G R A M A E L E C T O R A L") and page numbers
       ("02 / 15") -> RST comments
3. Verify against plain pdftotext (of the visible copy): the RST text is
   exactly the PDF's lines, each used once and whole, in any order; the
   bullet glyphs are the only text set aside. pandoc must read the same
   words, without warnings.

Usage:  python3 teruel_existe-pdf2rst.py Programa_Teruel_Existe_Completo_29N.pdf -o teruel_existe.rst
        python3 teruel_existe-pdf2rst.py Programa_Teruel_Existe_Completo_29N.pdf --verify-only teruel_existe.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
GREEN = "#24644a"
BULLET = "•"
COLUMN_X = 300                # the right column starts at x 310
TOP_Y = 190                   # text under the page title starts at y 185


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def is_furniture(b):
    """Running head (letter-spaced) and page number ("02 / 15")."""
    return b.y0 < 60 and b.text.replace(" ", "") == "PROGRAMAELECTORAL" and b.page > 1 \
        or b.y0 > 780 and re.fullmatch(r"\d\d / \d\d", b.text)


def kind(b):
    f = b.font
    if f.color == GREEN:
        return {8: "kicker", 11: "box title", 12: "section", 16: "chapter", 25: "subtitle",
                32: "page title", 33: "page title"}.get(f.size, "?")
    return "body"


class Writer:
    """Paragraphs and bullet items, joined across columns and pages."""

    def __init__(self):
        self.out, self.para, self.pending = [], None, []      # para: [chars, kind]

    def flush(self):
        if self.para:
            chars, k = self.para
            txt = C.esc_start(style(chars))
            self.out += (C.wrap(txt, "  ", "* ") if k == "item" else C.wrap(txt)) + [""]
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
            self.pending.append(c)       # after the point that runs across the page
        else:
            self.out += c

    def start(self, chars, k):
        self.flush()
        self.para = [list(chars), k]


def priorities(w, bs, counts):
    """The five boxes of "Nuestras cinco prioridades": number, title, text."""
    nums = sorted((b for b in bs if kind(b) == "kicker" and re.fullmatch(r"\d\d", b.text)), key=lambda b: b.text)
    used = set(nums)
    for n in nums:
        title = next((o for o in bs if kind(o) == "box title" and abs(o.y0 - n.y0) < 4 and 0 < o.x0 - n.x1 < 20), None)
        text = title and next((o for o in bs if kind(o) == "body" and abs(o.x0 - title.x0) < 2
                               and 0 <= o.y0 - title.y1 < 15), None)
        if title is None or text is None:
            raise C.ConvertError(f"p{n.page}: priority box {n.text} without its title or text")
        used.update((title, text))
        w.emit(C.wrap(C.esc_start(C.esc_inline(f"{n.text} {title.text}")), "  ", "* ") + [""]
               + C.wrap(C.esc_start(style(text.chars())), "  ") + [""])
        counts["priority boxes"] += 1
    return used


def flow(w, bs, counts, state):
    seen = set()                                          # columns with text already
    skip = set()
    for b in bs:
        if b in skip:
            continue
        k, t = kind(b), b.text
        col = b.x0 > COLUMN_X
        top = col not in seen and b.y0 < TOP_Y            # first block at the top of a column
        if k != "page title":
            seen.add(col)
        if k == "page title":
            if t == "Propuestas" and state["propuestas"]:
                w.comment([b])                            # the same title on every page
                counts["repeated page titles"] += 1
                continue
            state["propuestas"] = state["propuestas"] or t == "Propuestas"
            w.emit(heading(t, "="))
            state["level 1"] += 1
            if state["kicker"]:
                w.emit(C.wrap(C.esc_start(style(state["kicker"]))) + [""])
                state["kicker"] = None
        elif k == "kicker" and not re.fullmatch(r"\d\d", t):          # "PRIORIDAD 1": after the heading
            w.flush()
            state["kicker"] = b.chars()
        elif k == "kicker" or k == "box title" and any(
                kind(o) == "kicker" and abs(o.y0 - b.y0) < 4 and o.x1 < b.x0 for o in bs):
            skip = priorities(w, bs, counts)                           # "01" + title: the boxes
        elif k in ("chapter", "section", "subtitle"):
            w.emit(heading(t, {"chapter": "-", "subtitle": "-", "section": "~"}[k]))
            counts[k + "s"] += 1
            if state["kicker"]:
                w.emit(C.wrap(C.esc_start(style(state["kicker"]))) + [""])
                state["kicker"] = None
        elif k == "box title":                                         # "Ser pocos no resta derechos"
            w.start(b.chars(), "para")
            counts["taglines"] += 1
        elif t.startswith(BULLET):
            chars = b.chars()
            i = 1
            while i < len(chars) and chars[i][0].isspace():
                i += 1
            w.start(chars[i:], "item")
            counts["points"] += 1
        elif top and w.para and w.para[1] == "item" and b.font.size == 10:
            w.para[0] += [SPACE] + b.chars()                          # the point goes on here
            counts["points continued"] += 1
        elif k == "?":
            raise C.ConvertError(f"p{b.page}: block of unknown kind {b.font}: {t[:50]!r}")
        else:
            w.start(b.chars(), "para")
            counts["paragraphs"] += 1


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    state = {"propuestas": False, "kicker": None, "level 1": 0}
    # cover: "PROGRAMA ELECTORAL" and the slogan -> document title and subtitle
    cover = sorted((b for b in pages[1] if not is_furniture(b) and b.font.color == GREEN), key=lambda b: b.y0)
    if len(cover) != 2:
        raise C.ConvertError(f"cover: expected title and slogan, got {[b.text for b in cover]}")
    w = Writer()
    w.emit(heading(cover[0].text, "#", over=True) + heading(cover[1].text, "*", over=True))
    for p in pages:
        bs = [b for b in pages[p] if b not in cover]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.y0)
        body = [b for b in bs if b not in furniture]
        # reading order: pdftotext mixes the columns; read the left column,
        # then the right one (page 2, a single column over a grid of boxes,
        # from top to bottom)
        if any(kind(b) == "subtitle" for b in body):
            body.sort(key=lambda b: (b.y0, b.x0))
        else:
            body.sort(key=lambda b: (b.x0 > COLUMN_X, b.y0))
        flow(w, body, counts, state)
        w.comment(furniture)
    if state["kicker"]:
        raise C.ConvertError("kicker without its heading")
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    counts["level 1"] = state["level 1"]
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=BULLET)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Teruel Existe programme", default_output="teruel_existe.rst"))
