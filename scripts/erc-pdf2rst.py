#!/usr/bin/env python3
"""
erc-pdf2rst.py - Convert the ERC programme "Eleccions al Congrés 2026 -
Programa electoral" (PDF) to reStructuredText, repeatably, and verify that
no text was altered, omitted or added.

Pipeline
--------
1. Extract (poppler-utils)
     * ``pdftotext -bbox-layout``  every word with its position: the source of
                                   the text that goes into the RST
     * ``pdftotext``               plain text: the independent reference for
                                   the verification in step 3
     * ``pdftohtml -xml``          font of every text run: only used to know
                                   which words are bold (the proposals' lead-in)
2. Clean up and convert, driven by geometry
     * running header (ESQUERRA REPUBLICANA / Eleccions al Congrés 2026 |
       Programa electoral / page number) removed
     * cover -> title, subtitle and a line block with the slogan; the inner
       title page is kept as an RST comment
     * ÍNDEX -> a table (entry, page) without the dot leaders
     * part titles (30.6 pt) -> level 1, section titles (18.8 pt) -> level 2
     * prose paragraphs re-flowed; layout hyphens at line ends joined
     * numbered proposals -> RST enumerated list (numbers kept as printed),
       bold lead-ins kept as **bold**, "–" sub-items and tab-indented
       paragraphs kept inside their proposal
3. Verify against the plain pdftotext output
     * word sequence identical, after setting aside only what step 2 removes
       (running headers, page numbers, TOC dot leaders); line-end hyphens may
       be joined; proposal numbers compared as their own sequence (plain
       pdftotext prints them in a column before the proposals)
     * character inventory identical
     * pandoc parses the RST to exactly the same words (no text swallowed or
       produced by markup), without warnings, with the expected headings
     * proposals numbered 1..N without gaps; headings match the ÍNDEX

Usage
-----
    python3 erc-pdf2rst.py e2026-programa.pdf -o erc.rst [--report REPORT.txt]
    python3 erc-pdf2rst.py e2026-programa.pdf --verify-only erc.rst

Exit status: 0 verified, 1 verification failed, 2 extraction/structure error.
"""

from __future__ import annotations

import collections
import difflib
import html
import os
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

# ------------------------------------------------------------------ layout (pt)
MARGIN_ODD, MARGIN_EVEN = 85.0, 62.0     # left margin of odd / even pages
OFF_ITEM, OFF_SUB, OFF_SUB_CONT = 23.0, 37.0, 51.0
X_TOL = 3.5
H_SLOGAN_MIN = 100.0                     # PEL PAÍS. / PER LA GENT. / PEL FUTUR.
H_PART = (29.0, 32.0)                    # A. DEFENSAR EL PAÍS (30.6), cover title (29.2/31.1)
H_SECTION = (18.0, 19.5)                 # A. Autodeterminació, democràcia i drets (18.8)
H_HALFTITLE = (14.0, 15.5)               # inner title page / TOC part entries (14.6-14.9)
H_BODY = (11.0, 13.0)                    # body (11.5; 11.8-12.5 with bold)
H_HEADER = (7.5, 9.0)                    # running header (8.2/8.5)
HEADER_MAX_Y = 50.0
PARA_GAP = 18.0                          # line pitch 12, paragraph gap 24
WORD_GLUE_GAP = 0.8
WRAP = 79

HEADER_RE = re.compile(r"^(?:ESQUERRA REPUBLICANA|(?:\d{1,2} )?Eleccions al Congrés 2026 \| "
                       r"Programa electoral(?: \d{1,2})?|\d{1,2})$")
MARKER_RE = re.compile(r"^(\d{1,3})\.$")
INLINE_ITEM_RE = re.compile(r"^(\d{1,3})\. (?=\S)")
LEADER_RE = re.compile(r"\.{3,}")
TOC_ENTRY_RE = re.compile(r"^(.*?)\s*\.{3,}\s*(\d{1,3})$")
TERMINAL = tuple(".:;!?…)»”")


StructureError = C.ConvertError


# ================================================================ 1. extract
@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    bold: list[bool] = field(default_factory=list)   # one flag per character


