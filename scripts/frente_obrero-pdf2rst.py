#!/usr/bin/env python3
"""
frente_obrero-pdf2rst.py - Convert the Frente Obrero programme "Recuperar
España - Programa político" (PDF) to reStructuredText, repeatably, and verify
that no text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Each text
   block of this PDF is one paragraph; a hyphen at a line end inside a block
   is joined like plain pdftotext joins it.
2. Write RST:
     * the cover and back page are images (no text); "UN PROGRAMA PARA
       RECUPERAR ESPAÑA", the running head of the chapter dividers, is the
       document title
     * ÍNDICE -> a table (number | chapter | page)
     * each chapter's divider page (big number and title, repeated on the
       next page) -> an RST comment, kept but not rendered
     * chapter headings ("01 LA ESPAÑA QUE DEBEMOS SER") -> level 1; the drop
       cap is joined to its paragraph ("E" + "l Frente..." -> "El Frente...")
     * numbered measures ("1." ...) -> bullet items keeping their number
     * paragraphs joined across page breaks; bold and italics kept
     * page numbers ("-5-") -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order (plain pdftotext prints the index
   column by column); pandoc must read the same words, without warnings.

Usage:  python3 frente_obrero-pdf2rst.py Programa.pdf -o frente_obrero.rst
        python3 frente_obrero-pdf2rst.py Programa.pdf --verify-only frente_obrero.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)


def style(chars) -> str:
    """Bold and italics as printed (Avenir-MediumOblique is italic, not bold)."""
    return C.inline(chars, strong=lambda f: f.bold and not f.italic, emph=lambda f: f.italic)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def rows(blocks, tol=5):
    out, row = [], []
    for b in sorted(blocks, key=lambda b: (b.y0, b.x0)):
        if row and b.y0 > row[0].y0 + tol:
            out += sorted(row, key=lambda b: b.x0)
            row = []
        row.append(b)
    return out + sorted(row, key=lambda b: b.x0)


def is_page_number(b):
    return b.y0 > 795 and re.fullmatch(r"-\d+-", b.text)


def is_divider(bs):
    return any(b.h > 200 for b in bs)


def page_index(bs):
    title = next(b for b in bs if b.text == "ÍNDICE")
    nums = sorted((b for b in bs if re.fullmatch(r"\d\d", b.text) and b.font.size == 21), key=lambda b: b.y0)
    pages = [b for b in bs if b.text.startswith("PÁG.")]
    names = [b for b in bs if b not in nums + pages + [title]]
    out = heading(title.text, "=")
    table = []
    def mid(b):
        return (b.y0 + b.y1) / 2

    for n in nums:                       # same row: compare vertical centres (sizes differ)
        name = min(names, key=lambda b: abs(mid(b) - mid(n)))
        page = min(pages, key=lambda b: abs(mid(b) - mid(n)))
        if abs(mid(name) - mid(n)) > 6 or abs(mid(page) - mid(n)) > 6:
            raise C.ConvertError(f"index entry {n.text} without its chapter or page")
        names.remove(name)
        pages.remove(page)
        table.append([C.esc_inline(n.text), C.esc_start(C.esc_inline(name.text)), C.esc_inline(page.text)])
    if names or pages:
        raise C.ConvertError(f"index: lines left over {[b.text for b in names + pages]}")
    return out + C.list_table(table, widths=[8, 80, 12])


class Writer:
    def __init__(self):
        self.out, self.para, self.pending = [], None, []      # para: [chars, kind]

    def flush(self):
        if self.para:
            chars, kind = self.para
            txt = C.esc_start(style(chars))
            self.out += (C.wrap(txt, "  ", "* ") if kind == "item" else C.wrap(txt)) + [""]
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
            self.pending.append(c)       # after the paragraph that runs across the page
        else:
            self.out += c

    def start(self, chars, kind):
        self.flush()
        self.para = [list(chars), kind]


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = {}
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    w = Writer()
    counts = {"chapters": 0, "measures": 0, "dividers": 0}
    # document title: the running head of the first chapter divider
    first_div = next(p for p in sorted(pages) if is_divider(pages[p]))
    head = next(b for b in pages[first_div] if b.font.name.startswith("Avenir-Heavy") and b.font.size == 12)
    w.emit(heading(head.text, "#", over=True))
    dividers = []
    for p in sorted(pages):
        bs = pages[p]
        footer = [b for b in bs if is_page_number(b)]
        body = [b for b in bs if b not in footer and b is not head]
        if any(b.text == "ÍNDICE" for b in body):
            w.emit(page_index(body))
        elif is_divider(body):
            num = next(b for b in body if b.h > 200)
            title = [b for b in body if b.font.size > 50 and b is not num]
            dividers.append(f"{num.text} {' '.join(b.text for b in rows(title))}")
            w.emit(C.comment_blocks([num] + rows(title)) +
                   C.comment_blocks([b for b in body if b is not num and b not in title]))
            counts["dividers"] += 1
        else:
            flow(w, body, counts, dividers)
        w.comment(footer)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def flow(w, bs, counts, dividers):
    first = True
    skip = set()
    cap = None
    prev = None
    for b in bs:
        if b in skip:
            continue
        t = b.text
        if b.font.size >= 80 and re.fullmatch(r"\d\d", t):              # chapter number + title
            title = [o for o in bs if o.font.name.startswith("Univers") and o.y0 < 140]
            skip.update(title)
            name = f"{t} {' '.join(o.text for o in rows(title))}"
            w.emit(heading(name, "="))
            counts["chapters"] += 1
            if dividers and dividers[-1] != name:
                raise C.ConvertError(f"chapter {name!r} does not match its divider {dividers[-1]!r}")
        elif b.font.size >= 50 and len(t) == 1:                          # drop cap
            cap = b.chars()
        elif re.fullmatch(r"\d{1,2}\.", t) and b.x0 < 60:                 # "1." + its text
            mate = next((o for o in bs if o is not b and abs(o.y0 - b.y0) < 4 and o.x0 > b.x1), None)
            if mate is None:
                raise C.ConvertError(f"p{b.page}: measure {t} without its text")
            skip.add(mate)
            w.start(b.chars() + [SPACE] + mate.chars(), "item")
            counts["measures"] += 1
        elif re.match(r"\d{1,2}\. \S", t) and b.x0 < 60:                  # "10. Reforzar ..." in one block
            w.start(b.chars(), "item")
            counts["measures"] += 1
        elif first and w.para and (not "".join(c for c, _ in w.para[0]).rstrip().endswith(C.TERMINAL)
                                   or t[:1].islower()):
            w.para[0] += [SPACE] + b.chars()                              # continued from the previous page
        elif w.para and prev is not None and abs(b.x0 - prev.x0) < 3 and b.y0 - prev.y1 < 3:
            # the same paragraph split in two blocks (the font size changes): join like
            # pdftotext joins lines, a hyphen at the break included ("sub-" + "venciones")
            if w.para[0] and w.para[0][-1][0] == "-":
                w.para[0].pop()
            else:
                w.para[0].append(SPACE)
            w.para[0] += b.chars()
        else:
            chars = b.chars()
            if cap:                       # drop cap: a letter of the paragraph's first word, in its font
                chars, cap = [(c, chars[0][1]) for c, _ in cap] + chars, None
            w.start(chars, "para")
        first = False
        prev = b
    if cap:
        raise C.ConvertError("drop cap without its paragraph")


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Frente Obrero programme", default_output="frente_obrero.rst"))
