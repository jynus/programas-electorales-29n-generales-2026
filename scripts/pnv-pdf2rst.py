#!/usr/bin/env python3
"""
pnv-pdf2rst.py - Convert the EAJ-PNV programme "Programa Elecciones
Generales 2026 - Defendemos Euskadi" (PDF) to reStructuredText, repeatably,
and verify that no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py).
2. Write RST:
     * cover -> title (Spanish) and subtitle (Basque)
     * ÍNDICE and "Cinco compromisos" -> tables (entries paired with their
       page numbers, which plain pdftotext prints as a separate column)
     * chapter openers (01 COMPROMISO 1 ...) and page titles -> level 1;
       "1.1 ..." sections -> level 2; green proposal groups -> level 3
     * numbered proposals ("01 ...") -> bullet lists, the number in bold
     * lead and body paragraphs re-flowed and joined across page breaks;
       bold and italics kept
     * running header and footer (with the page number) -> RST comments
       (kept, not rendered), so nothing of the PDF is dropped
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole (only the index and box pages change order);
   and pandoc reads the same words, without warnings.

Usage:  python3 pnv-pdf2rst.py programa-electoral-29n.pdf -o pnv.rst [--report R]
        python3 pnv-pdf2rst.py programa-electoral-29n.pdf --verify-only pnv.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

GREEN, WHITE, BODY, LEAD = "#0d724c", "#ffffff", "#111717", "#2a3030"


def line_block(page, line):
    return C.Block(page, [line])


def comment(blocks):
    return C.comment_blocks(sorted(blocks, key=lambda b: b.x0))


def page_cover(bs):
    lines = [l for b in bs for l in b.lines]
    es = [l for l in lines if "PROGRAMA ELECCIONES" in l.text or "GENERALES" in l.text]
    eu = [l for l in lines if l not in es]
    return C.heading(" ".join(l.text for l in es), "#", over=True) + \
        C.heading(" ".join(l.text for l in eu), "*", over=True)


def kicker_title(bs):
    """Small green letter-spaced kicker and the big page title."""
    out, used = [], []
    for b in bs:
        if b.font.color == GREEN and b.h < 11.5 and b.y0 < 120:
            out += C.wrap("*" + C.esc_inline(b.text) + "*") + [""]
            used.append(b)
    titles = [b for b in bs if b.h >= 35 and b.font.color == BODY]
    for b in titles:
        out += C.heading(b.text, "=")
        used.append(b)
    return out, used


def page_index(bs):
    out, used = kicker_title(bs)
    rest = [b for b in bs if b not in used]
    left = [line_block(b.page, l) for b in rest if b.x0 < 400 for l in b.lines]
    right = [line_block(b.page, l) for b in rest if b.x0 >= 400 for l in b.lines]
    rows = []
    for l in left:
        r = min(right, key=lambda r: abs(r.y0 - l.y0))
        if abs(r.y0 - l.y0) > 4:
            raise C.ConvertError(f"index entry without page: {l.text!r}")
        right.remove(r)
        chapter = l.h > 12
        rows.append([("**%s**" if chapter else "%s") % C.esc_start(C.esc_inline(l.text)), C.esc_inline(r.text)])
    if right:
        raise C.ConvertError(f"index page numbers without entry: {[r.text for r in right]}")
    return out + C.list_table(rows, widths=[90, 10])


def page_commitments(bs):
    out, used = kicker_title(bs)
    rest = [b for b in bs if b not in used]
    boxes = sorted((b for b in rest if b.font.color == WHITE and b.h > 25), key=lambda b: b.y0)
    rows = []
    for k, n in enumerate(boxes):
        y_end = boxes[k + 1].y0 - 5 if k + 1 < len(boxes) else 900
        inside = [b for b in rest if n.y0 - 5 <= b.y0 < y_end]
        left = sorted((b for b in inside if b.x0 < 250), key=lambda b: b.y0)
        right = [b for b in inside if b.x0 >= 250]
        cell = " ".join(C.inline(b.chars()) for b in left)
        rows.append([C.esc_start(cell), " ".join(C.esc_start(C.inline(b.chars())) for b in right)])
    return out + C.list_table(rows, widths=[40, 60])


def page_opener(bs):
    """Chapter opener: 01 / COMPROMISO 1 / TITLE / tagline / EN ESTE CAPÍTULO + list."""
    big = next(b for b in bs if b.h > 100)
    label = next(b for b in bs if b.text.startswith("COMPROMISO"))
    title = next(b for b in bs if 30 < b.h < 100)
    tag = [b for b in bs if b.font.name.startswith("Playfair")]
    inchap = [b for b in bs if b.text == "EN ESTE CAPÍTULO"]
    out = C.heading(title.text, "=")
    out += C.wrap(f"**{C.esc_inline(big.text)}** {C.esc_inline(label.text)}") + [""]
    for b in tag:
        out += C.wrap(C.esc_start(C.inline(b.chars(), strong=lambda f: False, emph=lambda f: True))) + [""]
    for b in inchap:
        out += C.wrap("**" + C.esc_inline(b.text) + "**") + [""]
    rest = [b for b in bs if b not in [big, label, title] + tag + inchap]
    nums = sorted((b for b in rest if re.fullmatch(r"\d\.\d", b.text)), key=lambda b: b.y0)
    for n in nums:
        t = next(b for b in rest if b not in nums and abs(b.y0 - n.y0) < 3)
        out += C.wrap(f"{C.esc_inline(n.text)} {C.esc_inline(t.text)}", "  ", "* ")
    return out + [""]


def flow(pages_bs, out, counts):
    """Ordinary pages, in pdftotext order; paragraphs continue across pages."""
    para = None                  # [chars, kind]

    def flush():
        nonlocal para
        if para:
            chars, kind = para
            if kind == "item":
                out.extend(C.wrap(C.inline(chars), "  ", "* ") + [""])
            else:
                out.extend(C.wrap(C.esc_start(C.inline(chars))) + [""])
            para = None

    for p, bs, header, footer in pages_bs:
        out += comment(header)
        first = True
        i = 0
        while i < len(bs):
            b = bs[i]
            t = b.text
            if b.h >= 35:
                flush()
                out += C.heading(t, "=")
                counts["titles"] += 1
            elif b.font.color == GREEN and b.h < 11.5 and b.y0 < 120 and t.isupper():
                flush()
                out += C.wrap("*" + C.esc_inline(t) + "*") + [""]
            elif b.h >= 19 and b.font.color == BODY and re.match(r"\d\.\d ", t):
                flush()
                out += C.heading(t, "-")
                counts["sections"] += 1
            elif b.font.color == GREEN and 12.5 < b.h < 14 and t.isupper():
                flush()
                out += C.heading(t, "~")
                counts["groups"] += 1
            elif re.fullmatch(r"\d\d", t) and b.font.color == GREEN and b.x0 < 80:
                flush()
                text = next((x for x in bs[i + 1:i + 3] if x.x0 > 85 and abs(x.y0 - b.y0) < 3), None)
                if text is None:
                    raise C.ConvertError(f"p{p}: proposal {t} without text")
                para = [[(c, C.Font(f.family, f.size, f.color, True)) for c, f in b.chars()]
                        + [(" ", C.NOFONT)] + text.chars(), "item"]
                counts["proposals"] += 1
                i = bs.index(text)
            elif b.x0 > 85 and para and para[1] == "item" and first:
                para[0] += [(" ", C.NOFONT)] + b.chars()        # proposal continued on this page
            elif t.isupper() and len(t) < 60 and (b.font.color == WHITE or b.font.bold or b.h < 12):
                flush()
                out += C.wrap("**" + C.esc_inline(t) + "**") + [""]
            else:
                kind = "lead" if b.font.color == LEAD else "body"
                cont = para and para[1] == kind and (
                    (first and (not "".join(c for c, _ in para[0]).rstrip().endswith(C.TERMINAL)
                                or t[:1].islower()))
                    or (kind == "lead" and not first))
                if cont:
                    para[0] += [(" ", C.NOFONT)] + b.chars()
                else:
                    flush()
                    para = [b.chars(), kind]
            first = False
            i += 1
        if footer:
            flush()
        out += comment(footer)
    flush()


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = {}
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    out, counts = [], {"titles": 0, "sections": 0, "groups": 0, "proposals": 0}
    pending = []
    for p in sorted(pages):
        bs = pages[p]
        header = [b for b in bs if b.y0 < 45]
        footer = [b for b in bs if b.y0 > 785]
        body = [b for b in bs if b not in header and b not in footer]
        texts = " ".join(b.text for b in body)
        special = None
        if p == 1:
            special = page_cover(body)
        elif any(b.text == "ÍNDICE" and b.h > 35 for b in body):
            special = page_index(body)
        elif "CINCO COMPROMISOS" in texts:
            special = page_commitments(body)
        elif any(b.h > 100 for b in body):
            special = page_opener(body)
        if special is not None:
            if pending:
                flow(pending, out, counts)
                pending = []
            out += comment(header) + special + comment(footer)
        else:
            pending.append((p, body, header, footer))
    if pending:
        flow(pending, out, counts)
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, join_u2010=False)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the EAJ-PNV programme"))