@dataclass
class Line:
    page: int
    words: list[Word]
    tab: bool = False          # paragraph indented with tab characters
    kind: str = ""
    marker: str = ""

    @property
    def x0(self): return self.words[0].x0
    @property
    def x1(self): return self.words[-1].x1
    @property
    def y0(self): return min(w.y0 for w in self.words)
    @property
    def y1(self): return max(w.y1 for w in self.words)
    @property
    def h(self): return self.y1 - self.y0

    @property
    def text(self):
        return join_words(self.words)


def join_words(words):
    out, prev = "", None
    for w in words:
        if prev is not None:
            out += "" if w.x0 - prev.x1 < WORD_GLUE_GAP else " "
        out += w.text
        prev = w
    return out


def extract_lines(pdf: str) -> list[Line]:
    lines = []
    for pno, page in enumerate(C.read_bbox(pdf), 1):
        for bl in (bl for b in page.blocks for bl in b.lines):
            words, tab = [], False
            for x0, y0, x1, y1, t in bl.words:
                if t.strip() == "":          # tab "words" used as a first-line indent
                    tab = tab or not words
                    continue
                words.append(Word(x0, y0, x1, y1, t))
            if words:
                lines.append(Line(pno, words, tab))
    if not lines:
        raise StructureError("no text in the PDF")
    return lines


def apply_bold(pdf: str, lines: list[Line]) -> None:
    """Mark the characters of every word that pdftohtml reports in a bold
    (Medium/Bold) font. Runs are matched to words by position on the page."""
    xml = C.run(["pdftohtml", "-xml", "-i", "-zoom", "1", "-stdout", pdf])
    fonts = {m.group(1): m.group(2) for m in
             re.finditer(r'<fontspec id="(\d+)" size="-?\d+" family="([^"]+)"', xml)}
    runs = collections.defaultdict(list)
    for pm in re.finditer(r'<page number="(\d+)".*?</page>', xml, re.S):
        p = int(pm.group(1))
        for t in re.finditer(r'<text top="(-?\d+)" left="(-?\d+)" width="(\d+)" height="(\d+)" '
                             r'font="(\d+)">(.*?)</text>', pm.group(0), re.S):
            top, left, width, _h, f, txt = t.groups()
            txt = html.unescape(re.sub(r"<[^>]+>", "", txt))
            bold = bool(re.search(r"Medium|Bold", fonts[f]))
            runs[p].append((int(top), int(left), int(left) + int(width), bold, txt))
    # pdftohtml sometimes merges "61." with the first words of the proposal into
    # one run carrying the number's (regular) font: those words take the font
    # of the next run on the same line.
    for p, rs in runs.items():
        rs.sort(key=lambda r: (r[0], r[1]))
        for i, r in enumerate(rs):
            m = re.match(r"^\s*\d{1,3}\.\s+(?=\S)", r[4])
            if m and not r[3]:
                nxt = next((q for q in rs[i + 1:] if abs(q[0] - r[0]) <= 2 and q[1] >= r[2] - 1), None)
                if nxt and nxt[3]:
                    # split: number regular, remainder bold
                    num_w = r[2] - r[1]
                    cut = r[1] + int(num_w * len(m.group(0)) / max(len(r[4]), 1))
                    rs[i] = (r[0], r[1], cut, False, r[4][:m.end()])
                    rs.insert(i + 1, (r[0], cut, r[2], True, r[4][m.end():]))
    for ln in lines:
        for w in ln.words:
            over = sorted((r for r in runs[ln.page]
                           if abs(r[0] - w.y0) <= 3 and r[1] <= w.x1 - 0.5 and r[2] >= w.x0 + 0.5),
                          key=lambda r: r[1])
            flags = {r[3] for r in over}
            if len(flags) <= 1:
                w.bold = [bool(flags and flags.pop())] * len(w.text)
                continue
            # font changes inside the word: align the word with the runs' text
            stream, sflags = "", []
            for r in over:
                s = r[4].replace(" ", "")
                stream += s
                sflags += [r[3]] * len(s)
            k = stream.find(w.text)
            if k < 0:
                raise StructureError(f"p{ln.page}: cannot align fonts for word {w.text!r}")
            w.bold = sflags[k:k + len(w.text)]


