#!/usr/bin/env python3
"""
psoe-pdf2rst.py - Convert the PSOE programme "La fuerza de seguir avanzando
- Programa elecciones generales 29 noviembre 2026" (PDF, Word, A4) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for words and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). Word
   exported almost every line as a block of its own (and a few justified
   lines word by word), so the lines are rebuilt from the words' positions
   and the paragraphs from the line spacing (19 pt inside a paragraph, 27
   between paragraphs).
2. Write RST:
     * the cover's "LA FUERZA DE SEGUIR AVANZANDO." and "PROGRAMA ELECCIONES
       GENERALES 29 NOVIEMBRE 2026" -> document title and subtitle
     * ÍNDICE -> a table (section | page), without the dot leaders; entries
       that wrap to a second line are joined
     * chapter titles (red, 22 pt) -> level 1; their sections (18 pt) ->
       level 2; subsections (Arial 14 pt) -> level 3; the photo page before
       each chapter, with its short title ("Un modelo económico para el
       crecimiento.") -> an RST comment, kept but not rendered
     * "Compromisos" and the other small red labels -> bold paragraphs
     * the 500 numbered commitments ("1. Promover desde lo público...") ->
       bullet items keeping their number and bold; their "o" points ->
       nested bullets, their lettered points ("a. modernizar ...") ->
       nested bullets keeping the letter; the one "●" point -> a bullet;
       the glyphs are kept in RST comments ("o" is also a word, so it cannot
       be set aside by position); the commitments printed in a red box
       repeat their number under or in the box ("1 1.", "22.", "247247." in
       plain pdftotext): that copy -> an RST comment right before the item
       (for 426 and 449 that copy follows the label, so it stays in the
       text: "426. 426 Reforzar...", as plain pdftotext reads it)
     * paragraphs and items joined across page breaks; bold and italics kept
     * page numbers -> RST comments
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order; the index's dot leaders are the
   only text set aside. pandoc must read the same words, without warnings.

Usage:  python3 psoe-pdf2rst.py PROGRAMAPSOE29N26.pdf -o psoe.rst
        python3 psoe-pdf2rst.py PROGRAMAPSOE29N26.pdf --verify-only psoe.rst
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

SPACE = (" ", C.NOFONT)
RED, WHITE = "#ee0000", "#ffffff"
PARA_GAP = 23.0              # line pitch 19 inside a paragraph, 27 between paragraphs
LABEL_MIN_X, LABEL_X = 76, 100   # commitment numbers start at x 79-86, their text at 114.6
TEXT_X, SUB_X, SUBTEXT_X, DOT_TEXT_X = 114.6, 108.0, 126.0, 90.0
LETTER_X, LETTERTEXT_X = 144.0, 162.0       # "a. modernizar..." and its next lines
INDEX_ENTRY = re.compile(r"(.*?)\s*\.{4,}\s*(\d+)")
LEVEL = {22: "=", 18: "-", 14: "~"}


def style(chars):
    return C.inline(chars, strong=lambda f: f.bold, emph=lambda f: f.italic and not f.bold)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def first_font(b):
    return next((f for c, f in b.chars() if not c.isspace()), C.NOFONT)


def near(x, y, tol=3):
    return abs(x - y) < tol


def page_lines(blocks):
    """Lines rebuilt from the words of each page: same baseline, left to right."""
    words = collections.defaultdict(list)
    for b in blocks:
        for ln in b.lines:
            words[b.page] += ln.words
    pages = {}
    for p, ws in sorted(words.items()):
        lines = []
        for w in sorted(ws, key=lambda w: ((w.y0 + w.y1) / 2, w.x0)):
            mid = (w.y0 + w.y1) / 2
            if lines and abs(mid - lines[-1][0]) < 3:
                lines[-1][1].append(w)
            else:
                lines.append([mid, [w]])
        pages[p] = [C.Block(p, [C.Line(sorted(lw, key=lambda w: w.x0))]) for _, lw in lines]
    return pages


def is_page_number(b):
    f = first_font(b)
    return b.y0 > 770 and f.color == RED and re.fullmatch(r"\d{1,3}", b.text)


def index_table(pages, ip, report):
    """The index pages: entries with dot leaders and page numbers -> rows.
    Word by word: the leader is a word of its own (only dots), the page
    number the last one; an entry may wrap to a second line."""
    title = next(b for b in pages[ip[0]] if b.text == "ÍNDICE")
    rows, entry = [], []
    for p in ip:
        for b in pages[p]:
            if b is title or is_page_number(b):
                continue
            ws = b.lines[0].words
            m = re.fullmatch(r"(\.{4,})?(\d+)", ws[-1].text)
            if not m or not (m.group(1) or len(ws) > 1 and re.fullmatch(r"\.{4,}", ws[-2].text)):
                entry.append(ws)                                 # the entry goes on in the next line
                continue
            entry.append(ws[:-1] if m.group(1) else ws[:-2])
            chars = []
            for line in entry:
                chars += ([SPACE] if chars else []) + C.Block(p, [C.Line(line)]).chars()
            rows.append([C.esc_start(style(chars)), C.esc_inline(m.group(2))])
            entry = []
    if entry:
        raise C.ConvertError(f"index entry without page: {' '.join(w.text for ws in entry for w in ws)!r}")
    report.append(f"index: {len(rows)} entries")
    return heading(title.text, "=") + C.list_table(rows, widths=[90, 10])


class Writer:
    """Paragraphs and items (with their points), joined across page breaks."""

    def __init__(self):
        self.out, self.item, self.para, self.pending = [], None, None, []
        self.box = None              # the item's number printed again in a red box
        self.number = 0              # the last commitment's number
        # item: list of [kind, chars], kind "p" (the item, its paragraphs) or "sub"

    def flush(self):
        if self.para is not None:
            self.out += C.wrap(C.esc_start(style(self.para))) + [""]
            self.para = None
        if self.item is not None:
            parts = self.item
            first = parts[0][1]
            if self.box:
                self.out += C.comment(self.box)
                self.box = None
            if parts[0][0] != "p":                                 # the item's glyph ("●")
                self.out += C.comment(parts[0][0])
            self.out += C.wrap(C.esc_start(style(first)), "  ", "* ") + [""]
            for kind, chars in parts[1:]:
                txt = C.esc_start(style(chars))
                if kind.startswith("sub") and kind != "sub":      # the point's glyph ("o")
                    self.out += ["  .. " + kind[3:], ""]
                self.out += (C.wrap(txt, "    ", "  * ") if kind.startswith("sub") else C.wrap(txt, "  ")) + [""]
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

def ends_open(chars):
    return not "".join(c for c, _ in chars).rstrip().endswith(C.TERMINAL)


def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = page_lines(blocks)
    counts = collections.Counter()
    w = Writer()
    # cover: title (29 pt) and subtitle (22 pt), white on the photo
    cover = pages[min(pages)]
    title = [b for b in cover if first_font(b).size >= 26]
    sub = [b for b in cover if 20 <= first_font(b).size < 26]
    if not title or not sub or len(title) + len(sub) != len(cover):
        raise C.ConvertError("cover: title and subtitle not found")
    w.emit(heading(" ".join(b.text for b in title), "#", over=True)
           + heading(" ".join(b.text for b in sub), "*", over=True))
    ip = [p for p in pages if any(INDEX_ENTRY.fullmatch(b.text) for b in pages[p])]
    if not ip or ip != list(range(ip[0], ip[-1] + 1)):
        raise C.ConvertError(f"index pages not found or not consecutive: {ip}")
    w.emit(index_table(pages, ip, report))
    for p in sorted(pages):
        if p == min(pages) or p in ip:
            w.comment([b for b in pages[p] if is_page_number(b)])
            continue
        bs = pages[p]
        footer = [b for b in bs if is_page_number(b)]
        body = [b for b in bs if b not in footer]
        if body and all(first_font(b).size >= 30 and first_font(b).color == WHITE for b in body):
            w.emit(C.comment_blocks(body))                   # photo page with the chapter's short title
            counts["chapter photo pages"] += 1
        else:
            flow(w, body, counts)
        w.comment(footer)
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def flow(w, bs, counts):
    prev = None                       # previous line on this page
    pending_box = None                # a boxed number seen above its item's first line
    i = 0
    while i < len(bs):
        b = bs[i]
        f = first_font(b)
        t = b.text
        gap = b.y0 - prev.y0 if prev is not None else None
        if f.color == RED and f.size in LEVEL and not f.name.startswith("Geomanist-Medium") or \
                f.color == RED and f.size == 22:
            parts = [b]                                          # a heading, maybe on several lines
            while i + 1 < len(bs) and first_font(bs[i + 1]).size == f.size \
                    and first_font(bs[i + 1]).color == RED and bs[i + 1].y0 - parts[-1].y1 < f.size:
                i += 1
                parts.append(bs[i])
            w.emit(heading(" ".join(x.text for x in parts), LEVEL[f.size]))
            counts[f"level {'=-~'.index(LEVEL[f.size]) + 1}"] += 1
            b = parts[-1]
        elif f.name.startswith("Geomanist-Medium") and f.size == 11:   # "Compromisos", small labels
            w.flush()
            w.para = b.chars()
            counts["labels"] += 1
        elif b.x0 < LABEL_X and all(c.isdigit() and ft.color == WHITE for c, ft in b.chars() if not c.isspace()):
            box_line = b.text                                    # the boxed number on its own line
            if w.item is not None and w.box is None and prev is not None and b.y0 - prev.y0 < 8:
                w.box = box_line                                 # just below its item's first line
            else:
                pending_box = box_line
            counts["boxed numbers"] += 1
        elif LABEL_MIN_X < b.x0 < LABEL_X and re.match(r"(\d+ ?)?\d+\.(?=\s|\d)", t) \
                and f.color in (RED, WHITE, "#000000"):
            chars = b.chars()
            k = 0
            while k < len(chars) and (chars[k][1].color == WHITE or chars[k][0].isspace()):
                k += 1
            dup = re.match(r"(\d+) ?(?=\1\.)", t)
            if not k and dup and int(dup.group(1)) == w.number + 1 \
                    and int(re.match(r"\d+", t).group(0)) != w.number + 1:
                k = dup.end()              # the box's number in red, under the box: "247247."
            m = re.match(r"\d+", "".join(c for c, _ in chars[k:]))
            w.number = int(m.group(0))
            after = re.match(r"(\d+)\.(?=\1 )", "".join(c for c, _ in chars[k:]))
            if after:                      # the box's number printed after the label, over it
                chars = chars[:k + after.end()] + [SPACE] + chars[k + after.end():]
                counts["box numbers after their label (kept)"] += 1
            box = "".join(c for c, _ in chars[:k]).strip()
            w.flush()
            if box:                                              # the number again, in a red box
                counts["boxed numbers"] += 1
            if pending_box:
                box, pending_box = (box + " " + pending_box).strip(), None
            w.box = box or None
            w.item = [["p", chars[k:]]]
            counts["commitments"] += 1
        elif t.startswith("●"):
            w.flush()
            w.item = [["●", strip_glyph(b.chars())]]
            counts["● points"] += 1
        elif near(b.x0, SUB_X) and f.name.startswith("CourierNew") and t.startswith("o "):
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: point outside a commitment: {t[:50]!r}")
            w.item.append(["subo", strip_glyph(b.chars())])
            counts["o points"] += 1
        elif near(b.x0, LETTER_X) and re.match(r"[a-z]\. ", t):
            if w.item is None:
                raise C.ConvertError(f"p{b.page}: lettered point outside a commitment: {t[:50]!r}")
            w.item.append(["sub", b.chars()])
            counts["lettered points"] += 1
        elif w.item is not None and any(near(b.x0, x) for x in (TEXT_X, SUBTEXT_X, DOT_TEXT_X, LETTERTEXT_X)):
            kind = "sub" if near(b.x0, SUBTEXT_X) or near(b.x0, LETTERTEXT_X) else "p"
            same = w.item[-1][0].startswith("sub") if kind == "sub" else w.item[-1][0] in ("p", "●")
            if not same or gap is not None and gap > PARA_GAP:
                w.item.append([kind, b.chars()])                 # a new paragraph of the item
            else:
                w.item[-1][1] += [SPACE] + b.chars()
        elif near(b.x0, 72) and f.color == "#000000":
            if w.para is not None and (gap is not None and gap < PARA_GAP
                                       or gap is None and (ends_open(w.para) or t[:1].islower())):
                w.para += [SPACE] + b.chars()
            else:
                w.flush()
                w.para = b.chars()
                counts["paragraphs"] += 1
        else:
            raise C.ConvertError(f"p{b.page} y{b.y0:.0f}: line not understood ({f}): {t[:60]!r}")
        prev = b
        i += 1


def strip_glyph(chars):
    k = 1
    while k < len(chars) and chars[k][0].isspace():
        k += 1
    return chars[k:]


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report, leaders=True)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the PSOE programme", default_output="psoe.rst"))
