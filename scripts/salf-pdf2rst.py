#!/usr/bin/env python3
"""
salf-pdf2rst.py - Convert the SALF "Contrato electoral simplificado,
Generales 2026" (PDF) to reStructuredText, repeatably, and verify that no
text was altered, omitted or added.

1. Extract with poppler-utils (``pdftotext -bbox-layout`` for text and
   positions, ``pdftohtml -xml`` for fonts; see pdf2rst_common.py).
2. Rebuild the reading order from the layout and write RST:
     * cover -> title and line blocks; closing page -> line block
     * chapter banners (I VIVIENDA ...) -> level 1, tagline in bold
     * each numbered measure -> level 2 heading "N title", its text, the
       cost note in italics and the side figure as a bullet
     * stat strips and org-chart boxes -> bullet lists (figure + caption)
     * "10 compromisos" and "Cómo se paga" -> tables
     * pull quotes -> block quotes
     * running headers, footers and page numbers -> RST comments (kept,
       not rendered), so nothing of the PDF is dropped
     * layout hyphens (U+2010) at line ends joined
   plain pdftotext prints the measures' numbers, titles and side figures in
   layout order, not reading order, which is why the order is rebuilt.
3. Verify against plain pdftotext: the RST text is exactly the PDF's
   paragraphs, each used once and whole (only their order may change); and
   pandoc reads the same words, without warnings.

Usage:  python3 salf-pdf2rst.py SALF_Contrato_Electoral_29N.pdf -o salf.rst [--report R]
        python3 salf-pdf2rst.py SALF_Contrato_Electoral_29N.pdf --verify-only salf.rst
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

ROMAN = re.compile(r"^[IVXL]+$")
SIDE_X = 300          # side figures and their captions start right of this
MAIN_X = 80           # measure titles and text start right of this


def is_heavy(b):
    return b.font.name.startswith(("Montserrat-Heavy", "Montserrat-Ultra"))


def rows(blocks, tol=8):
    """Blocks in reading order: rows by y (tolerance), each row left to right."""
    out, row = [], []
    for b in sorted(blocks, key=lambda b: (b.y0, b.x0)):
        if row and b.y0 > row[0].y0 + tol:
            out += sorted(row, key=lambda b: b.x0)
            row = []
        row.append(b)
    return out + sorted(row, key=lambda b: b.x0)


def para(b, emph=False):
    return C.wrap(C.esc_start(C.inline(b.chars(), emph=(lambda f: True) if emph else (lambda f: False)))) + [""]


def stack(blocks, gap=1.7):
    """Merge boxes whose lines the PDF split into separate blocks: same font,
    same column (centre or left edge), next line right below."""
    groups = []
    for b in rows(blocks, 3):
        for g in groups:
            last = g[-1]
            same_col = abs((b.x0 + b.x1) / 2 - (last.x0 + last.x1) / 2) < 12 or abs(b.x0 - last.x0) < 3
            if same_col and str(b.font) == str(last.font) and 0 <= b.y0 - last.y1 < gap * b.h \
                    and b.y0 - last.y0 < 2.2 * last.h * len(last.lines):
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def pairs(groups):
    """Big figure (or title) followed right below by a smaller caption in the
    same column -> one item. Figures side by side on one row ('22 →' '10')
    are joined first."""
    gs = [g[:] for g in groups]
    merged = True
    while merged:                          # side-by-side big figures
        merged = False
        for a in gs:
            for b in gs:
                if a is b or not is_heavy(a[0]) or not is_heavy(b[0]) or a[0].h < 20 or b[0].h < 20:
                    continue
                if 0 <= b[0].x0 - a[-1].x1 < 15 and abs(a[-1].y1 - b[-1].y1) < 8:
                    a += b
                    gs.remove(b)
                    merged = True
                    break
            if merged:
                break
    items, used = [], set()
    for i, g in enumerate(gs):
        if i in used:
            continue
        head = g
        cap = None
        if is_heavy(g[0]) and g[0].h >= 15:
            for j, k in enumerate(gs):
                if j == i or j in used or k[0].h >= g[0].h - 2:
                    continue
                short = len(" ".join(b.text for b in k)) < 120
                near = abs(k[0].x0 - g[0].x0) < 12 or abs(k[0].x1 - g[-1].x1) < 12
                if short and near and -8 <= k[0].y0 - max(x.y1 for x in g) < 30:
                    cap = k
                    used.add(j)
                    break
        used.add(i)
        items.append((head, cap))
    return items


def bullets(items):
    out = []
    for head, cap in items:
        txt = C.inline(C.chars_of(*head))
        if cap:
            txt += " " + C.inline(C.chars_of(*cap))
        out += C.wrap(txt, "  ", "* ")
    return out + [""]


def comment(blocks):
    return C.comment_blocks(rows(blocks))


# ------------------------------------------------------------------ pages
def page_cover(bs):
    by = rows(bs)
    title = next(b for b in by if "CONTRATO" in b.text)
    rest = [b for b in by if b is not title]
    logo = [b for b in rest if b.y0 < 120]
    other = [b for b in rest if b.y0 >= 120]
    out = C.heading(title.text, "#", over=True)
    out += ["| " + C.esc_inline(b.text) for b in other] + [""]
    out += ["| " + C.esc_inline(b.text) for b in logo] + [""]
    return out


def page_closing(bs):
    by = rows(bs)
    seal = [b for b in by if b.y0 > 740 and b.x0 > 480]
    text = [b for b in by if b not in seal]
    return ["| " + C.esc_inline(b.text) for b in text] + [""] + comment(seal)


def page_generic(bs, level="="):
    """Kicker + title + boxes, figures and paragraphs in reading order."""
    out = []
    kicker = [b for b in bs if b.y0 < 45 and b.font.name == "Montserrat" and b.h < 12]
    title = [b for b in bs if b.y0 < 70 and b.font.name == "Montserrat-Heavy" and b.h >= 20]
    seal = [b for b in bs if b.y0 < 100 and b.x0 > 480 and b not in title]
    for b in kicker:
        out += para(b, emph=True)
    for b in title:
        out += C.heading(b.text, level)
    out += comment(seal)
    body = [b for b in bs if b not in kicker + title + seal]
    out += render_flow(body)
    return out


def render_flow(bs):
    """Paragraphs stay paragraphs; boxes and figure+caption pairs become
    bullets; a «quote» with its attribution becomes a block quote."""
    out = []
    items = pairs(stack(bs))
    pending = []
    for head, cap in items:
        first = head[0]
        long_text = len(" ".join(b.text for b in head)) > 90 or (len(first.lines) >= 2 and not is_heavy(first))
        quote = first.text.startswith("«")
        label = is_heavy(first) and first.h < 10.5 and len(head) == 1 and first.text.upper() == first.text
        if quote:              # pull quote (+ attribution) -> block quote
            if pending:
                out += bullets(pending)
                pending = []
            out += ["..", ""] + C.wrap(C.esc_start(C.inline(C.chars_of(*head))), "   ") + [""]
            if cap:
                out += C.wrap(C.esc_start(C.inline(C.chars_of(*cap))), "   ") + [""]
            continue
        if (long_text or label) and not cap:
            if pending:
                out += bullets(pending)
                pending = []
            txt = C.esc_start(C.inline(C.chars_of(*head)))
            if quote:      # block quote; ".." ends a preceding list first
                out += ["..", ""] + C.wrap(txt, "   ") + [""]
            else:
                out += C.wrap(txt) + [""]
        else:
            pending.append((head, cap))
    if pending:
        out += bullets(pending)
    return out


def page_table3(bs):
    """10 COMPROMISOS: number | area | text rows, then the clause box."""
    out = []
    nums = [b for b in bs if b.font.name == "Montserrat-Heavy" and b.text.isdigit() and b.x0 < 80]
    head = [b for b in bs if b.y0 < 110 and b not in nums]
    out += page_generic(head)
    tab, used = [], set(nums)
    for n in sorted(nums, key=lambda b: b.y0):
        area = next(b for b in bs if abs(b.y0 - n.y0 - 5) < 8 and 80 < b.x0 < 200)
        text = next(b for b in bs if abs(b.y0 - n.y0 - 4) < 8 and b.x0 > 200)
        used |= {area, text}
        tab.append([C.esc_inline(n.text), C.inline(area.chars()), C.inline(text.chars())])
    out += C.list_table(tab, widths=[6, 22, 72])
    rest = [b for b in bs if b not in used and b not in head]
    out += render_flow(rest)
    return out


def page_howpaid(bs, runhead):
    out = comment(runhead)
    title = next(b for b in bs if b.text == "Cómo se paga")
    out += C.heading(title.text, "-")
    rest = [b for b in bs if b is not title]
    intro = [b for b in rest if b.y0 < 150]
    for b in intro:
        out += para(b)
    left = [b for b in rest if b.y0 >= 150 and b.x0 < 300]
    right = [b for b in rest if b.y0 >= 150 and b.x0 > 440]
    used = set()
    tables, cur = [], []
    for l in sorted(left, key=lambda b: b.y0):
        r = min(right, key=lambda b: abs((b.y0 + b.y1) / 2 - (l.y0 + l.y1) / 2))
        if abs((r.y0 + r.y1) / 2 - (l.y0 + l.y1) / 2) > 14 or r in used:
            continue
        used |= {l, r}
        row = [C.inline(l.chars()), C.inline(r.chars())]
        if cur and is_heavy(l) != is_heavy(cur[-1][2]):
            tables.append(cur)
            cur = []
        cur.append(row + [l])
    tables.append(cur)
    for t in tables:
        out += C.list_table([r[:2] for r in t], widths=[80, 20])
    rest = [b for b in rest if b not in intro and b not in used]
    out += render_flow(rest)
    return out


def page_chapter(bs, runhead, state):
    out = comment(runhead)
    eyebrow = [b for b in bs if b.y0 < 45 and ROMAN.match(b.text) and is_heavy(b)]
    title = [b for b in bs if b.y0 < 80 and b.font.name == "Montserrat-Heavy" and b.h >= 30]
    if title:
        tag = [b for b in bs if 85 < b.y0 < 140 and is_heavy(b) and b.font.color == "#d2a95a"]
        name = " ".join(b.text for b in eyebrow + title)
        out += C.heading(name, "=")
        for b in tag:
            out += para(b)
        bs = [b for b in bs if b not in eyebrow + title + tag]
    nums = sorted((b for b in bs if b.x0 < MAIN_X and b.text.isdigit() and is_heavy(b) and b.h < 20),
                  key=lambda b: b.y0)
    top = nums[0].y0 - 25 if nums else 900
    strip = [b for b in bs if b.y0 < top and b not in nums]
    if strip:
        out += render_flow(strip)
    # where the last measure ends: a pull quote or a callout box
    tail_y = min([b.y0 for b in bs if b.y0 > top and (b.text.startswith("«") and b.h > 18
                                                       or b.font.name == "Montserrat" and b.x0 < MAIN_X
                                                       and b.text.isupper() and b.y0 > (nums[-1].y0 if nums else 0))]
                 or [900])
    done = set(nums) | set(strip)
    for k, n in enumerate(nums):
        y_end = nums[k + 1].y0 - 25 if k + 1 < len(nums) else tail_y
        band = [b for b in bs if n.y0 - 25 <= b.y0 < y_end and b not in done]
        main = sorted((b for b in band if MAIN_X <= b.x0 < SIDE_X), key=lambda b: b.y0)
        side = [b for b in band if b.x0 >= SIDE_X]
        other = [b for b in band if b.x0 < MAIN_X]
        done |= set(band)
        titles = [b for b in main if is_heavy(b) and b.h >= 15]
        if not titles:
            raise C.ConvertError(f"p{n.page}: measure {n.text} without a title")
        out += C.heading(f"{n.text} {' '.join(b.text for b in titles)}", "-")
        state["measures"] += 1
        for b in main:
            if b in titles:
                continue
            note = b.font.color == "#6e6a60"
            out += para(b, emph=note)
        if side:
            out += render_flow(side)
        if other:
            out += render_flow(other)
    rest = [b for b in bs if b not in done]
    if rest:
        out += render_flow(rest)
    return out


# ------------------------------------------------------------------ main
def convert(pdf, report):
    blocks = C.extract(pdf)
    pages = {}
    for b in blocks:
        pages.setdefault(b.page, []).append(b)
    last = max(pages)
    out, state = [], {"measures": 0}
    for p in sorted(pages):
        bs = pages[p]
        footer = [b for b in bs if b.y0 > 800]
        runhead = [b for b in bs if b.y0 < 34 and p > 4 and not (ROMAN.match(b.text) and b.font.color == "#d2a95a")]
        body = [b for b in bs if b not in footer and b not in runhead]
        texts = " ".join(b.text for b in body)
        if p == 1:
            out += page_cover(body)
        elif p == last:
            out += page_closing(body)
        elif "10 COMPROMISOS" in texts:
            out += page_table3(body)
        elif any(b.text == "Cómo se paga" for b in body):
            out += page_howpaid(body, runhead)
        elif "EL GOBIERNO" in texts[:60]:
            out += page_generic(body)
        else:
            out += page_chapter(body, runhead, state)
        out += comment(footer)
    while out and out[-1] == "":
        out.pop()
    report.append(f"pages: {len(pages)}; blocks: {len(blocks)}; measures: {state['measures']}")
    return "\n".join(out) + "\n"


def verify(rst, raw, report):
    return C.verify_blocks(rst, raw, report)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the SALF electoral contract"))