# ================================================================ 2a. classify
def within(v, rng):
    return rng[0] <= v <= rng[1]


def margin(page):
    return MARGIN_ODD if page % 2 else MARGIN_EVEN


def classify(lines: list[Line], report: list[str]) -> list[Line]:
    toc_page = next((l.page for l in lines if within(l.h, H_PART) and l.text == "ÍNDEX"), None)
    if toc_page is None:
        raise StructureError("ÍNDEX page not found")
    counts = collections.Counter()
    for l in lines:
        h, t = l.h, l.text
        if l.y0 < HEADER_MAX_Y and within(h, H_HEADER):
            if not HEADER_RE.match(t):
                raise StructureError(f"p{l.page}: unexpected header text {t!r}")
            l.kind = "header"
        elif h >= H_SLOGAN_MIN:
            l.kind = "slogan"
        elif l.page == 1 and within(h, H_PART):
            l.kind = "cover"
        elif l.page < toc_page and within(h, H_HALFTITLE):
            l.kind = "halftitle"
        elif l.page == toc_page:
            l.kind = "toc_title" if t == "ÍNDEX" else "toc"
        elif within(h, H_PART):
            l.kind = "part"
        elif within(h, H_SECTION):
            l.kind = "section"
        elif within(h, H_BODY):
            off = (l.x0 - margin(l.page))
            if MARKER_RE.match(t) and abs(off) <= X_TOL:
                l.kind = "marker"
            elif abs(off) <= X_TOL or (l.tab and l.x0 > margin(l.page) + X_TOL):
                l.kind = "body"
            elif abs(off - OFF_ITEM) <= X_TOL:
                l.kind = "body"
            elif abs(off - OFF_SUB) <= X_TOL and t.startswith("– "):
                l.kind = "sub"
            elif abs(off - OFF_SUB_CONT) <= X_TOL:
                l.kind = "sub_cont"
            else:
                raise StructureError(f"p{l.page}: body line at unexpected x={l.x0:.1f}: {t[:60]!r}")
        else:
            raise StructureError(f"p{l.page}: unclassified line h={h:.2f}: {t[:60]!r}")
        counts[l.kind] += 1
    report.append(f"lines by kind: {dict(sorted(counts.items()))}")

    # attach separate "N." markers to the body line they number
    for m in [l for l in lines if l.kind == "marker"]:
        cands = [b for b in lines if b.kind == "body" and b.page == m.page
                 and abs(b.y0 - m.y0) <= 3 and b.x0 > m.x1]
        if len(cands) != 1:
            raise StructureError(f"p{m.page}: cannot attach proposal number {m.text}")
        cands[0].marker = MARKER_RE.match(m.text).group(1)
    lines = [l for l in lines if l.kind not in ("marker", "header")]
    # numbers printed on the same line as the text ("254. Reforçar ...")
    for l in lines:
        if l.kind == "body" and not l.marker and abs(l.x0 - margin(l.page)) <= X_TOL:
            m = INLINE_ITEM_RE.match(l.words[0].text + " " + (l.words[1].text if len(l.words) > 1 else ""))
            if m and l.words[0].text == m.group(1) + ".":
                l.marker = m.group(1)
                l.words = l.words[1:]
    return lines


# ================================================================ 2b. structure
@dataclass
class Block:
    kind: str                      # title subtitle slogan halftitle toc part section prose item itempara sub
    lines: list[Line] = field(default_factory=list)
    marker: str = ""
    toc: list = field(default_factory=list)


class Text:
    """Running text of a block as characters with bold flags; joins lines,
    removing layout hyphens at line ends."""

    def __init__(self):
        self.chars: list[tuple[str, bool]] = []

    def add_line(self, ln: Line, hyph: "Hyphens"):
        first, prev = True, None
        for w in ln.words:
            pairs = list(zip(w.text, w.bold or [False] * len(w.text)))
            if first and self.chars:
                if self.chars[-1][0] == "-" and hyph.is_layout(self, w.text):
                    self.chars.pop()
                    hyph.joined += 1
                elif self.chars[-1][0] == "-":
                    hyph.kept.append(self.last_word() + w.text)
                else:
                    self.chars.append((" ", self.chars[-1][1] and pairs[0][1]))
            elif not first:
                if w.x0 - prev.x1 >= WORD_GLUE_GAP:
                    self.chars.append((" ", self.chars[-1][1] and pairs[0][1]))
            self.chars += pairs
            prev, first = w, False

    def last_word(self):
        s = "".join(c for c, _ in self.chars)
        return s.split(" ")[-1]

    @property
    def plain(self):
        return "".join(c for c, _ in self.chars)


