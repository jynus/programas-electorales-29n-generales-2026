#!/usr/bin/env python3
"""
pdf2rst.py - Convert the BNG "Programa electoral" PDF (and PDFs built from the
same template) to reStructuredText, repeatably, and verify that the text was
not altered, omitted or added.

Pipeline
--------
1. Extraction (poppler-utils):
     * ``pdftotext -bbox-layout``  -> every text line with its geometry
     * ``pdftotext`` (plain)       -> independent reference text for step 3
     * ``pdftoppm -gray -r 72``    -> page bitmaps, used only to detect the
                                      bullet dots (they are vector graphics,
                                      so they are not part of the text layer)
2. Clean-up + RST generation, driven by geometry:
     * font height  -> document title / subtitle / chapter / section heading /
                       table of contents / running footer / body text
     * x position   -> prose paragraph vs. list item vs. nested list item
     * ink left of a line start -> a bullet starts here
     * vertical gaps -> paragraph / item boundaries
     * running page footers ("SECTION · 12") are removed
     * lines are re-flowed into paragraphs; headings split over several
       lines are joined
3. Verification: the RST is reduced back to plain words (only the markup
   this script emits is stripped) and compared word-by-word, in order,
   against the plain ``pdftotext`` output minus the running footers.
   Any difference is reported and the exit status is 1.

Usage
-----
    python3 pdf2rst.py INPUT.pdf [-o OUTPUT.rst] [--report REPORT.txt]

Only the Python standard library and poppler-utils are required. If docutils
happens to be installed, the RST is additionally parsed with it and any
docutils warning is reported.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Layout constants (points). Tuned for this document's template; anything
# that does not fit a known category aborts the run instead of being dropped.
# ---------------------------------------------------------------------------
H_TITLE_MIN = 100.0          # "PROGRAMA" / "ELECTORAL" on the cover (146.4)
H_SUBTITLE = (20.0, 40.0)    # "29N. XERAIS 2026" (25.9)
H_CHAPTER = (12.0, 16.0)     # "1. MÁIS FORZA PARA GALIZA" (13.8)
H_BODY = (8.0, 10.5)         # body text (9.5; 8.7 when a line has bold)
H_SECTION = (6.80, 7.02)     # "2.1 VIVENDA", also the "1." list numbers (6.9)
H_TOC_SUB = (7.05, 7.30)     # table of contents sub-entries (7.1)
H_FOOTER = (5.5, 6.2)        # "DEREITO A UNHA VIDA DIGNA · 11" (5.8)
H_TOC_MAIN = (5.0, 5.5)      # table of contents main entries (5.2)

FOOTER_MIN_Y = 760.0         # footers sit at y≈777 on an 842pt page
LEFT_LABEL_MAX_X = 120.0     # "Páxina 04." labels in the left column (x≈65)
X_TOL = 2.0                  # same-column tolerance
PARA_GAP = 18.0              # line pitch is 14.4; item/paragraph gaps ≥ 21.4
MARKER_Y_TOL = 4.0           # "1." number vs. its item's first line
BULLET_PROBE = (14.0, 4.0)   # look for ink from x-14 to x-4 (pt) for a bullet
INK_THRESHOLD = 200          # gray level (0-255) counted as ink
WORD_GLUE_GAP = 0.8          # word pieces closer than this are one word
WRAP = 79

TERMINAL_PUNCT = tuple(".:;!?…)")
FOOTER_RE = re.compile(r"^\S.* · \d{1,3}$")
TOC_LABEL_RE = re.compile(r"^Páxina \d+\.$")
ENUM_MARKER_RE = re.compile(r"^\d{1,2}\.$")
# Starts of TOC entries / heading numbering
TOC_ENTRY_RE = re.compile(r"^(\d+(\.\d+)*\s?\.?\s|[a-z]\)\s|\d+\)\s)")
SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")
PAREN_NUM_RE = re.compile(r"^\d+\)\s")

# RST adornment characters, by level. Title and subtitle get over+underline.
ADORN_TITLE, ADORN_SUBTITLE = "#", "*"
ADORN_LEVELS = {1: "=", 2: "-", 3: "~", 4: "^"}


class ConversionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Step 1: extraction
# ---------------------------------------------------------------------------
@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    kind: str = ""          # assigned by classify()
    bullet: bool = False    # ink immediately left of the line start
    marker: str = ""        # "1." etc. attached from a separate marker line

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def run(cmd: list[str]) -> bytes:
    try:
        return subprocess.run(cmd, check=True, capture_output=True).stdout
    except FileNotFoundError:
        raise ConversionError(f"{cmd[0]} not found: install poppler-utils")
    except subprocess.CalledProcessError as e:
        raise ConversionError(f"{' '.join(cmd)} failed: {e.stderr.decode(errors='replace')}")


def extract_lines(pdf: str) -> tuple[list[Line], list[tuple[float, float]]]:
    xhtml = run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", pdf, "-"]).decode("utf-8")
    page_re = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
    line_re = re.compile(
        r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
    word_re = re.compile(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>', re.S)
    lines, sizes = [], []
    for pno, pm in enumerate(page_re.finditer(xhtml), 1):
        sizes.append((float(pm.group(1)), float(pm.group(2))))
        for lm in line_re.finditer(pm.group(3)):
            # A word whose font changes mid-way (bold "alimentación" + regular ",")
            # comes out as two <word> pieces with no gap: glue those back together.
            text, prev_x1 = "", None
            for wx0, wx1, w in word_re.findall(lm.group(5)):
                w = html.unescape(w)
                if prev_x1 is not None:
                    text += "" if float(wx0) - prev_x1 < WORD_GLUE_GAP else " "
                text += w
                prev_x1 = float(wx1)
            x0, y0, x1, y1 = map(float, lm.groups()[:4])
            lines.append(Line(pno, x0, y0, x1, y1, text))
    if not lines:
        raise ConversionError("pdftotext returned no text (scanned PDF?)")
    return lines, sizes


def extract_reference(pdf: str) -> str:
    return run(["pdftotext", "-enc", "UTF-8", pdf, "-"]).decode("utf-8")


class PageBitmaps:
    """Gray 72-dpi renderings (1 px = 1 pt) used to look for bullet dots."""

    def __init__(self, pdf: str, enabled: bool = True):
        self.pages: dict[int, tuple[int, int, bytes]] = {}
        self.enabled = enabled and shutil.which("pdftoppm") is not None
        if not self.enabled:
            return
        self.tmp = tempfile.mkdtemp(prefix="pdf2rst-")
        run(["pdftoppm", "-gray", "-r", "72", pdf, os.path.join(self.tmp, "p")])
        for fn in os.listdir(self.tmp):
            m = re.match(r"p-0*(\d+)\.pgm$", fn)
            if m:
                self.pages[int(m.group(1))] = self._load_pgm(os.path.join(self.tmp, fn))
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _load_pgm(path: str) -> tuple[int, int, bytes]:
        data = open(path, "rb").read()
        tokens, i = [], 0
        while len(tokens) < 4:
            while data[i:i + 1].isspace():
                i += 1
            if data[i:i + 1] == b"#":
                while data[i:i + 1] not in (b"\n", b""):
                    i += 1
                continue
            j = i
            while not data[j:j + 1].isspace():
                j += 1
            tokens.append(data[i:j])
            i = j
        if tokens[0] != b"P5" or int(tokens[3]) > 255:
            raise ConversionError(f"unexpected bitmap format in {path}")
        w, h = int(tokens[1]), int(tokens[2])
        return w, h, data[i + 1:i + 1 + w * h]

    def has_ink_left_of(self, ln: Line, scale: float) -> bool:
        if ln.page not in self.pages:
            return False
        w, h, px = self.pages[ln.page]
        xa = int((ln.x0 - BULLET_PROBE[0]) * scale)
        xb = int((ln.x0 - BULLET_PROBE[1]) * scale)
        ya, yb = int(ln.y0 * scale), int(ln.y1 * scale)
        for y in range(max(ya, 0), min(yb, h)):
            row = y * w
            for x in range(max(xa, 0), min(xb, w)):
                if px[row + x] < INK_THRESHOLD:
                    return True
        return False


# ---------------------------------------------------------------------------
# Step 2a: classification
# ---------------------------------------------------------------------------
def within(v: float, rng: tuple[float, float]) -> bool:
    return rng[0] <= v <= rng[1]


def classify(lines: list[Line]) -> dict:
    toc_pages = {l.page for l in lines if l.x0 < LEFT_LABEL_MAX_X and TOC_LABEL_RE.match(l.text)}
    stats = collections.Counter()
    for l in lines:
        h = l.h
        if h >= H_TITLE_MIN:
            l.kind = "title"
        elif within(h, H_SUBTITLE):
            l.kind = "subtitle"
        elif l.page in toc_pages:
            if l.x0 < LEFT_LABEL_MAX_X and TOC_LABEL_RE.match(l.text):
                l.kind = "toc_label"
            elif within(h, H_TOC_MAIN):
                l.kind = "toc_main"
            elif within(h, H_TOC_SUB):
                l.kind = "toc_sub"
            else:
                raise ConversionError(f"unclassified TOC line p{l.page}: {l.text!r} h={h:.2f}")
        elif within(h, H_FOOTER) and l.y0 >= FOOTER_MIN_Y and FOOTER_RE.match(l.text):
            l.kind = "footer"
        elif within(h, H_CHAPTER):
            l.kind = "chapter"
        elif within(h, H_SECTION) and ENUM_MARKER_RE.match(l.text):
            l.kind = "enum_marker"
        elif within(h, H_SECTION):
            l.kind = "section"
        elif within(h, H_BODY):
            l.kind = "body"
        else:
            raise ConversionError(f"unclassified line p{l.page}: {l.text!r} h={h:.2f} x={l.x0:.1f}")
        stats[l.kind] += 1
    return {"toc_pages": sorted(toc_pages), "kinds": stats}


def attach_markers(lines: list[Line]) -> list[Line]:
    """Fold "1." marker lines into the body line they number."""
    out = []
    for i, l in enumerate(lines):
        if l.kind != "enum_marker":
            continue
        cands = [b for b in lines if b.kind == "body" and b.page == l.page
                 and b.x0 > l.x1 and abs(b.y0 - l.y0) <= MARKER_Y_TOL]
        if len(cands) != 1:
            raise ConversionError(f"cannot attach list number {l.text!r} on p{l.page}")
        cands[0].marker = l.text
    return [l for l in lines if l.kind != "enum_marker"]


# ---------------------------------------------------------------------------
# Step 2b: structure
# ---------------------------------------------------------------------------
@dataclass
class Block:
    kind: str                       # title, subtitle, toc, heading, para
    x: float = 0.0
    lines: list[Line] = field(default_factory=list)
    item: str = ""                  # "" (paragraph), "bullet", or the "1." marker
    level: int = 0                  # heading level
    toc: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(l.text for l in self.lines)


def build_toc(toc_lines: list[Line]) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    labels = [l for l in toc_lines if l.kind == "toc_label"]
    for l in toc_lines:
        if l.kind == "toc_label":
            continue
        if l.kind == "toc_main":
            lab = [b for b in labels if b.page == l.page and abs(b.y0 - l.y0) < 1.0]
            if len(lab) != 1:
                raise ConversionError(f"TOC entry without page label: {l.text!r}")
            entries.append((f"{lab[0].text} {l.text}", []))
        else:  # toc_sub
            if not entries:
                raise ConversionError(f"TOC sub-entry before any entry: {l.text!r}")
            subs = entries[-1][1]
            if subs and not TOC_ENTRY_RE.match(l.text):
                subs[-1] += " " + l.text          # wrapped entry
            else:
                subs.append(l.text)
    # Labels must have been consumed one-to-one
    if len(labels) != len(entries):
        raise ConversionError("TOC labels and entries do not pair up")
    return entries


def build_blocks(lines: list[Line], bitmaps: PageBitmaps, page_sizes, report: list[str]) -> list[Block]:
    # Prose margin = leftmost x used by a sizeable share of body lines (the
    # bullet column is actually more common than the prose one in this PDF).
    xs = collections.Counter(round(l.x0) for l in lines if l.kind == "body")
    total = sum(xs.values())
    body_x = min(x for x, n in xs.items() if n >= 0.05 * total)
    blocks: list[Block] = []
    toc_buf: list[Line] = []
    prev: Line | None = None
    cur: Block | None = None
    crossings = []

    def flush_toc():
        nonlocal toc_buf
        if toc_buf:
            blocks.append(Block("toc", toc=build_toc(toc_buf)))
            toc_buf = []

    for l in lines:
        if l.kind == "footer":
            continue
        if l.kind in ("toc_label", "toc_main", "toc_sub"):
            toc_buf.append(l)
            cur, prev = None, None
            continue
        flush_toc()

        if l.kind in ("title", "subtitle", "chapter", "section"):
            if cur and cur.kind == l.kind and prev and prev.page == l.page \
                    and l.y0 - prev.y0 < 2.5 * l.h:
                cur.lines.append(l)            # heading split over lines
            else:
                cur = Block(l.kind, x=l.x0, lines=[l])
                blocks.append(cur)
            prev = l
            continue

        # body line
        if bitmaps.enabled:
            l.bullet = bitmaps.has_ink_left_of(l, 1.0) and not l.marker
        else:
            # Fallback: an indented line after a gap starts an item, unless it
            # reads like "a) ..." (lettered paragraphs inside an item).
            if prev is None:
                gap_ok = True
            elif prev.page == l.page:
                gap_ok = (l.y0 - prev.y0) > PARA_GAP
            else:  # same page-break rule as below
                gap_ok = prev.text.endswith(TERMINAL_PUNCT) and not l.text[:1].islower()
            l.bullet = (abs(l.x0 - body_x) > X_TOL and not l.marker and gap_ok
                        and not re.match(r"^[a-z]\) ", l.text))
        starts_item = l.bullet or bool(l.marker)
        new_block = True
        if cur is not None and cur.kind == "para" and prev is not None and not starts_item \
                and abs(prev.x0 - l.x0) <= X_TOL:
            if prev.page == l.page:
                new_block = (l.y0 - prev.y0) > PARA_GAP
            else:
                ends_terminal = prev.text.endswith(TERMINAL_PUNCT)
                starts_lower = l.text[:1].islower()
                new_block = ends_terminal and not starts_lower
                crossings.append((prev.page, l.page, "new paragraph" if new_block else "continues",
                                  prev.text[-40:], l.text[:40]))
        if new_block:
            item = l.marker if l.marker else ("bullet" if l.bullet else "")
            cur = Block("para", x=l.x0, lines=[l], item=item)
            blocks.append(cur)
        else:
            cur.lines.append(l)
        prev = l
    flush_toc()

    report.append("Page-break decisions (paragraph continues across the page break?):")
    for a, b, d, t1, t2 in crossings:
        report.append(f"  p{a}->p{b}: {d:13s} …{t1!r} | {t2!r}…")
    return blocks


def assign_heading_levels(blocks: list[Block]) -> None:
    last = 0
    in_numbered_l2 = False
    for b in blocks:
        if b.kind == "chapter":
            b.level, in_numbered_l2 = 1, False
        elif b.kind == "section":
            t = b.text
            m = SECTION_NUM_RE.match(t)
            if m:
                depth = len(m.group(1).split("."))
                b.level = max(2, depth)
                if b.level == 2:
                    in_numbered_l2 = True
            elif PAREN_NUM_RE.match(t):
                b.level = 2
            else:
                b.level = 3 if in_numbered_l2 else 2
            if b.level > last + 1:
                b.level = last + 1          # never skip a level
        else:
            continue
        last = b.level


# ---------------------------------------------------------------------------
# Step 2c: RST emission
# ---------------------------------------------------------------------------
_ENUMERATOR = re.compile(r"^(\(?)(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+|#)([.)])(\s|$)")
_OTHER_CONSTRUCT = re.compile(r"^(?:[-+*•‣⁃](?:\s|$)|\.\.(?:\s|$)|:|>>>)")


def esc_inline(text: str) -> str:
    """Escape characters that would start inline markup."""
    return re.sub(r"([\\*`|_\[\]])", r"\\\1", text)


def esc(text: str) -> str:
    """Escape inline markup and any start that RST would read as a list,
    comment, field or doctest block (e.g. the paragraph "1) En materia...")."""
    m = _ENUMERATOR.match(text)
    if m:
        pos = 0 if m.group(1) else m.start(2)      # "\(a) ..." or "1\) ..."
        return esc_inline(text[:pos]) + "\\" + esc_inline(text[pos:])
    if _OTHER_CONSTRUCT.match(text):
        return "\\" + esc_inline(text) if text[0] not in "\\*`|_[]" else esc_inline(text)
    return esc_inline(text)


def wrap(text: str, indent: str, first: str = "") -> list[str]:
    first = first or indent
    out = textwrap.wrap(text, width=WRAP, initial_indent=first, subsequent_indent=indent,
                        break_long_words=False, break_on_hyphens=False)
    # A continuation line made only of punctuation could read as an adornment.
    if any(re.fullmatch(r"\s*\W+", ln) for ln in out[1:]):
        out = [first + text]
    return out


def heading(text: str, ch: str, over: bool = False) -> list[str]:
    bar = ch * len(text)
    return ([bar] if over else []) + [text, bar, ""]


def emit(blocks: list[Block]) -> str:
    out: list[str] = []
    stack: list[tuple[float, int]] = []  # (text x, content indent) of open list items

    for b in blocks:
        if b.kind in ("title", "subtitle", "chapter", "section", "toc"):
            stack = []
        if b.kind == "title":
            out += heading(esc_inline(b.text), ADORN_TITLE, over=True)
        elif b.kind == "subtitle":
            out += heading(esc_inline(b.text), ADORN_SUBTITLE, over=True)
        elif b.kind in ("chapter", "section"):
            out += heading(esc_inline(b.text), ADORN_LEVELS[b.level])
        elif b.kind == "toc":
            for main, subs in b.toc:
                out += wrap(esc(main), "  ", "* ") + [""]
                for s in subs:
                    out += wrap(esc(s), "    ", "  * ") + [""]
        elif b.kind == "para":
            text = esc(b.text)
            if b.item:
                while stack and stack[-1][0] >= b.x - X_TOL:
                    stack.pop()
                base = stack[-1][1] if stack else 0
                marker = "* " if b.item == "bullet" else b.item + " "
                content = base + len(marker)
                stack.append((b.x, content))
                out += wrap(text, " " * content, " " * base + marker)
            else:
                while stack and stack[-1][0] > b.x + X_TOL:
                    stack.pop()
                indent = stack[-1][1] if stack else 0
                out += wrap(text, " " * indent)
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def convert(pdf: str, use_bitmaps: bool = True) -> tuple[str, str, list[str]]:
    report: list[str] = []
    lines, sizes = extract_lines(pdf)
    info = classify(lines)
    lines = attach_markers(lines)
    report.append(f"Lines by kind: {dict(sorted(info['kinds'].items()))}")
    report.append(f"Table-of-contents pages: {info['toc_pages']}")
    bitmaps = PageBitmaps(pdf, use_bitmaps)
    report.append("Bullet detection: " + ("page bitmaps (pdftoppm)" if bitmaps.enabled
                                          else "geometry fallback (pdftoppm not used)"))
    blocks = build_blocks(lines, bitmaps, sizes, report)
    assign_heading_levels(blocks)
    counts = collections.Counter(b.kind if b.kind != "para" else ("item" if b.item else "para")
                                 for b in blocks)
    report.insert(2, f"Blocks emitted: {dict(sorted(counts.items()))}")
    return emit(blocks), extract_reference(pdf), report


# ---------------------------------------------------------------------------
# Step 3: verification (independent of the generator's internal data)
# ---------------------------------------------------------------------------
ADORN_LINE = re.compile(r"^([#*=\-~^])\1+$")
BULLET_START = re.compile(r"^\s*\* ")


def rst_to_words(rst: str) -> list[str]:
    """Undo exactly the markup pdf2rst emits: adornments, '* ' bullets,
    indentation and backslash escapes. Everything else is content."""
    words = []
    for ln in rst.splitlines():
        if ADORN_LINE.match(ln):
            continue
        ln = BULLET_START.sub("", ln, count=1)
        ln = re.sub(r"\\(.)", r"\1", ln)
        words += ln.split()
    return words


def reference_words(ref: str) -> tuple[list[str], list[str]]:
    """Plain pdftotext output minus the running footer that ends each page."""
    words, removed = [], []
    for page in ref.split("\f"):
        plines = page.split("\n")
        nonempty = [i for i, t in enumerate(plines) if t.strip()]
        if nonempty and FOOTER_RE.match(plines[nonempty[-1]].strip()):
            removed.append(plines[nonempty[-1]].strip())
            del plines[nonempty[-1]]
        for t in plines:
            words += t.split()
    return words, removed


def pandoc_words(rst: str) -> list[str]:
    """Words of the document as pandoc's RST parser understands it. List
    numbers are not text in the AST, so they are rebuilt from the list
    attributes (start number + delimiter), as a reader would see them."""
    import json
    proc = subprocess.run(["pandoc", "-f", "rst", "-t", "json"], input=rst.encode("utf-8"),
                          capture_output=True)
    if proc.returncode:
        raise ConversionError(proc.stderr.decode(errors="replace"))
    doc = json.loads(proc.stdout)
    buf: list[str] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t, c = node.get("t"), node.get("c")
            if t == "Str":
                buf.append(c)
            elif t in ("Space", "SoftBreak", "LineBreak"):
                buf.append(" ")
            elif t == "Header":
                walk(c[2]); buf.append(" ")
            elif t == "OrderedList":
                (start, _style, delim), items = c
                d = {"Period": ".", "OneParen": ")", "TwoParens": ")"}.get(delim["t"], ".")
                for k, item in enumerate(items):
                    buf.append(f" {'(' if delim['t'] == 'TwoParens' else ''}{start + k}{d} ")
                    walk(item)
            elif t in ("MetaInlines", "MetaString"):
                walk(c) if t == "MetaInlines" else buf.append(c)
                buf.append(" ")
            elif c is not None:
                walk(c)
                if t in ("Para", "Plain"):
                    buf.append(" ")

    meta = doc.get("meta", {})
    for key in ("title", "subtitle"):
        if key in meta:
            walk(meta[key])
    walk(doc["blocks"])
    return "".join(buf).split()


def verify(rst: str, ref: str) -> tuple[bool, list[str]]:
    msgs = []
    out_w = rst_to_words(rst)
    ref_w, footers = reference_words(ref)
    msgs.append(f"Reference (plain pdftotext): {len(ref_w)} words after removing "
                f"{len(footers)} running page footers")
    msgs.append(f"RST content:                 {len(out_w)} words")
    ok = True
    sm = difflib.SequenceMatcher(a=ref_w, b=out_w, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ok = False
        ctx = " ".join(ref_w[max(0, i1 - 6):i1])
        msgs.append(f"  {tag.upper()} after …{ctx!r}: PDF {ref_w[i1:i2]!r} -> RST {out_w[j1:j2]!r}")
    if ok:
        msgs.append("Word sequence: IDENTICAL (nothing altered, omitted, added or reordered)")
    # Character-level check, whitespace-insensitive
    if "".join(ref_w) != "".join(out_w):
        ok = False
        msgs.append("Character stream: DIFFERENT")
    else:
        msgs.append(f"Character stream: IDENTICAL ({len(''.join(ref_w))} non-space characters)")
    # The only things removed must be footers
    bad = [f for f in footers if not FOOTER_RE.match(f)]
    if bad:
        ok = False
        msgs.append(f"Unexpected removed lines: {bad!r}")
    msgs.append("Removed footers: " + ", ".join(sorted(set(re.sub(r" · \d+$", "", f) for f in footers))))

    # Optional: independent parse with pandoc's RST reader
    if shutil.which("pandoc"):
        try:
            pw = pandoc_words(rst)
            sm = difflib.SequenceMatcher(a=ref_w, b=pw, autojunk=False)
            diffs = [op for op in sm.get_opcodes() if op[0] != "equal"]
            if diffs:
                ok = False
                msgs.append(f"pandoc parse: {len(diffs)} difference(s) vs. PDF text:")
                for tag, i1, i2, j1, j2 in diffs[:50]:
                    msgs.append(f"  {tag.upper()}: PDF {ref_w[i1:i2]!r} -> pandoc {pw[j1:j2]!r}")
            else:
                msgs.append(f"pandoc parse: IDENTICAL word sequence ({len(pw)} words) - "
                            "the RST means exactly the PDF text")
        except (ConversionError, ValueError) as e:
            ok = False
            msgs.append(f"pandoc parse failed: {e}")
    else:
        msgs.append("pandoc: not installed, independent parse skipped")

    # Optional: structural check with docutils
    try:
        import docutils.core  # type: ignore
        import docutils.utils  # type: ignore
        import io
        warn = io.StringIO()
        docutils.core.publish_doctree(rst, settings_overrides={
            "warning_stream": warn, "report_level": 2, "halt_level": 5})
        w = warn.getvalue().strip()
        if w:
            ok = False
            msgs.append("docutils warnings:\n" + w)
        else:
            msgs.append("docutils: parsed without warnings")
    except ImportError:
        msgs.append("docutils: not installed, structural parse skipped")
    return ok, msgs


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", help="output .rst (default: input name with .rst)")
    ap.add_argument("--report", help="also write the report to this file")
    ap.add_argument("--no-bitmaps", action="store_true",
                    help="do not render pages to detect bullets (geometry-only fallback)")
    args = ap.parse_args(argv)
    out = args.output or os.path.splitext(os.path.basename(args.pdf))[0] + ".rst"
    try:
        rst, ref, report = convert(args.pdf, use_bitmaps=not args.no_bitmaps)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(rst)
    ok, vmsgs = verify(rst, ref)
    text = "\n".join([f"Input:  {args.pdf}", f"Output: {out}", ""] + report +
                     ["", "VERIFICATION", "------------"] + vmsgs +
                     ["", "RESULT: " + ("PASS" if ok else "FAIL")]) + "\n"
    sys.stdout.write(text)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
