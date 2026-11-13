#!/usr/bin/env python3
"""
soria_ya-pdf2rst.py - Convert the Soria ¡Ya! programme for the general
election of 29 November 2026 (PDF, Illustrator, one page, three columns) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph; a hyphen at a line end inside a block is joined like
   plain pdftotext joins it.
2. Write RST:
     * "Soria ¡SÍ!", the logos and "Tu voto útil para Soria" are images;
       the slogan at the foot ("Del Olvido al Futuro") is the document title
       and the red call "SI EL 29 N, SORIA ¡YA! CONSIGUE REPRESENTACIÓN, ESTO
       ES LO QUE EXIGIREMOS" the first paragraph
     * the 12 red headings -> level 1, read column by column (left, middle,
       right), each from top to bottom
     * paragraphs as printed; Gotham-Black -> bold (Gotham-Medium is the
       regular text); a lone "." printed at the end of the line "Osma-Soria-
       Zaragoza." stays there
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order. pandoc must read the same words,
   without warnings.

Usage:  python3 soria_ya-pdf2rst.py ProgramaGenerales_26.pdf -o soria_ya.rst
        python3 soria_ya-pdf2rst.py ProgramaGenerales_26.pdf --verify-only soria_ya.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
RED = "#ea0029"
COLUMNS = (200, 400)          # columns start at x 25, 218 and 410


def style(chars):
    """Gotham-Black is the bold; Gotham-Medium the regular text."""
    return C.inline(chars, strong=lambda f: "Black" in f.family)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def column(b):
    return sum(b.x0 > x for x in COLUMNS)


def convert(pdf, report):
    blocks = C.extract(pdf)
    if {b.page for b in blocks} != {1}:
        raise C.ConvertError("expected a single page")
    counts = collections.Counter()
    title = [b for b in blocks if b.font.size >= 30]
    call = [b for b in blocks if b.font.color == RED and b.font.size == 12]
    if len(title) != 1 or len(call) != 1:
        raise C.ConvertError(f"slogan or call not found: {[b.text for b in title + call]}")
    out = heading(title[0].text, "#", over=True) + C.wrap(C.esc_start(style(call[0].chars()))) + [""]
    body = sorted((b for b in blocks if b not in title + call), key=lambda b: (column(b), b.y0))
    para = None

    def flush():
        nonlocal para
        if para:
            out.extend(C.wrap(C.esc_start(style(para))) + [""])
            para = None

    prev = None
    for b in body:
        if b.font.color == RED and b.font.size == 9:
            flush()
            out.extend(heading(b.text, "="))
            counts["headings"] += 1
        elif re.fullmatch(r"\W", b.text) and prev is not None and abs(b.y1 - prev.lines[-1].y1) < 2 \
                and b.x0 > prev.x1:
            para += [SPACE] + b.chars()                   # a mark printed at the end of the last line
            counts["marks at a line end"] += 1
        else:
            flush()
            para = b.chars()
            counts["paragraphs"] += 1
        prev = b
    flush()
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Soria ¡Ya! programme", default_output="soria_ya.rst"))