class Hyphens:
    """A hyphen at a line end is a layout hyphen unless the document shows it
    is real: digits on both sides, or the hyphenated form printed elsewhere
    inside a line while the joined form never is."""

    def __init__(self, lines: list[Line]):
        inline = collections.Counter()
        joined = collections.Counter()
        for l in lines:
            for t in l.text.split():
                t = t.strip(".,;:()«»“”")
                if "-" in t.strip("-"):
                    inline[t] += 1
                joined[t] += 1
        self.inline, self.words = inline, joined
        self.joined, self.kept = 0, []

    def is_layout(self, text: Text, nxt: str) -> bool:
        left = text.last_word()[:-1].strip(".,;:()«»“”")
        right = re.match(r"[\w·’']*", nxt).group(0)
        if left[-1:].isdigit() and right[:1].isdigit():
            return False
        if self.inline[f"{left}-{right}"] and not self.words[left + right]:
            return False
        return True


def build_blocks(lines: list[Line], report: list[str]) -> list[Block]:
    hyph = Hyphens(lines)
    blocks: list[Block] = []
    texts: dict[int, Text] = {}
    toc_lines = []
    prev: Line | None = None
    page_breaks = []

    def new(kind, ln, marker=""):
        b = Block(kind, [ln], marker)
        blocks.append(b)
        t = Text()
        t.add_line(ln, hyph)
        texts[id(b)] = t
        return b

    def extend(b, ln):
        b.lines.append(ln)
        texts[id(b)].add_line(ln, hyph)

    for ln in lines:
        k = ln.kind
        if k == "cover":
            new("title" if not any(b.kind == "title" for b in blocks) else "subtitle", ln)
        elif k == "slogan":
            new("slogan", ln)
        elif k == "halftitle":
            if blocks and blocks[-1].kind == "halftitle":
                extend(blocks[-1], ln)
            else:
                new("halftitle", ln)
        elif k == "toc_title":
            new("part", ln)
        elif k == "toc":
            toc_lines.append(ln)
            if not blocks or blocks[-1].kind != "toc":
                blocks.append(Block("toc"))
        elif k in ("part", "section"):
            b = blocks[-1] if blocks else None
            if b and b.kind == k and prev and prev.page == ln.page and ln.y0 - prev.y0 < 2.5 * ln.h:
                extend(b, ln)
            else:
                new(k, ln)
        elif k in ("sub", "sub_cont"):
            b = blocks[-1]
            if k == "sub":
                if b.kind not in ("item", "itempara", "sub"):
                    raise StructureError(f"p{ln.page}: sub-item outside a proposal: {ln.text[:50]!r}")
                new("sub", ln)
            else:
                if b.kind != "sub":
                    raise StructureError(f"p{ln.page}: sub-item continuation without sub-item")
                extend(b, ln)
        else:  # body
            off = ln.x0 - margin(ln.page)
            b = blocks[-1] if blocks else None
            same_page = prev is not None and prev.page == ln.page
            gap_ok = same_page and ln.y0 - prev.y0 <= PARA_GAP
            if ln.marker:
                new("item", ln, ln.marker)
            elif ln.tab:
                if not b or b.kind not in ("item", "itempara"):
                    raise StructureError(f"p{ln.page}: indented paragraph outside a proposal")
                new("itempara", ln)
            elif abs(off) <= X_TOL:                                      # prose
                if b and b.kind == "prose" and (gap_ok or (not same_page and cross_page(texts[id(b)], ln))):
                    if not same_page:
                        page_breaks.append((prev.page, ln.page, "continues", ln.text[:40]))
                    extend(b, ln)
                else:
                    if b and b.kind == "prose" and not same_page:
                        page_breaks.append((prev.page, ln.page, "new paragraph", ln.text[:40]))
                    new("prose", ln)
            else:                                                        # item text, no number
                if not b or b.kind not in ("item", "itempara", "sub"):
                    raise StructureError(f"p{ln.page}: proposal text without a number: {ln.text[:50]!r}")
                if b.kind == "sub" or (same_page and not gap_ok):
                    new("itempara", ln)
                elif not same_page and not cross_page(texts[id(b)], ln):
                    page_breaks.append((prev.page, ln.page, "new paragraph", ln.text[:40]))
                    new("itempara", ln)
                else:
                    if not same_page:
                        page_breaks.append((prev.page, ln.page, "continues", ln.text[:40]))
                    extend(b, ln)
        prev = ln

    toc = build_toc(toc_lines)
    for b in blocks:
        if b.kind == "toc":
            b.toc = toc
        b.text = texts.get(id(b))
    report.append(f"line-end hyphens joined: {hyph.joined}; kept as real hyphens: {len(hyph.kept)} {hyph.kept}")
    report.append("page breaks inside the text:")
    report += [f"  p{a}->p{c}: {d:13s} {t!r}" for a, c, d, t in page_breaks]
    return blocks


