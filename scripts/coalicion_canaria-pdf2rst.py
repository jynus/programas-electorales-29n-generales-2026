#!/usr/bin/env python3
"""
pdf2rst.py - Convert a (born-digital) PDF into clean reStructuredText using
poppler-utils' ``pdftotext``, and verify that no text was altered, omitted or
added in the process.

Pipeline
--------
1. Extraction (``pdftotext -bbox-layout``): words with their coordinates,
   grouped by poppler into flows / blocks / lines.  Word height is used as a
   proxy for font size, which tells headings, item titles and body text apart.

2. Clean-up and conversion to RST:
     * running headers (text repeated at the same position on most pages)
       are dropped;
     * lines are re-flowed into paragraphs; words hyphenated at the end of a
       line, column or page are re-joined;
     * paragraphs split by a page or column break are merged back;
     * multi-line display headings are re-assembled in visual order
       ("1 DEFENDER LOS DERECHOS DE CANARIAS");
     * numbered measures ("23. Title. Text...") become RST enumerated lists
       with the bold title as ``**strong**``;
     * candidate lists become definition lists (name -> constituency).

3. Verification against an *independent* extraction: plain
   ``pdftotext`` (reading-order mode), which poppler produces with different
   code paths from the bbox output used above.  The RST is reduced to plain
   text (adornment lines, list bullets, ``**`` and backslash escapes removed)
   and its word sequence is compared with the reference word sequence:

     * the only declared removals are the running headers;
     * the only declared edits are de-hyphenations ("consen-" + "so" ->
       "consenso") across line, column or page breaks;
     * anything else is either a *move* (the same words in a different
       order, e.g. a heading that pdftotext reads in the wrong order),
       which is listed for review, or an error.

   The word multiset must match exactly; otherwise the run fails with a list
   of differences and the exit status is 1.  With ``--strict`` moves also
   count as failures.  If ``docutils`` is installed, the RST is also parsed
   and any warnings are reported.

The typography table (STYLE_HEIGHTS) is tuned to the "DEFIENDE CANARIAS"
programme (CCA 2026).  Text with an unknown font size is treated as body
text and reported as a warning.  The verifier does not depend on that table,
so a wrong guess there can change the structure but never the text without
being reported.

Usage:  python3 pdf2rst.py input.pdf [-o output.rst] [--report report.txt]
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

XHTML = "{http://www.w3.org/1999/xhtml}"
WRAP = 79

# Word height (pt) -> role.  Heights are matched with a tolerance of HEIGHT_TOL.
STYLE_HEIGHTS = [
    (99.89, "doc_title"),      # cover: DEFIENDE CANARIAS
    (25.57, "doc_subtitle"),   # cover: BASES DEL PROGRAMA ...
    (74.28, "display"),        # cover: 29N
    (72.09, "display"),        # closing slogan
    (63.45, "h1"),             # MANIFIESTO POLÍTICO / DESARROLLO PROGRAMÁTICO
    (24.76, "h2"),             # Canarias tiene que contar ...
    (55.64, "cause_number"),   # big 1..10
    (27.14, "cause_title"),    # DEFENDER ...
    (22.40, "h1"),             # CONGRESO / SENADO
    (15.39, "cand_name"),
    (10.17, "cand_small"),     # constituency under a name
    (10.14, "cand_small"),     # list of further candidates
    (13.14, "lead"),           # bold: item titles, cause intros
    (12.38, "body"),
    (28.23, "header"),         # running header (also detected by repetition)
    (9.55, "header"),
]
HEIGHT_TOL = 0.3
TERMINAL = tuple(".:;!?»)\"”")


# --------------------------------------------------------------------------
# pdftotext
# --------------------------------------------------------------------------

def pdftotext(pdf: str, *args: str) -> str:
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext not found: install poppler-utils")
    res = subprocess.run(["pdftotext", "-enc", "UTF-8", *args, pdf, "-"],
                         capture_output=True, check=True)
    return res.stdout.decode("utf-8")


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Line:
    words: list[Word]

    @property
    def x0(self): return min(w.x0 for w in self.words)
    @property
    def x1(self): return max(w.x1 for w in self.words)
    @property
    def y0(self): return min(w.y0 for w in self.words)
    @property
    def y1(self): return max(w.y1 for w in self.words)
    @property
    def text(self): return " ".join(w.text for w in self.words)


@dataclass
class Block:
    page: int
    flow: int
    lines: list[Line]
    role: str = ""

    @property
    def words(self): return [w for l in self.lines for w in l.words]
    @property
    def x0(self): return min(l.x0 for l in self.lines)
    @property
    def y0(self): return min(l.y0 for l in self.lines)
    @property
    def text(self): return " ".join(l.text for l in self.lines)


def parse_bbox(html: str) -> list[list[Block]]:
    html = re.sub(r"<!DOCTYPE[^>]*>", "", html)
    root = ET.fromstring(html)
    pages = []
    for pno, pg in enumerate(root.iter(XHTML + "page"), 1):
        blocks = []
        for fno, fl in enumerate(pg.findall(XHTML + "flow")):
            for b in fl.findall(XHTML + "block"):
                lines = []
                for l in b.findall(XHTML + "line"):
                    ws = [Word(w.text or "", float(w.get("xMin")), float(w.get("yMin")),
                               float(w.get("xMax")), float(w.get("yMax")))
                          for w in l.findall(XHTML + "word")]
                    ws = [w for w in ws if w.text]
                    if ws:
                        lines.append(Line(ws))
                if lines:
                    blocks.append(Block(pno, fno, lines))
        pages.append(blocks)
    return pages


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def height_role(h: float, warnings: list[str], ctx: str) -> str:
    best = min(STYLE_HEIGHTS, key=lambda t: abs(t[0] - h))
    if abs(best[0] - h) <= HEIGHT_TOL:
        return best[1]
    warnings.append(f"unknown text height {h:.2f} ({ctx}); treated as body")
    return "body"


def detect_running_headers(pages: list[list[Block]]) -> set[tuple[str, int]]:
    """Blocks with identical text at the same position on >= half the pages."""
    seen = Counter()
    for blocks in pages:
        keys = {(b.text, round(b.y0)) for b in blocks}
        seen.update(keys)
    n = len([p for p in pages if p])
    return {k for k, c in seen.items() if c >= max(2, n // 2)}


def classify(pages, warnings):
    headers = detect_running_headers(pages)
    for blocks in pages:
        for b in blocks:
            if (b.text, round(b.y0)) in headers:
                b.role = "header"
                continue
            roles = [height_role(w.h, warnings, f"p{b.page} '{w.text}'") for w in b.words]
            rs = set(roles)
            if "body" in rs:
                b.role = "body"
            elif rs <= {"cause_number"}:
                b.role = "cause_number"
            elif "cause_title" in rs:
                b.role = "cause_title"
            elif rs == {"lead"}:
                b.role = "lead"
            else:
                # role of the tallest word
                order = [r for _, r in sorted(STYLE_HEIGHTS, reverse=True)]
                b.role = min(rs, key=order.index)


# --------------------------------------------------------------------------
# Document model
# --------------------------------------------------------------------------

@dataclass
class Elem:
    kind: str                 # title subtitle h1 h2 para item display defitem bullet
    text: str = ""
    num: int | None = None
    bold: str = ""            # item title (strong)
    extra: list[str] = field(default_factory=list)  # item continuation paragraphs
    term: str = ""


def join_words(words: list[Word]) -> list[Word]:
    """Words of consecutive lines -> one list; re-join end-of-line hyphenations."""
    out: list[Word] = []
    for w in words:
        if out and out[-1].text.endswith("-") and len(out[-1].text) > 1 \
                and out[-1].text[-2].isalpha() and getattr(out[-1], "_eol", False) \
                and w.text[:1].islower():
            prev = out[-1]
            out[-1] = Word(prev.text[:-1] + w.text, prev.x0, prev.y0, prev.x1, prev.y1)
            continue
        out.append(w)
    return out


def line_words(lines: list[Line]) -> list[Word]:
    ws = []
    for l in lines:
        for i, w in enumerate(l.words):
            w = Word(w.text, w.x0, w.y0, w.x1, w.y1)
            if i == len(l.words) - 1:
                w._eol = True  # type: ignore[attr-defined]
            ws.append(w)
    return join_words(ws)


def heading_text(blocks: list[Block]) -> tuple[str, str]:
    """Re-assemble a display heading in visual order. Returns (number, text)."""
    words = [w for b in blocks for w in b.words]
    num = [w for w in words if w.text.isdigit() and w.h > 40]
    rest = [w for w in words if w not in num]
    rows: list[list[Word]] = []
    for w in sorted(rest, key=lambda w: (w.y0, w.x0)):
        if rows and abs(rows[-1][0].y0 - w.y0) < 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    text = " ".join(w.text for r in rows for w in sorted(r, key=lambda w: w.x0))
    return (num[0].text if num else ""), text


class Builder:
    def __init__(self, warnings):
        self.elems: list[Elem] = []
        self.warnings = warnings
        self.next_item = 1
        self.pending_number = ""
        self.cause_blocks: list[Block] = []
        # continuation state
        self.last_text: Elem | None = None
        self.last_block: Block | None = None
        self.last_line_full = False
        self.item_indent: float | None = None

    # -- helpers -----------------------------------------------------------
    def heading(self, kind, text):
        self.elems.append(Elem(kind, text))
        self.last_text = None
        self.item_indent = None

    def append_text(self, target: Elem, words: list[Word]):
        """Append words to target's last paragraph (handles hyphen joins)."""
        add = " ".join(w.text for w in words)
        if target.kind == "item" and target.extra:
            base = target.extra[-1]
        else:
            base = target.text
        if base.endswith("-") and len(base) > 1 and base[-2].isalpha() and add[:1].islower():
            new = base[:-1] + add
        else:
            new = (base + " " + add).strip()
        if target.kind == "item" and target.extra:
            target.extra[-1] = new
        else:
            target.text = new

    # -- main --------------------------------------------------------------
    def flush_cause(self):
        if self.cause_blocks:
            num, text = heading_text(self.cause_blocks)
            num = num or self.pending_number
            self.heading("h2", f"{num} {text}".strip())
            self.cause_blocks = []
            self.pending_number = ""

    def add_page(self, blocks: list[Block]):
        if any(b.role in ("cand_name", "cand_small") for b in blocks):
            self.flush_cause()
            self.add_candidates(blocks)
            return
        for b in blocks:
            if b.role != "cause_title":
                self.flush_cause()
            r = b.role
            if r == "header":
                continue
            if r == "doc_title":
                self.heading("title", heading_text([b])[1])
            elif r == "doc_subtitle":
                self.heading("subtitle", b.text)
            elif r == "display":
                self.elems.append(Elem("display", b.text))
                self.last_text = None
            elif r in ("h1", "h2"):
                self.heading(r, heading_text([b])[1])
            elif r == "cause_number":
                self.pending_number = b.text
            elif r == "cause_title":
                self.cause_blocks.append(b)
            else:  # body / lead
                self.add_body(b)
        self.flush_cause()

    def add_body(self, b: Block):
        # split block lines into runs starting at item numbers
        runs: list[tuple[int | None, list[Line]]] = []
        for li, line in enumerate(b.lines):
            first = line.words[0]
            starts_item = first.text == f"{self.next_item}." and (
                first.h > 12.8 or li == 0 or b.lines[li - 1].text.endswith(TERMINAL))
            if starts_item:
                runs.append((self.next_item, [line]))
                self.next_item += 1
            elif runs:
                runs[-1][1].append(line)
            else:
                runs.append((None, [line]))

        for i, (num, lines) in enumerate(runs):
            words = line_words(lines)
            if num is None:
                self.add_continuation(b, words)
            else:
                body = words[1:]
                k = 0
                while k < len(body) and body[k].h > 12.8:
                    k += 1
                if k == 0:
                    # Title not set in bold in the PDF: take the first sentence.
                    while k < len(body) and not body[k].text.endswith("."):
                        k += 1
                    k = k + 1 if k < len(body) else 0
                    self.warnings.append(f"item {num}: title not bold in the PDF; "
                                         f"first sentence used as title")
                title = " ".join(w.text for w in body[:k])
                rest = " ".join(w.text for w in body[k:])
                e = Elem("item", rest, num=num, bold=title)
                self.elems.append(e)
                self.last_text = e
                self.item_indent = b.x0
            last_line = lines[-1]
            widths = [l.x1 for l in b.lines]
            self.last_line_full = len(b.lines) > 1 and last_line.x1 >= max(widths) - 3
        self.last_block = b

    def add_continuation(self, b: Block, words: list[Word]):
        prev = self.last_text
        lb = self.last_block
        crossed = lb is None or lb.page != b.page or abs(lb.x0 - b.x0) > 50
        first = words[0].text
        prev_txt = (prev.extra[-1] if prev and prev.kind == "item" and prev.extra
                    else prev.text if prev else "")
        if prev is not None and (
                first[:1].islower()
                or prev_txt.endswith("-")
                or (crossed and (self.last_line_full or not prev_txt.endswith(TERMINAL)))):
            self.append_text(prev, words)
            return
        text = " ".join(w.text for w in words)
        if prev is not None and prev.kind == "item" and self.item_indent is not None \
                and abs(b.x0 - self.item_indent) < 4:
            prev.extra.append(text)
            return
        e = Elem("para", text)
        self.elems.append(e)
        self.last_text = e
        self.item_indent = None

    def add_candidates(self, blocks: list[Block]):
        lines = [(b, l) for b in blocks if b.role != "header" for l in b.lines]
        names = [l for b, l in lines if b.role == "cand_name"]
        role_of: dict[int, Line] = {}
        used = set()
        for b, l in lines:
            if b.role != "cand_small":
                continue
            best = None
            for n in names:
                dy = l.y0 - n.y0
                overlap = min(l.x1, n.x1) - max(l.x0, n.x0)
                if 0 < dy <= n.y1 - n.y0 + 2 and overlap > 0 and id(n) not in role_of:
                    if best is None or dy < best[0]:
                        best = (dy, n)
            if best:
                role_of[id(best[1])] = l
                used.add(id(l))
        for b, l in lines:
            if b.role == "h1":
                self.heading("h1", l.text)
            elif b.role == "cand_name":
                r = role_of.get(id(l))
                self.elems.append(Elem("defitem", r.text if r else "", term=l.text))
            elif b.role == "cand_small" and id(l) not in used:
                self.elems.append(Elem("bullet", l.text))
            elif b.role in ("body", "lead"):
                self.elems.append(Elem("para", l.text))
        self.last_text = None


