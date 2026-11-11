#!/usr/bin/env python3
"""
iustitia_europa-pdf2rst.py - Convert the Iustitia Europa (IE) "Contrato
Electoral" (PDF, Word, A5) to reStructuredText, repeatably, and verify that
no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for words and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). The text
   is justified with wide gaps, so pdftotext's blocks cut lines into pieces:
   the lines are rebuilt from the words' positions (one column per page).
2. Write RST:
     * "Contrato Electoral de Iustitia Europa" (its index page) is the
       document title
     * the 15 points ("1. POR UN REINICIO MORAL EN ESPAÑA.", upper case,
       Arial) -> level 1; their numbered upper-case subheadings (body font)
       -> level 2
     * "•" bullets -> RST bullets (glyph dropped); numbered lines -> bullet
       items keeping their number; "- ..." measures stay paragraphs
     * paragraphs rebuilt from the line spacing and joined across pages;
       bold and italics kept
     * page numbers -> RST comments
     * the cover and back-page slogans use a font without a Unicode mapping:
       pdftotext gives only control characters for them, so they cannot be
       converted; they are set aside, counted and reported
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines
   (as pdftotext cuts them), each used once and whole, in any order; the
   bullet glyphs and the unreadable glyphs are the only text set aside.
   pandoc must read the same words, without warnings.

Usage:  python3 iustitia_europa-pdf2rst.py descarga-aqui-tu-contrato-00030.pdf -o iustitia_europa.rst
        python3 iustitia_europa-pdf2rst.py descarga-aqui-tu-contrato-00030.pdf --verify-only iustitia_europa.rst
"""
import collections
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
PARA_GAP = 18.0          # line pitch 13.9-16.8 inside a paragraph, 20+ between paragraphs
BULLET = "•"
TITLE = "Contrato Electoral de Iustitia Europa"


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def unreadable(text):
    return all(unicodedata.category(c) == "Cc" for c in text)


def page_lines(blocks, report):
    """Lines rebuilt from the words of each page: same baseline, left to right."""
    words, dropped = collections.defaultdict(list), collections.Counter()
    for b in blocks:
        for ln in b.lines:
            for w in ln.words:
                if unreadable(w.text):
                    dropped[b.page] += 1
                else:
                    words[b.page].append(w)
    pages = {}
    for p, ws in words.items():
        lines = []
        for w in sorted(ws, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
            mid = (w.y0 + w.y1) / 2
            if lines and abs(mid - lines[-1][0]) < 3:
                lines[-1][1].append(w)
            else:
                lines.append([mid, [w]])
        pages[p] = [C.Block(p, [C.Line(sorted(lw, key=lambda w: w.x0))]) for _, lw in lines]
    if dropped:
        report.append("unreadable glyphs (font without Unicode mapping) set aside: "
                      + ", ".join(f"p{p}: {n} words" for p, n in sorted(dropped.items())))
    return pages


def upper(text):
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.9 * len(letters)


def is_body_font(ln):
    return ln.font.name.startswith("___WRD_EMBED")


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = page_lines(blocks, report)
    out, para, pending = [], None, []
    counts = collections.Counter()

    def flush():
        nonlocal para
        if para:
            chars, kind = para
            txt = C.esc_start(style(chars))
            out.extend((C.wrap(txt, "  ", "* ") if kind == "item" else C.wrap(txt)) + [""])
            para = None
        out.extend(x for c in pending for x in c)
        pending.clear()

    def emit(lines):
        flush()
        out.extend(lines)

    title = next((ln for p in sorted(pages) for ln in pages[p] if ln.text == TITLE), None)
    if title is None:
        raise C.ConvertError(f"title {TITLE!r} not found")
    emit(heading(title.text, "#", over=True))
    for p in sorted(pages):
        lines = [ln for ln in pages[p] if ln is not title]
        numbers = [ln for ln in lines if ln.y0 > 520 and re.fullmatch(r"\d{1,2}", ln.text)]
        lines = [ln for ln in lines if ln not in numbers]
        prev = None
        i = 0
        while i < len(lines):
            ln = lines[i]
            t = ln.text
            numbered_upper = re.match(r"\d{1,2}\. ", t) and upper(t)
            if numbered_upper:                                    # section / subsection heading
                level = "-" if is_body_font(ln) else "="
                parts = [ln]
                while i + 1 < len(lines) and upper(lines[i + 1].text) \
                        and lines[i + 1].y0 - parts[-1].y0 <= 16 \
                        and is_body_font(lines[i + 1]) == is_body_font(ln):
                    i += 1
                    parts.append(lines[i])
                emit(heading(" ".join(x.text for x in parts), level))
                counts["sections" if level == "=" else "subsections"] += 1
                prev = parts[-1]
            elif t.startswith(BULLET):
                chars = ln.chars()
                k = 1
                while k < len(chars) and chars[k][0].isspace():
                    k += 1
                flush()
                para = [chars[k:], "item"]
                counts["bullets"] += 1
                prev = ln
            elif re.match(r"\d{1,2}\. \S", t):                    # numbered item (index, questions)
                flush()
                para = [ln.chars(), "item"]
                counts["numbered items"] += 1
                prev = ln
            elif para and prev is not None and ln.y0 - prev.y0 <= PARA_GAP:
                para[0] += [SPACE] + ln.chars()
                prev = ln
            elif para and prev is None and para[0][-1][1].size <= 11 and ln.font.size <= 11 and (
                    not "".join(c for c, _ in para[0]).rstrip().endswith(C.TERMINAL) or t[:1].islower()):
                para[0] += [SPACE] + ln.chars()                   # continued from the previous page
                prev = ln
            else:
                flush()
                para = [ln.chars(), "para"]
                prev = ln
            i += 1
        c = C.comment_blocks(numbers)
        if para:
            pending.append(c)
        else:
            out.extend(c)
    flush()
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, strip_start=BULLET, skip_unreadable=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Iustitia Europa electoral contract",
                    default_output="iustitia_europa.rst"))