def cross_page(text: Text, ln: Line) -> bool:
    s = text.plain
    return s.endswith("-") or not s.endswith(TERMINAL) or ln.text[:1].islower()


def build_toc(lines: list[Line]) -> list[tuple[str, str, bool]]:
    """(entry, page, is_part). Section letters ("A.") are separate text items."""
    entries = []
    letters = [l for l in lines if re.fullmatch(r"[A-Z]\.", l.text)]
    for l in lines:
        if l in letters:
            continue
        m = TOC_ENTRY_RE.match(l.text)
        if not m:
            raise StructureError(f"unexpected ÍNDEX line: {l.text!r}")
        title, page = m.groups()
        part = within(l.h, H_HALFTITLE)
        if not part:
            lt = [x for x in letters if x.page == l.page and abs(x.y0 - l.y0) <= 2]
            if len(lt) != 1:
                raise StructureError(f"ÍNDEX entry without its letter: {l.text!r}")
            title = f"{lt[0].text} {title}"
        entries.append((title, page, part))
    return entries


# ================================================================ 2c. RST
def inline(chars: list[tuple[str, bool]]) -> str:
    """Characters with bold flags -> RST inline text with **strong** runs."""
    return C.inline(chars, strong=lambda bold: bold)


def emit(blocks: list[Block]) -> str:
    out: list[str] = []
    content_indent = 0
    for b in blocks:
        t = b.text
        if b.kind == "title":
            out += C.heading(t.plain, "#", over=True)
        elif b.kind == "subtitle":
            out += C.heading(t.plain, "*", over=True)
        elif b.kind == "slogan":
            if out and out[-1] == "" and out[-2].startswith("| "):
                out.pop()
            out += ["| " + C.esc_inline(t.plain), ""]
        elif b.kind == "halftitle":
            out += [".. " + t.plain, ""]
        elif b.kind == "part":
            out += C.heading(t.plain, "=")
        elif b.kind == "section":
            out += C.heading(t.plain, "-")
        elif b.kind == "toc":
            rows = [(("**" + C.esc_inline(e) + "**") if part else C.esc_start(C.esc_inline(e)), p)
                    for e, p, part in b.toc]
            w = max(len(r[0]) for r in rows)
            bar = "=" * w + "  " + "=" * 4
            out += [bar] + [f"{r[0]:<{w}}  {r[1]}" for r in rows] + [bar, ""]
        elif b.kind == "prose":
            content_indent = 0
            out += C.wrap(C.esc_start(inline(t.chars)), "") + [""]
        elif b.kind == "item":
            marker = f"{b.marker}. "
            content_indent = len(marker)
            out += C.wrap(inline(t.chars), " " * content_indent, marker) + [""]
        elif b.kind == "itempara":
            out += C.wrap(C.esc_start(inline(t.chars)), " " * content_indent) + [""]
        elif b.kind == "sub":
            ind = " " * (content_indent + 3)
            out += C.wrap(C.esc_start(inline(t.chars)), ind) + [""]
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ================================================================ 3. verify
ADORN = re.compile(r"^([#*=\-~])\1+$")
TABLE_BORDER = re.compile(r"^=+ +=+$")


