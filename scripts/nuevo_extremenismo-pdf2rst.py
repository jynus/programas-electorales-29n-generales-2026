#!/usr/bin/env python3
"""
nuevo_extremenismo-pdf2rst.py - Convert the Nuevo Extremeñismo programme "150
medidas para que se escuche a Extremadura en Madrid" (PDF printed from a web
page by Chromium, A4, 15 pages) to reStructuredText, repeatably, and verify
that no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts and bold; see pdf2rst_common.py).
   All the body fonts are called "DMSans9pt" whatever their weight: the bold
   comes from pdftohtml's <b>, character by character. The text is not
   hyphenated.
2. Write RST:
     * the cover ("150" / "medidas para que se escuche a Extremadura en
       Madrid", and "La única candidatura 100% extremeña" above) -> document
       title and subtitle
     * "Lo que llevaremos a Madrid" -> level 1; its list of the 11 chapters
       ("I. Vivienda. Una casa para vivir, no para invertir.") -> bullets
     * the chapters ("I. Vivienda", big) -> level 1, and their tag line
       ("Una casa para vivir, no para invertir.") -> a paragraph; the
       chapter's title repeated at the top of its second page -> an RST
       comment
     * the blocks in capitals ("EMPLEO Y SALARIOS", "CÓMO SE PAGA"...) ->
       level 2; the other small headings ("Cómo se paga", "Cuánto cuesta",
       "Dignos, con tus derechos garantizados.") -> level 3
     * the figures ("+45 %" and "subida del alquiler en cinco años en la
       provincia de Cáceres" under it) -> bullets, the figure in bold
     * the 150 numbered measures (the number apart, in colour, or in the same
       block from 100 on) -> bullets keeping their number; bold kept
     * the steps ("1 Te apuntas en tu oficina de empleo.") and the kinds of
       work ("Cuidados y acompañamiento") -> bullets; a line that a step
       wraps onto ("proyectos.") is joined to it
     * paragraphs and source notes -> paragraphs
     * the footer ("@nuevoext", page number, "www.nuevoext.es") -> RST
       comments; the back cover -> paragraphs at the end
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; pandoc must read the same words,
   without warnings.

Usage:  python3 nuevo_extremenismo-pdf2rst.py Programa-Electoral-Generales-29N.pdf -o nuevo_extremenismo.rst
        python3 nuevo_extremenismo-pdf2rst.py Programa-Electoral-Generales-29N.pdf --verify-only nuevo_extremenismo.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
GREEN, GREY, DARK = "#00bfa8", "#4a5653", "#0a3c36"
MEASURE_NUM = re.compile(r"\d{1,3}\.")
MEASURE = re.compile(r"\d{1,3}\. ")
CHAPTER_ITEM = re.compile(r"[IVX]+\. ")


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def paragraph(chars):
    return C.wrap(C.esc_start(style(chars))) + [""]


def bullet(chars):
    return C.wrap(C.esc_start(style(chars)), "  ", "* ") + [""]


def is_furniture(b):
    """The footer: "@nuevoext", the page number, "www.nuevoext.es"."""
    return b.y0 > 790 and b.font.name.startswith("Poppins")


def is_figure(b):
    """A big figure ("+45 %"): Poppins-ExtraBold, below the chapter title."""
    return b.font.name.startswith("Poppins-ExtraBold") and b.font.size in (22, 25) and b.y0 > 140


def join_wrapped(bs, counts):
    """A step that wraps ("2 Ayuntamientos y entidades proponen" / "proyectos."):
    pdftotext gives the second line as a block of its own, indented under the
    first. Join it to the block right above it in the same column."""
    out = list(bs)
    for b in sorted(bs, key=lambda b: b.y0):
        if not (b.text[:1].islower() and b.font.size == 10):
            continue
        above = [o for o in out if o is not b and o.font.size == 10 and o.x0 <= b.x0 <= o.x1 and -6 < b.y0 - o.y1 < 4]   # the big step number makes the boxes overlap
        if len(above) != 1:
            continue
        a = above[0]
        out[out.index(a)] = C.Block(a.page, a.lines + b.lines, a.index, a.role, a.data)
        out.remove(b)
        counts["wrapped lines joined"] += 1
    return out


def page_flow(p, bs, counts, state):
    out = []
    used = set()
    nums = [b for b in bs if MEASURE_NUM.fullmatch(b.text) and b.x0 < 60]
    for n in nums:                                   # a measure's number and its text beside it
        t = min((b for b in bs if b not in nums and b.x0 > n.x1 and abs(b.y0 - n.y0) < 3),
                key=lambda b: b.x0, default=None)
        if t is None:
            raise C.ConvertError(f"p{p}: measure {n.text} without its text")
        used.update((n, t))
        state["measures"].append((n.y0, int(n.text[:-1]), n.chars() + [SPACE] + t.chars()))
    figures = [b for b in bs if is_figure(b)]
    captions = {}
    for f in figures:                                # a figure and its caption under it
        c = min((b for b in bs if b not in figures and abs(b.x0 - f.x0) < 2 and 0 < b.y0 - f.y0 < 40),
                key=lambda b: b.y0, default=None)
        if c is None:
            raise C.ConvertError(f"p{p}: figure {f.text!r} without its caption")
        captions[f] = c
        used.update((f, c))
    rows = sorted([(n_y, "measure", k, chars) for n_y, k, chars in state.pop("measures")]
                  + [(f.y0, "figure", f.x0, f.chars() + [SPACE] + captions[f].chars()) for f in figures]
                  + [(b.y0, "block", b.x0, b) for b in bs if b not in used],
                  key=lambda r: (r[0], r[2]))
    state["measures"] = []
    for y, kind, key, b in rows:
        if kind == "measure":
            state["numbers"].append(key)
            out += bullet(b)
            counts["measures"] += 1
            continue
        if kind == "figure":
            out += bullet(b)
            counts["figures"] += 1
            continue
        f, t = b.font, b.text
        if f.name.startswith("Poppins-ExtraBold") and f.size == 25 and y < 100:
            if t == state["chapter"]:
                out += C.comment_blocks([b])         # the chapter's title again, on its second page
                counts["chapter titles repeated"] += 1
            else:
                state["chapter"] = t
                out += heading(t, "=")
                counts["chapters"] += 1
        elif f.name.startswith("Poppins-ExtraBold") and f.size == 33:
            out += heading(t, "=")
        elif f.name.startswith("Poppins") and f.size == 13:
            out += paragraph(b.chars())              # the chapter's tag line
        elif f.name.startswith("Poppins") and f.size == 11:
            letters = [c for c in t if c.isalpha()]
            out += heading(t, "-" if all(c.isupper() for c in letters) else "~")
            counts["headings"] += 1
        elif p == 2 and CHAPTER_ITEM.match(t):
            out += bullet(b.chars())                 # the list of chapters
            counts["chapters listed"] += 1
        elif MEASURE.match(t) and b.x0 < 60:
            state["numbers"].append(int(t.split(".")[0]))
            out += bullet(b.chars())                 # from 100 on: number and text in one block
            counts["measures"] += 1
        elif f.name.startswith("DMSans") and f.size == 10 and f.color in (DARK, GREEN):
            out += bullet(b.chars())                 # steps and kinds of work
            counts["steps and kinds of work"] += 1
        elif f.name.startswith("DMSans"):
            out += paragraph(b.chars())
            counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{p} x{b.x0:.0f} y{y:.0f}: block not understood ({f}): {t[:60]!r}")
    return out


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = collections.OrderedDict()
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    counts = collections.Counter()
    first, last = min(pages), max(pages)
    cover = sorted(pages[first], key=lambda b: b.y0)
    num = [b for b in cover if b.text == "150"]
    title = [b for b in cover if b.font.size == 25]
    sub = [b for b in cover if b.font.size == 54]
    if len(cover) != 3 or not (num and title and sub):
        raise C.ConvertError("cover: title and subtitle not found")
    out = heading(f"{num[0].text} {title[0].text}", "#", over=True) + heading(sub[0].text, "*", over=True)
    state = {"chapter": None, "measures": [], "numbers": []}
    for p in sorted(pages):
        if p in (first, last):
            continue
        bs = pages[p]
        furniture = sorted((b for b in bs if is_furniture(b)), key=lambda b: b.x0)
        out += page_flow(p, join_wrapped([b for b in bs if not is_furniture(b)], counts), counts, state)
        out += C.comment_blocks(furniture)
    for b in sorted(pages[last], key=lambda b: (b.y0, b.x0)):     # the back cover
        out += paragraph(b.chars())
    if state["numbers"] != list(range(1, len(state["numbers"]) + 1)):
        raise C.ConvertError(f"measures not numbered 1..n: {state['numbers']}")
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Nuevo Extremeñismo programme",
                    default_output="nuevo_extremenismo.rst"))
