#!/usr/bin/env python3
"""
feministas_al_congreso-pdf2rst.py - Convert the Feministas al Congreso
leaflet "Nuestras Propuestas - Juntas hacemos historia" (PDF, Illustrator,
two pages) to reStructuredText, repeatably, and verify that no text was
altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block is a paragraph or a proposal; a hyphen at a line end inside a block
   is joined like plain pdftotext joins it (the text has none).
2. Write RST:
     * "Nuestras Propuestas" and "JUNTAS HACEMOS HISTORIA" (page 2's heading
       and its box) -> document title and subtitle; the party's name is a
       logo (an image)
     * the cover's line "El único partido de izquierdas y feminista de
       verdad." and the candidate's name ("ZURIÑE OJEDA", bold) -> paragraphs
     * the 14 numbered proposals (the number apart, in colour) -> bullet items
       keeping their number; bold kept
     * "Consulta nuestro ideario" and the web address -> paragraphs
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order. pandoc must read the same words,
   without warnings.

Usage:  python3 feministas_al_congreso-pdf2rst.py Programa_1IMP.pdf -o feministas_al_congreso.rst
        python3 feministas_al_congreso-pdf2rst.py Programa_1IMP.pdf --verify-only feministas_al_congreso.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def paragraph(chars):
    return C.wrap(C.esc_start(style(chars))) + [""]


def convert(pdf, report):
    blocks = C.extract(pdf)
    title = [b for b in blocks if b.font.size == 24]
    sub = [b for b in blocks if b.font.size == 16 and b.font.color == "#ffffff"]
    if len(title) != 1 or len(sub) != 1:
        raise C.ConvertError("title or subtitle not found")
    out = heading(title[0].text, "#", over=True) + heading(sub[0].text, "*", over=True)
    nums = [b for b in blocks if re.fullmatch(r"\d{1,2}", b.text) and b.font.size == 20]
    used = set(title + sub + nums)
    items = []
    for n in nums:
        text = min((b for b in blocks if b not in used and b.page == n.page and b.font.size == 9
                    and b.x0 > n.x1), key=lambda b: abs(b.y0 - n.y0), default=None)
        if text is None or abs(text.y0 - n.y0) > 8:
            raise C.ConvertError(f"p{n.page}: proposal {n.text} without its text")
        used.add(text)
        items.append((int(n.text), n, text))
    numbers = sorted(k for k, _, _ in items)
    if numbers != list(range(1, len(numbers) + 1)):
        raise C.ConvertError(f"proposals not numbered 1..n: {numbers}")
    rest = sorted((b for b in blocks if b not in used), key=lambda b: (b.page, b.y0))
    before = [b for b in rest if b.page == 1]               # cover: the line and the candidate
    after = [b for b in rest if b.page != 1]                # the box under the proposals
    for b in before:
        out += paragraph(b.chars())
    for _, n, text in sorted(items, key=lambda t: t[0]):
        out += C.wrap(C.esc_start(style(n.chars() + [SPACE] + text.chars())), "  ", "* ") + [""]
    for b in after:
        out += paragraph(b.chars())
    while out and out[-1] == "":
        out.pop()
    report.append(f"structure: proposals {len(items)}, paragraphs {len(before) + len(after)}")
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Feministas al Congreso leaflet",
                    default_output="feministas_al_congreso.rst"))