def rst_plain(rst: str):
    """Undo exactly the markup this script emits. Returns (words, flags,
    proposal numbers, ÍNDEX letters): flag True = inside a comment (not
    rendered), "letter" = ÍNDEX section letter (compared apart because
    pdftotext prints the letters as a column)."""
    words, is_comment, markers, letters = [], [], [], []
    in_table = False
    for line in rst.split("\n"):
        if TABLE_BORDER.match(line):
            in_table = not in_table
            continue
        if ADORN.match(line):
            continue
        s, comment = line.strip(), False
        if s.startswith(".. "):
            s, comment = s[3:], True
        elif line.startswith("| "):
            s = line[2:]
        elif in_table and re.match(r"^[A-Z]\\\. ", line):
            letters.append(line[0] + ".")
            words.append(line[0] + ".")
            is_comment.append("letter")
            s = line[4:]
        else:
            m = re.match(r"^(\d{1,3})\. ", line)        # list item (never indented)
            if m:
                markers.append(m.group(1))
                s = line[m.end():]
        if not comment:
            s = s.replace("\\ ", "").replace("**", "")
            s = re.sub(r"\\(.)", r"\1", s)
        for w in s.split():
            words.append(w)
            is_comment.append(comment)
    return words, is_comment, markers, letters


def reference(raw: str):
    """Plain pdftotext output without what step 2 removes: running headers,
    page numbers and the ÍNDEX dot leaders. Proposal numbers (line-initial
    "N.") are returned separately."""
    words, markers, letters, removed = [], [], [], collections.Counter()
    for page in raw.split("\f"):
        toc_page = any(l.strip() == "ÍNDEX" for l in page.split("\n"))
        for line in page.split("\n"):
            s = line.strip()
            if not s:
                continue
            if toc_page and re.fullmatch(r"[A-Z]\.", s):
                letters.append(s)
                continue
            if HEADER_RE.match(s):
                removed["running header / page number lines"] += 1
                continue
            m = re.match(r"^(\d{1,3})\.(?: |$)", s)
            if m:
                markers.append(m.group(1))
                s = s[m.end():]
            if toc_page and LEADER_RE.search(s):
                removed["ÍNDEX dot leaders"] += 1
                s = LEADER_RE.sub(" ", s)
            words += s.split()
    return words, markers, letters, removed