# --------------------------------------------------------------------------
# RST rendering
# --------------------------------------------------------------------------

ENUM_LIKE = re.compile(r"^(\(?(\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]|[-*+•‣⁃])(\s|$)")


def esc(text: str, block_start: bool = True) -> str:
    text = re.sub(r"([\\*`|_])", r"\\\1", text)
    text = text.replace("::", r":\:")
    if block_start and (ENUM_LIKE.match(text) or text.startswith("..")):
        text = "\\" + text
    return text


def wrap(text: str, indent: str = "", first: str = "") -> str:
    return textwrap.fill(text, width=WRAP, initial_indent=first or indent,
                         subsequent_indent=indent, break_long_words=False,
                         break_on_hyphens=False)


def render(elems: list[Elem]) -> str:
    out: list[str] = []

    def emit(s: str):
        out.append(s)
        out.append("")

    for e in elems:
        if e.kind in ("title", "subtitle"):
            t = esc(e.text, False)
            ch = "=" if e.kind == "title" else "-"
            emit(f"{ch * len(t)}\n{t}\n{ch * len(t)}")
        elif e.kind in ("h1", "h2"):
            t = esc(e.text, False)
            emit(f"{t}\n{('=' if e.kind == 'h1' else '-') * len(t)}")
        elif e.kind == "display":
            emit(f"**{esc(e.text, False)}**")
        elif e.kind == "para":
            emit(wrap(esc(e.text)))
        elif e.kind == "item":
            marker = f"{e.num}. "
            ind = " " * len(marker)
            parts = []
            if e.bold:
                parts.append(f"**{esc(e.bold, False)}**")
            if e.text:
                parts.append(esc(e.text, not e.bold))
            emit(wrap(" ".join(parts), ind, marker))
            for p in e.extra:
                emit(wrap(esc(p), ind))
        elif e.kind == "defitem":
            body = wrap(esc(e.text), "   ") if e.text else "   \\"
            emit(f"{esc(e.term)}\n{body}")
        elif e.kind == "bullet":
            # consecutive bullets form one compact list
            if out and out[-2:-1] and out[-2].startswith("- "):
                out.pop()
            emit(wrap(esc(e.text, False), "  ", "- "))
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

