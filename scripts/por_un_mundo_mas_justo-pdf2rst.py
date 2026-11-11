#!/usr/bin/env python3
"""
por_un_mundo_mas_justo-pdf2rst.py - Convert the Por un Mundo Más Justo (M+J)
programme "Programa electoral · Elecciones Generales 29N 2026" (PDF,
LibreOffice) to reStructuredText, repeatably, and verify that no text was
altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py). The PDF
   gives every printed line as its own text block, so paragraphs and list
   items are rebuilt from the line spacing, the markers and the fonts.
2. Write RST:
     * the cover and back page are images (no text); the "PROGRAMA ELECTORAL
       · 29N 2026" kicker of the index is the document title
     * ÍNDICE -> a table (number | axis | page); "Guía por colectivos" ->
       one list of axes per group
     * each axis ("EJE 01 ..."): heading "01 Vivienda", the kicker in italics,
       its "Te interesa si te importa:" tags as a list, the lead in italics
     * OBJETIVOS / MEDIDAS / EL ESFUERZO EN CIFRAS / COMPROMISOS MEDIBLES /
       FUENTES CONSULTADAS -> level 2; the numbered measures of axis 01 ->
       level 3
     * measures ("01 ...", "1. ...") -> bullet items with their number;
       "a." sub-items nested; "•" bullets -> RST bullets (glyph dropped)
     * tables (QUÉ / HOY / META / PLAZO ...) -> list-tables; figure strips ->
       bullets (figure + caption)
     * paragraphs re-flowed and joined across page breaks; bold, italics and
       small print (captions, sources: italics) kept
     * a hyphen at a line end is kept: this PDF is not hyphenated, so every
       one is real ("aprendizaje-servicio", URLs); a URL broken over lines is
       rejoined
     * running header and footer (with the page number) -> RST comments
       (kept, not rendered), so nothing of the PDF is dropped
3. Verify against plain pdftotext: the RST text is exactly the PDF's lines,
   each used once and whole, in any order (plain pdftotext prints the index,
   tables and figure strips column by column); the bullet glyphs are the
   only text set aside, and the line-end hyphens plain pdftotext drops are
   checked against ``pdftotext -layout``. pandoc must read the same words,
   without warnings.

Usage:  python3 por_un_mundo_mas_justo-pdf2rst.py Castellano.pdf -o por_un_mundo_mas_justo.rst
        python3 por_un_mundo_mas_justo-pdf2rst.py Castellano.pdf --verify-only por_un_mundo_mas_justo.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

ACCENT = ("#c98d6a", "#c88d6a")          # orange labels, numbers, bullet glyphs
LABEL = ("#8a5c46", "#895c46")           # small brown labels
GAP = 23.5                               # a larger step between lines starts a new paragraph
BULLET = "•"


def style(chars) -> str:
    """Bold and italics as printed; small print (captions, sources) in italics.
    Inside a URL "_" needs no escape (and pandoc would keep the backslash)."""
    txt = C.inline(chars, strong=lambda f: f.bold and not f.italic,      # MediumItalic: italic
                   emph=lambda f: f.italic or 0 < f.size <= 8 and not f.bold)
    return re.sub(r"https?://\S+", lambda m: m.group(0).replace("\\_", "_"), txt)


def heading(text, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(text)), ch, over, escape=False)


def lines_of(blocks):
    """Every printed line as a one-line Block (pdftotext order)."""
    return [C.Block(b.page, [ln]) for b in blocks for ln in b.lines]


def rows(lines, tol=3):
    """Lines in reading order: by y (within tol), then left to right."""
    out, row = [], []
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        if row and ln.y0 > row[0].y0 + tol:
            out += sorted(row, key=lambda l: l.x0)
            row = []
        row.append(ln)
    return out + sorted(row, key=lambda l: l.x0)


def joined(lines, sep=(" ", C.NOFONT)):
    out = []
    for ln in lines:
        if out:
            out.append(sep)
        out += ln.chars()
    return out


def is_accent_label(ln):
    f = ln.font
    return f.name.startswith("Poppins-ExtraBold") and f.color in ACCENT and f.size <= 10 \
        and re.search(r"[A-ZÁÉÍÓÚÑ]{3}", ln.text) and ln.text.upper() == ln.text


# ------------------------------------------------------------------ special pages
def page_index(lines):
    kicker = [l for l in lines if l.y0 < 80 and l.font.color in ACCENT]
    title = [l for l in lines if l.h > 40]
    sub = [l for l in lines if l.font.size == 10 and l.font.color in LABEL and l.x0 < 70]
    nums = sorted((l for l in lines if l.x0 < 110 and l.font.color in ACCENT and l not in kicker),
                  key=lambda l: l.y0)
    names = [l for l in lines if 120 < l.x0 < 140]
    pages = [l for l in lines if l.x0 > 500]
    used = set(kicker + title + sub + nums + names + pages)
    if len(used) != len(lines):
        raise C.ConvertError(f"index: unexpected lines {[l.text for l in lines if l not in used]}")
    out = heading(" ".join(l.text for l in sorted(kicker, key=lambda l: l.x0)), "#", over=True)
    out += heading(" ".join(l.text for l in title), "=")
    out += C.wrap(C.esc_start(style(joined(sub)))) + [""]
    table = []
    for n in nums:
        name = min(names, key=lambda l: abs(l.y0 - n.y0))
        page = min(pages, key=lambda l: abs(l.y0 - n.y0))
        if abs(name.y0 - n.y0) > 4 or abs(page.y0 - n.y0) > 4:
            raise C.ConvertError(f"index entry {n.text} without its axis or page")
        names.remove(name)
        pages.remove(page)
        table.append([C.esc_start(C.esc_inline(n.text)), C.esc_start(C.esc_inline(name.text)),
                      C.esc_inline(page.text)])
    if names or pages:
        raise C.ConvertError("index: entries left without a number")
    return out + C.list_table(table, widths=[8, 80, 12])


def page_guide(lines):
    kicker = [l for l in lines if is_accent_label(l)]
    title = [l for l in lines if l.h > 40]
    sub = [l for l in lines if l.font.size == 10 and l.font.color in LABEL]
    groups = [l for l in lines if l.font.name.startswith("Poppins-ExtraBold") and l.font.size == 11]
    items = [l for l in lines if l not in kicker + title + sub + groups]
    out = []
    for k in kicker:
        out += C.wrap("*" + C.esc_inline(k.text) + "*") + [""]
    out += heading(" ".join(l.text for l in title), "=")
    out += C.wrap(C.esc_start(style(joined(sub)))) + [""]
    for g in rows(groups):
        below = [o for o in groups if abs(o.x0 - g.x0) < 3 and o.y0 > g.y0]
        end = min((o.y0 for o in below), default=900)
        mine = sorted((l for l in items if abs(l.x0 - g.x0) < 40 and g.y0 < l.y0 < end and l.x0 >= g.x0 - 1),
                      key=lambda l: l.y0)
        out += heading(g.text, "-")
        entries = []
        for l in mine:
            items.remove(l)
            if re.match(r"\d\d ", l.text) or not entries:
                entries.append([l])
            else:
                entries[-1].append(l)
        for e in entries:
            out += C.wrap(C.esc_inline(" ".join(l.text for l in e)), "  ", "* ")
        out += [""]
    if items:
        raise C.ConvertError(f"guide: lines outside any group {[l.text for l in items]}")
    return out


def opener(lines):
    """Axis opener: big number, kicker, title, tags, lead. Returns (rst, used)."""
    big = next(l for l in lines if l.h > 100)
    kick = [l for l in lines if abs(l.y0 - big.y1 + 10) < 6 and l is not big]
    kick_y = min(l.y0 for l in kick)
    kick = [l for l in lines if abs(l.y0 - kick_y) < 3 and l is not big]
    title = [l for l in lines if l.font.name.startswith("Poppins-ExtraBold") and l.font.size == 30]
    lead = [l for l in lines if l.font.italic and l.font.size == 13]
    end = min([l.y0 for l in lead] + [l.y0 for l in lines if l.y0 > max(t.y1 for t in title) and
                                          l.font.size > 8] + [900])
    tags = [l for l in lines if max(t.y1 for t in title) < l.y0 < end and l.font.size == 8]
    out = heading(f"{big.text} " + " ".join(l.text for l in title), "=")
    out += C.wrap("*" + C.esc_inline(" ".join(l.text for l in sorted(kick, key=lambda l: l.x0))) + "*") + [""]
    if tags:
        chars = joined(rows(tags), sep=("\n", C.NOFONT))    # "\n" separates tags
        k = next(i for i, (c, f) in enumerate(chars) if f.color not in LABEL and not c.isspace())
        label = "".join(c for c, _ in chars[:k]).strip()
        names = [t.strip() for t in "".join(c for c, _ in chars[k:]).split("\n")]
        out += C.wrap(C.esc_start(C.esc_inline(label))) + [""]
        out += [line for t in names for line in C.wrap(C.esc_inline(t), "  ", "* ")] + [""]
    if lead:
        out += C.wrap(C.esc_start(style(joined(rows(lead))))) + [""]
    return out, set([big] + kick + title + tags + lead)


# ------------------------------------------------------------------ tables and strips
def tables(lines):
    """Header rows: 3+ orange ExtraBold upper-case labels on one line."""
    out = []
    labels = [l for l in lines if is_accent_label(l) and l.font.size <= 9]
    for l in labels:
        head = sorted((o for o in labels if abs(o.y0 - l.y0) < 2), key=lambda o: o.x0)
        if len(head) < 3 or head[0] is not l:
            continue
        x_min = head[0].x0 - 3
        body = []
        for o in rows([o for o in lines if o.y0 > max(h.y1 for h in head) - 1 and o not in head]):
            if o.x0 < x_min:
                break
            body.append(o)
        out.append((head, body))
    return out


def render_table(head, body):
    groups, bottom = [], None
    for ln in sorted(body, key=lambda l: l.y0):
        if not groups or ln.y0 > bottom + 4:        # rows are separated by a clear gap
            groups.append([])
            bottom = ln.y1
        groups[-1].append(ln)
        bottom = max(bottom, ln.y1)
    xs = [h.x0 for h in head]
    table = [[C.esc_start(C.esc_inline(h.text)) for h in head]]
    for g in groups:
        cells = [[] for _ in xs]
        for ln in g:
            col = max(i for i, x in enumerate(xs) if x <= ln.x0 + 3)
            cells[col].append(ln)
        table.append([C.esc_start(style(joined(sorted(c, key=lambda l: l.y0)))) if c else "" for c in cells])
    return C.list_table(table, header=True)


def strips(lines):
    """Rows of big figures (ExtraBold 15, dark) with small captions below."""
    figs = [l for l in lines if l.font.name.startswith("Poppins-ExtraBold") and l.font.size == 15
            and l.font.color not in ACCENT]
    out = []
    for f in figs:
        row = sorted((o for o in figs if abs(o.y0 - f.y0) < 2), key=lambda o: o.x0)
        if len(row) < 2 or row[0] is not f:
            continue
        items = []
        for g in row:
            caps = [o for o in lines if abs(o.x0 - g.x0) < 2 and g.y0 < o.y0 < g.y1 + 40 and o.font.size <= 8]
            items.append((g, sorted(caps, key=lambda o: o.y0)))
        out.append(items)
    return out


def render_strip(items):
    out = []
    for g, caps in items:
        out += C.wrap(C.esc_start(style(joined([g] + caps))), "  ", "* ")
    return out + [""]


# ------------------------------------------------------------------ flow
class Writer:
    """Paragraphs and list items, joined across lines and page breaks."""

    def __init__(self):
        self.out = []
        self.top = None          # open unit: {"kind", "body": [("p"|"sub", chars)], ...}
        self.cur = None          # chars list that continuation lines extend
        self.last = None         # last line added
        self.pending = []        # page furniture comments waiting for the open unit to close
        self.hyphens = []

    # -- units
    def flush(self):
        u = self.top
        if u:
            self.out += self.render(u)
        self.top = self.cur = self.last = None
        for c in self.pending:
            self.out += c
        self.pending = []

    def emit(self, lines):
        self.flush()
        self.out += lines

    def comment(self, blocks):
        c = C.comment_blocks(blocks)
        if self.top:
            self.pending.append(c)
        else:
            self.out += c

    def start(self, kind, chars, ln, xtext=None):
        self.flush()
        self.cur = list(chars)
        self.top = {"kind": kind, "body": [("p", self.cur)]}
        self.last, self.xtext = ln, xtext if xtext is not None else ln.x0

    def sub(self, chars, ln):
        self.cur = list(chars)
        self.top["body"].append(("sub", self.cur))
        self.last = ln

    def new_par_in_item(self, ln):
        self.cur = ln.chars()
        self.top["body"].append(("p", self.cur))
        self.last = ln

    def extend(self, ln):
        text = "".join(c for c, _ in self.cur).rstrip()
        prev = self.last.text
        url = "://" in prev.split()[-1] or getattr(self.last, "url_part", False)
        if text.endswith("-"):
            self.hyphens.append(prev.split()[-1] + ln.text.split()[0])
            ln.url_part = url and len(ln.text.split()) == 1
        elif url and len(ln.text.split()) == 1:
            ln.url_part = True                        # URL broken over lines: rejoin
        else:
            self.cur.append((" ", C.NOFONT))
        self.cur += ln.chars()
        self.last = ln

    @staticmethod
    def render(u):
        kind, body = u["kind"], u["body"]
        first = C.esc_start(style(body[0][1]))
        if kind == "para":
            out = C.wrap(first) + [""]
        else:
            out = C.wrap(first, "  ", "* ") + [""]
        for what, chars in body[1:]:
            txt = C.esc_start(style(chars))
            out += (C.wrap(txt, "    ", "  * ") if what == "sub" else C.wrap(txt, "  ")) + [""]
        return out


def row_mates(ln, lines):
    return sorted((o for o in lines if o is not ln and abs(o.y0 - ln.y0) < 3 and o.x0 > ln.x1),
                  key=lambda o: o.x0)


def flow(w, seq):
    """One page of ordinary text, in reading order; tables and strips come
    pre-rendered as ("special", rst) at their place."""
    first = True
    skip = set()
    page_lines = [x for x in seq if not isinstance(x, tuple)]
    for ln in seq:
        if isinstance(ln, tuple):
            w.emit(ln[1])
            first = False
            continue
        if ln in skip:
            continue
        t = ln.text
        mates = [m for m in row_mates(ln, page_lines) if m not in skip]
        f = ln.font
        if is_accent_label(ln):
            w.flush()
            w.emit(heading(t, "-"))
            if mates:                        # "FUENTES CONSULTADAS  [6] ...  [9] ..."
                w.emit([s for m in mates for s in C.wrap(C.esc_inline(m.text), "  ", "* ")] + [""])
                skip.update(mates)
        elif re.fullmatch(r"\d\d", t) and f.color in ACCENT and ln.x0 < 70 and f.size == 15:
            title = [ln] + mates
            nxt = [o for o in page_lines if o not in title and abs(o.x0 - mates[0].x0) < 2
                   and o.font.size == 13 and 0 < o.y0 - title[-1].y0 < GAP + 2] if mates else []
            title += nxt
            skip.update(title)
            w.emit(heading(" ".join(o.text for o in title), "~"))
        elif re.fullmatch(r"\d\d", t) and f.color in ACCENT and ln.x0 < 70 and mates:
            skip.update(mates[:1])
            w.start("item", ln.chars() + [(" ", C.NOFONT)] + mates[0].chars(), mates[0], mates[0].x0)
        elif re.fullmatch(r"\d{1,2}\.", t) and ln.x0 < 130 and mates:
            skip.update(mates[:1])
            w.start("item", ln.chars() + [(" ", C.NOFONT)] + mates[0].chars(), mates[0], mates[0].x0)
        elif re.match(r"\d{1,2}\. \S", t) and ln.x0 < 130 and ln.chars()[0][1].bold:
            w.start("item", ln.chars(), ln, ln.x0 + 18)
        elif w.top and w.top["kind"] == "item" and 85 < ln.x0 < 100 and (
                re.fullmatch(r"[a-z]\.", t) and mates or re.match(r"[a-z]\. \S", t)):
            if mates and re.fullmatch(r"[a-z]\.", t):
                skip.update(mates[:1])
                w.sub(ln.chars() + [(" ", C.NOFONT)] + mates[0].chars(), mates[0])
            else:
                w.sub(ln.chars(), ln)
        elif t.startswith(BULLET):
            chars = ln.chars()
            k = 1
            while k < len(chars) and chars[k][0].isspace():
                k += 1
            w.start("item", chars[k:], ln, ln.x0 + 13)
        elif re.match(r"\[\d+\] ", t) and ln.x0 < 70:
            w.start("item", ln.chars(), ln, ln.x0 + 25)
        elif w.top and w.last and w.last.page == ln.page and ln.y0 - w.last.y0 <= GAP:
            w.extend(ln)
        elif w.top and w.last and w.last.page == ln.page and w.top["kind"] == "item" \
                and abs(ln.x0 - w.xtext) <= 3 and ln.x0 > 70:
            w.new_par_in_item(ln)
        elif w.top and first and w.last and w.last.page != ln.page and (
                not "".join(c for c, _ in w.cur).rstrip().endswith(C.TERMINAL) or t[:1].islower()):
            w.extend(ln)                     # continued from the previous page
        else:
            w.start("para", ln.chars(), ln)
        first = False


# ------------------------------------------------------------------ main
def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = {}
    for ln in lines_of(blocks):
        pages.setdefault(ln.page, []).append(ln)
    w = Writer()
    counts = {"axes": 0, "tables": 0, "strips": 0}
    for p in sorted(pages):
        lines = pages[p]
        header = [l for l in lines if l.y0 < 45 and l.text.startswith("M+J")]
        footer = [l for l in lines if l.y0 > 785]
        body = [l for l in lines if l not in header and l not in footer]
        w.comment(sorted(header, key=lambda l: l.x0))
        texts = {l.text for l in body}
        if "Índice" in texts:
            w.emit(page_index(body))
        elif "Guía por colectivos" in texts:
            w.emit(page_guide(body))
        else:
            used = set()
            if any(l.h > 100 for l in body):
                rst, used = opener(body)
                w.emit(rst)
                counts["axes"] += 1
            rest = [l for l in body if l not in used]
            specials = []
            for head, tbody in tables(rest):
                specials.append((min(l.y0 for l in head), render_table(head, tbody), head + tbody))
                counts["tables"] += 1
            for items in strips(rest):
                taken = [x for g, caps in items for x in [g] + caps]
                specials.append((min(l.y0 for l in taken), render_strip(items), taken))
                counts["strips"] += 1
            taken = {x for s in specials for x in s[2]}
            seq = [l for l in rows(rest) if l not in taken]
            for y, rst, _ in sorted(specials, key=lambda s: s[0]):
                k = next((i for i, l in enumerate(seq) if not isinstance(l, tuple) and l.y0 > y), len(seq))
                seq.insert(k, ("special", rst))
            flow(w, seq)
        w.comment(sorted(footer, key=lambda l: l.x0))
    w.flush()
    out = w.out
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    report.append(f"line-end hyphens kept: {len(w.hyphens)} {w.hyphens}")
    return "\n".join(out) + "\n"


def verify(rst, raw, report, pdf=None):
    breaks = C.layout_hyphen_breaks(pdf) if pdf else []
    return C.verify_blocks(rst, raw, report, join_u2010=False, hyphen_breaks=breaks,
                           strip_start=BULLET)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the Por un Mundo Más Justo programme",
                    default_output="por_un_mundo_mas_justo.rst"))