def verify(rst: str, raw: str, report: list[str]) -> bool:
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        report.append("  FAIL: " + msg)

    out_all, flags, out_m, out_l = rst_plain(rst)
    out_w = [w for w, f in zip(out_all, flags) if f != "letter"]       # text, as in the PDF
    rendered = [w for w, f in zip(out_all, flags) if f is not True]    # what a reader sees
    ref_w, ref_m, ref_l, removed = reference(raw)

    report.append("[1] word sequence vs plain pdftotext")
    for k, v in sorted(removed.items()):
        report.append(f"  set aside in the PDF text: {k}: {v}")
    sm = difflib.SequenceMatcher(a=ref_w, b=out_w, autojunk=False)
    joins = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        A, B = ref_w[i1:i2], out_w[j1:j2]
        if A and B and "".join(t[:-1] if t.endswith("-") and k < len(A) - 1 else t
                               for k, t in enumerate(A)) == "".join(B) and "".join(A) != "".join(B):
            joins += sum(1 for t in A[:-1] if t.endswith("-"))
            continue
        if A and B and "".join(A) == "".join(B):
            continue
        ctx = " ".join(ref_w[max(0, i1 - 5):i1])
        fail(f"{tag} after …{ctx!r}: PDF {A[:12]!r} -> RST {B[:12]!r}")
    report.append(f"  PDF words {len(ref_w)}, RST words {len(out_w)}, "
                  f"line-end hyphens joined across page/column breaks: {joins}")
    if ok:
        report.append("  identical: nothing altered, omitted, added or reordered")

    report.append("[2] proposal numbers and ÍNDEX letters (printed as columns by pdftotext)")
    nums = [int(n) for n in out_m]
    if sorted(nums) != sorted(int(n) for n in ref_m):
        only_pdf = sorted(set(map(int, ref_m)) - set(nums))
        only_rst = sorted(set(nums) - set(map(int, ref_m)))
        fail(f"proposal numbers differ: PDF {len(ref_m)}, RST {len(out_m)}; "
             f"only in PDF {only_pdf[:10]}, only in RST {only_rst[:10]}")
    elif any(b <= a for a, b in zip(nums, nums[1:])):
        fail("proposal numbers are not increasing")
    else:
        report.append(f"  {len(nums)} proposals, {nums[0]}..{nums[-1]}: the same numbers as the PDF, in order")
        for a, b in zip(nums, nums[1:]):
            if b != a + 1:
                missing = f"{a + 1}" if b == a + 2 else f"{a + 1}-{b - 1}"
                report.append(f"  WARNING: the PDF skips proposal number(s) {missing} ({a} is followed by {b})")
    if out_l != ref_l:
        fail(f"ÍNDEX letters differ: PDF {ref_l} RST {out_l}")
    else:
        report.append(f"  ÍNDEX section letters: same {len(out_l)}, same order")

    report.append("[3] character inventory (non-whitespace)")
    cr = collections.Counter("".join(ref_w))
    co = collections.Counter("".join(out_w))
    cr["-"] -= joins
    if +cr != +co:
        fail(f"only in PDF: {dict((cr - co).most_common(10))}; only in RST: {dict((co - cr).most_common(10))}")
    else:
        report.append(f"  identical ({sum(co.values())} characters)")

    report.append("[4] pandoc reading of the RST")
    try:
        r = C.pandoc_read(rst, list_labels=False)     # proposal numbers compared apart
        pw, pm, headers, warn = r.words, r.markers, r.headers, r.warnings
    except StructureError as e:
        fail(str(e))
        return ok
    if warn:
        fail("pandoc warnings: " + warn[:300])
    expect = rendered            # comments are not rendered; ÍNDEX letters are
    if pw != expect:
        d = next((i for i, (a, b) in enumerate(zip(expect, pw)) if a != b), min(len(expect), len(pw)))
        fail(f"pandoc reads different words at {d}: {expect[d:d + 8]!r} vs {pw[d:d + 8]!r}")
    elif pm != out_m:
        fail("pandoc reads different list numbers")
    else:
        report.append(f"  same {len(pw)} words and {len(pm)} list numbers; "
                      f"headings: {collections.Counter(l for l, _ in headers)}")

    report.append("[5] headings vs ÍNDEX")
    toc = []
    for line in rst.split("\n"):
        m = re.match(r"^(\*\*)?(.+?)\*{0,2}\s{2,}(\d{1,3})$", line)
        if m and not TABLE_BORDER.match(line):
            toc.append(re.sub(r"\\(.)", r"\1", m.group(2)).strip())
    names = [h for _, h in headers]
    start = names.index("ÍNDEX") + 1 if "ÍNDEX" in names else 0
    heads = [h for _, h in headers[start:]]
    if toc == heads:
        report.append(f"  all {len(toc)} ÍNDEX entries match the headings, in order")
    else:
        for a, b in zip(toc, heads):
            if a != b:
                report.append(f"  WARNING: ÍNDEX {a!r} vs heading {b!r}")
        if len(toc) != len(heads):
            report.append(f"  WARNING: {len(toc)} ÍNDEX entries, {len(heads)} headings")
    return ok


# ================================================================ main
def convert(pdf: str, report: list[str]) -> str:
    lines = extract_lines(pdf)
    apply_bold(pdf, lines)
    lines = classify(lines, report)
    blocks = build_blocks(lines, report)
    counts = collections.Counter(b.kind for b in blocks)
    report.insert(1, f"blocks: {dict(sorted(counts.items()))}")
    items = [b for b in blocks if b.kind == "item"]
    no_lead = [b.marker for b in items if not (b.text.chars and b.text.chars[0][1])]
    report.append(f"proposals: {len(items)}; without a bold lead-in: {no_lead or 'none'}")
    return emit(blocks)


if __name__ == "__main__":
    sys.exit(C.main(convert, verify, "the ERC programme", default_output="erc.rst"))