ADORNMENT = re.compile(r"^([=\-~^\"#*+`:.'])\1+$")


def rst_plain_tokens(rst: str) -> list[str]:
    """Plain-text projection of the RST constructs this script emits."""
    toks: list[str] = []
    prev_blank = True
    prev_bullet = False      # previous line was a top-level bullet item
    for line in rst.splitlines():
        s = line.strip()
        if not s:
            prev_blank, prev_bullet = True, False
            continue
        if ADORNMENT.match(s):
            prev_blank = prev_bullet = False
            continue
        if (prev_blank or prev_bullet) and line.startswith("- "):
            s = s[2:]
            prev_bullet = True
        else:
            prev_bullet = prev_bullet and line.startswith("  ")
        if s == "\\":          # empty definition
            prev_blank = False
            continue
        s = s.replace("**", "")
        s = re.sub(r"\\(.)", r"\1", s)
        toks.extend(s.split())
        prev_blank = False
    return toks


def reference_tokens(raw: str) -> tuple[list[str], list[str], int]:
    """Reference words from plain pdftotext, minus repeated page headers."""
    pages = raw.split("\f")
    starts = Counter()
    for p in pages:
        nb = [l.strip() for l in p.splitlines() if l.strip()]
        if len(nb) >= 3:
            starts[tuple(nb[:3])] += 1
    header, count = (starts.most_common(1)[0] if starts else ((), 0))
    removed = 0
    if count < max(2, len(pages) // 2):
        header = ()
    toks: list[str] = []
    for p in pages:
        lines = [l.strip() for l in p.splitlines()]
        nb_idx = [i for i, l in enumerate(lines) if l]
        if header and len(nb_idx) >= 3 and tuple(lines[i] for i in nb_idx[:3]) == header:
            for i in nb_idx[:3]:
                lines[i] = ""
            removed += 1
        for l in lines:
            toks.extend(l.split())
    return toks, list(header), removed


def dehyphenate(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        if out and out[-1].endswith("-") and len(out[-1]) > 1 and out[-1][-2].isalpha() \
                and t[:1].islower():
            out[-1] = out[-1][:-1] + t
        else:
            out.append(t)
    return out


def verify(ref: list[str], out: list[str], strict: bool) -> tuple[bool, list[str]]:
    rep: list[str] = []
    sm = difflib.SequenceMatcher(None, ref, out, autojunk=False)
    dehyph, dels, ins = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        a, b = ref[i1:i2], out[j1:j2]
        if op == "replace" and dehyphenate(a) == b:
            dehyph.append(f"{' '.join(a)!r} -> {' '.join(b)!r}")
            continue
        if a:
            dels.append((i1, tuple(a)))
        if b:
            ins.append((j1, tuple(b)))
    moves = []
    for d in list(dels):
        for x in ins:
            if x[1] == d[1]:
                moves.append(d)
                dels.remove(d)
                ins.remove(x)
                break

    ref_d = Counter(dehyphenate(ref))
    out_c = Counter(out)
    missing = ref_d - out_c
    added = out_c - ref_d

    rep.append(f"Reference words (plain pdftotext, headers removed): {len(ref)}")
    rep.append(f"RST words (markup stripped):                         {len(out)}")
    rep.append(f"Re-joined hyphenations across breaks: {len(dehyph)}")
    for h in dehyph:
        rep.append(f"    {h}")
    rep.append(f"Moved passages (same words, different order than plain pdftotext): {len(moves)}")
    for pos, m in moves:
        ctx = " ".join(ref[max(0, pos - 4):pos])
        rep.append(f"    {' '.join(m)!r}  (reference had it after: ...{ctx})")
    if dels or ins:
        rep.append(f"Unpaired reorderings: {len(dels)} deleted / {len(ins)} inserted segments")
        for pos, d in dels:
            rep.append(f"    ref  @{pos}: {' '.join(d)[:150]!r}")
        for pos, d in ins:
            rep.append(f"    rst  @{pos}: {' '.join(d)[:150]!r}")
    ok = not missing and not added
    if missing:
        rep.append(f"OMITTED / ALTERED words ({sum(missing.values())}): {dict(missing)}")
    if added:
        rep.append(f"ADDED / ALTERED words ({sum(added.values())}): {dict(added)}")
    if strict and (moves or dels or ins):
        ok = False
        rep.append("--strict: reorderings count as failures")
    return ok, rep


def structural_checks(elems: list[Elem]) -> list[str]:
    notes = []
    nums = [e.num for e in elems if e.kind == "item"]
    if nums:
        gaps = [n for n in range(1, max(nums) + 1) if n not in nums]
        notes.append(f"Numbered items: {len(nums)} ({min(nums)}-{max(nums)})"
                     + (f", MISSING {gaps}" if gaps else ", sequence complete"))
    for e in elems:
        paras = [e.text] + e.extra if e.kind in ("item", "para") else []
        for p in paras:
            if p and not p.rstrip().endswith(TERMINAL):
                label = f"item {e.num}" if e.kind == "item" else "paragraph"
                notes.append(f"Note: {label} ends without final punctuation "
                             f"(possibly truncated in the PDF itself): ...{p[-60:]!r}")
    return notes


def pandoc_tokens(rst: str) -> list[str] | None:
    """Words of the RST as parsed by pandoc (enumerators rebuilt from start+index)."""
    import json
    if not shutil.which("pandoc"):
        return None
    res = subprocess.run(["pandoc", "-f", "rst", "-t", "json"], input=rst.encode("utf-8"),
                         capture_output=True, check=True)
    doc = json.loads(res.stdout)
    toks: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            toks.extend("".join(buf).split())
            buf.clear()

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
            elif t == "OrderedList":
                flush()
                start = c[0][0]
                for i, item in enumerate(c[1]):
                    toks.append(f"{start + i}.")
                    walk(item)
                    flush()
            elif t in ("Para", "Plain", "Header"):
                flush()
                walk(c)
                flush()
            elif t == "MetaInlines":
                flush()
                walk(c)
                flush()
            else:
                walk(c)

    meta = doc.get("meta", {})
    for key in ("title", "subtitle"):
        if key in meta:
            walk(meta[key])
    walk(doc.get("blocks", []))
    flush()
    return toks


def docutils_check(rst: str) -> list[str]:
    try:
        from docutils.core import publish_doctree
        import io
    except ImportError:
        return ["docutils not installed: RST syntax check skipped"]
    err = io.StringIO()
    publish_doctree(rst, settings_overrides={"warning_stream": err, "report_level": 2})
    msgs = err.getvalue().strip()
    return ["docutils: RST parses without warnings"] if not msgs else ["docutils warnings:", msgs]


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", help="output .rst (default: <pdf>.rst)")
    ap.add_argument("--report", help="also write the verification report here")
    ap.add_argument("--strict", action="store_true",
                    help="fail if any passage was reordered relative to plain pdftotext")
    args = ap.parse_args(argv)
    out_path = args.output or re.sub(r"\.pdf$", "", args.pdf, flags=re.I) + ".rst"

    warnings: list[str] = []
    pages = parse_bbox(pdftotext(args.pdf, "-bbox-layout"))
    classify(pages, warnings)
    b = Builder(warnings)
    for blocks in pages:
        b.add_page(blocks)
    rst = render(b.elems)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(rst)

    ref, header, n_headers = reference_tokens(pdftotext(args.pdf))
    plain = rst_plain_tokens(rst)
    ok, rep = verify(ref, plain, args.strict)
    pt = pandoc_tokens(rst)
    if pt is None:
        rep.append("pandoc not installed: parser cross-check skipped")
    elif pt == plain:
        rep.append("pandoc cross-check: RST parsed by pandoc yields exactly the same "
                   "word sequence (markup neither hides nor adds text)")
    else:
        ok = False
        sm = difflib.SequenceMatcher(None, plain, pt, autojunk=False)
        diffs = [(" ".join(plain[i1:i2]), " ".join(pt[j1:j2]))
                 for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
        rep.append(f"pandoc cross-check FAILED ({len(diffs)} differences): {diffs[:10]}")

    report = [f"pdf2rst verification report for {args.pdf}",
              f"Output: {out_path}",
              f"Running header removed from {n_headers} pages: {' / '.join(header)!r}"]
    report += rep
    report += structural_checks(b.elems)
    report += docutils_check(rst)
    report += [f"Warning: {w}" for w in dict.fromkeys(warnings)]
    report.append("RESULT: " + ("PASS - no text altered, omitted or added" if ok else "FAIL"))
    text = "\n".join(report) + "\n"
    print(text, end="")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
