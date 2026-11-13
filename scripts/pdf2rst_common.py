"""
pdf2rst_common.py - shared code for the <party>-pdf2rst.py converters.

Used by salf, partido_humanista, pnv, por_un_mundo_mas_justo, frente_obrero,
iustitia_europa, vox and pacma (the whole engine: extraction with fonts, RST
writing, verification and the command line), and in part by bng, erc and
coalicion_canaria (``read_bbox``, ``run``, RST escaping/wrapping,
``pandoc_read``; erc also ``inline`` and ``main``).
build.py uses the pandoc reader (``pandoc_ast``/``ast_text``) and the
``pdftotext -layout`` evidence of real line-end hyphens
(``layout_hyphen_breaks``). pp and adelante_andalucia are self-contained.

Extraction (poppler-utils)
  * ``pdftotext -bbox-layout`` gives the text in blocks, lines and words with
    their positions, in exactly the reading order of plain ``pdftotext``;
  * ``pdftohtml -xml`` gives the font (family, size, colour) of every text
    run; each word gets the font of the run under it, so blocks can be
    classified by style and inline bold can be kept.
Every block's text is built like plain pdftotext builds it (lines joined
with a space, a hyphen at a line end joined to the next line), so the RST
can be checked word by word against plain pdftotext.

Verification
  * the RST, with only the markup the converters emit stripped, must give
    exactly the words of plain pdftotext once the page furniture the
    converter declares (``Furniture``) is set aside;
  * same characters (inventory);
  * pandoc must read the same words (markup did not swallow or create text)
    and emit no warnings.
"""

from __future__ import annotations

import collections
import difflib
import html
import json
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field

WORD_GLUE_GAP = 0.8
WRAP = 79
TERMINAL = tuple(".:;!?…)»”\"")      # a paragraph ending in one of these is complete
# Table-of-contents dot leaders: 4+ dots ("PREÀMBUL........5"; an ellipsis has
# 3), or 3 dots between a space and the page number ending the line, where a
# long entry left room for no more ("veterinaria social ...19").
DOT_LEADER = re.compile(r"\.{4,}|(?<= )\.{3}(?=\d+[ \t]*$)", re.M)


class ConvertError(RuntimeError):
    pass


def run(cmd: list[str], text=True) -> str:
    if not shutil.which(cmd[0]):
        raise ConvertError(f"{cmd[0]} not found: install poppler-utils")
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode:
        raise ConvertError(f"{' '.join(cmd)} failed: {p.stderr.decode(errors='replace')[:300]}")
    return p.stdout.decode("utf-8") if text else p.stdout


def pdftotext_version() -> str:
    if not shutil.which("pdftotext"):
        raise ConvertError("pdftotext not found: install poppler-utils")
    v = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
    return (v.stderr or v.stdout).splitlines()[0]


# ===================================================================== model
@dataclass
class Font:
    family: str
    size: int
    color: str
    b: bool = False           # pdftohtml <b> / <i> on the run
    i: bool = False

    @property
    def bold(self) -> bool:
        return self.b or bool(re.search(r"Bold|Black|Heavy|Ultra|Medium|Semi|Demi", self.family))

    @property
    def italic(self) -> bool:
        return self.i or bool(re.search(r"Italic|Oblique", self.family))

    @property
    def name(self) -> str:
        return re.sub(r"^[A-Z]{6}\+", "", self.family)

    def __str__(self):
        return f"{self.name}/{self.size}/{self.color}"


NOFONT = Font("?", 0, "#000000")


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    fonts: list = field(default_factory=list)     # one Font per character


@dataclass
class Line:
    words: list[Word]

    @property
    def x0(self): return self.words[0].x0
    @property
    def x1(self): return self.words[-1].x1
    @property
    def y0(self): return min(w.y0 for w in self.words)
    @property
    def y1(self): return max(w.y1 for w in self.words)

    @property
    def text(self):
        out, prev = "", None
        for w in self.words:
            if prev is not None and w.x0 - prev.x1 >= WORD_GLUE_GAP:
                out += " "
            out += w.text
            prev = w
        return out


@dataclass(eq=False)
class Block:
    page: int
    lines: list[Line]
    index: int = 0
    role: str = ""
    data: dict = field(default_factory=dict)

    @property
    def x0(self): return min(l.x0 for l in self.lines)
    @property
    def x1(self): return max(l.x1 for l in self.lines)
    @property
    def y0(self): return min(l.y0 for l in self.lines)
    @property
    def y1(self): return max(l.y1 for l in self.lines)
    @property
    def h(self): return self.lines[0].y1 - self.lines[0].y0      # first line height ~ font size

    def chars(self, keep_hyphens=False) -> list[tuple[str, Font]]:
        """Text with one font per character, built like plain pdftotext:
        lines joined with a space; a hyphen ending a line is dropped and
        the line joined to the next one (with ``keep_hyphens``, for PDFs
        that are not hyphenated, the hyphen stays: "higiénico-sanitarias")."""
        out: list[tuple[str, Font]] = []
        for i, ln in enumerate(self.lines):
            if out:
                if out[-1][0] == "-" and i > 0:
                    if not keep_hyphens:
                        out.pop()
                elif out[-1][0] == "\u2010":           # layout hyphen (U+2010) at a line end
                    out.pop()
                else:
                    out.append((" ", out[-1][1]))
            prev = None
            for w in ln.words:
                if prev is not None and w.x0 - prev.x1 >= WORD_GLUE_GAP:
                    out.append((" ", w.fonts[0] if w.fonts else NOFONT))
                out += list(zip(w.text, w.fonts or [NOFONT] * len(w.text)))
                prev = w
        return out

    @property
    def text(self) -> str:
        return "".join(c for c, _ in self.chars())

    @property
    def font(self) -> Font:
        """Most frequent font of the block (by characters)."""
        c = collections.Counter((f.family, f.size, f.color) for ch, f in self.chars() if not ch.isspace())
        if not c:
            return NOFONT
        return Font(*c.most_common(1)[0][0])

    def fonts(self) -> set:
        return {str(f) for ch, f in self.chars() if not ch.isspace()}

    def __repr__(self):
        return f"<p{self.page} #{self.index} {self.role or '?'} x{self.x0:.0f} y{self.y0:.0f} {self.font} {self.text[:50]!r}>"


# ================================================================ extraction
@dataclass
class BBoxLine:
    x0: float                 # the <line> box as poppler gives it
    y0: float
    x1: float
    y1: float
    words: list[tuple]        # (x0, y0, x1, y1, text); text may be whitespace (tabs)


@dataclass
class BBoxBlock:
    page: int                 # 1-based
    flow: int                 # 0-based flow index within the page
    lines: list[BBoxLine]


@dataclass
class BBoxPage:
    width: float
    height: float
    blocks: list[BBoxBlock]


_NUM = r'"(-?[\d.]+)"'
_BOX = rf'xMin={_NUM} yMin={_NUM} xMax={_NUM} yMax={_NUM}'


def read_bbox(pdf: str) -> list[BBoxPage]:
    """``pdftotext -bbox-layout``: pages -> flows -> blocks -> lines -> words,
    with positions, in plain pdftotext's reading order. Nothing is filtered."""
    xhtml = run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", pdf, "-"])
    pages = []
    for pno, pm in enumerate(re.finditer(rf'<page width={_NUM} height={_NUM}>(.*?)</page>', xhtml, re.S), 1):
        blocks = []
        for fno, fm in enumerate(re.finditer(r"<flow>(.*?)</flow>", pm.group(3), re.S)):
            for bm in re.finditer(r"<block [^>]*>(.*?)</block>", fm.group(1), re.S):
                lines = []
                for lm in re.finditer(rf"<line {_BOX}>(.*?)</line>", bm.group(1), re.S):
                    words = [(*map(float, wm.groups()[:4]), html.unescape(wm.group(5)))
                             for wm in re.finditer(rf"<word {_BOX}>(.*?)</word>", lm.group(5), re.S)]
                    lines.append(BBoxLine(*map(float, lm.groups()[:4]), words))
                blocks.append(BBoxBlock(pno, fno, lines))
        pages.append(BBoxPage(float(pm.group(1)), float(pm.group(2)), blocks))
    if not pages:
        raise ConvertError("pdftotext -bbox-layout returned no pages")
    return pages


def extract(pdf: str) -> list[Block]:
    """Blocks of words (whitespace-only words dropped) with their fonts."""
    blocks: list[Block] = []
    for page in read_bbox(pdf):
        for bb in page.blocks:
            lines = [Line([Word(*w) for w in bl.words if w[4].strip()]) for bl in bb.lines]
            lines = [ln for ln in lines if ln.words]
            if lines:
                blocks.append(Block(bb.page, lines, len(blocks)))
    if not blocks:
        raise ConvertError("no text in the PDF")
    apply_fonts(pdf, blocks)
    return blocks


def apply_fonts(pdf: str, blocks: list[Block]) -> None:
    xml = run(["pdftohtml", "-xml", "-i", "-zoom", "1", "-stdout", pdf])
    fonts = {m.group(1): Font(m.group(3), int(m.group(2)), m.group(4)) for m in re.finditer(
        r'<fontspec id="(\d+)" size="(-?\d+)" family="([^"]+)" color="([^"]+)"', xml)}
    runs = collections.defaultdict(list)
    for pm in re.finditer(r'<page number="(\d+)".*?</page>', xml, re.S):
        p = int(pm.group(1))
        for t in re.finditer(r'<text top="(-?\d+)" left="(-?\d+)" width="(\d+)" height="(\d+)" '
                             r'font="(\d+)">(.*?)</text>', pm.group(0), re.S):
            top, left, width, height, f, txt = t.groups()
            font = fonts[f]
            if "<b>" in txt or "<i>" in txt:
                font = Font(font.family, font.size, font.color, "<b>" in txt, "<i>" in txt)
            txt = html.unescape(re.sub(r"<[^>]+>", "", txt))
            runs[p].append((int(top), int(left), int(left) + int(width), int(height), font, txt))
    for b in blocks:
        rs = runs[b.page]
        for ln in b.lines:
            for w in ln.words:
                over = sorted((r for r in rs if r[1] <= w.x1 - 0.5 and r[2] >= w.x0 + 0.5
                               and r[0] - 2 <= (w.y0 + w.y1) / 2 <= r[0] + r[3] + 2),
                              key=lambda r: r[1])
                fs = {(r[4].family, r[4].size, r[4].color, r[4].bold) for r in over}
                if len(fs) == 1:
                    w.fonts = [over[0][4]] * len(w.text)
                    continue
                if not over:
                    w.fonts = [NOFONT] * len(w.text)
                    continue
                stream, sf = "", []
                for r in over:
                    s = r[5].replace(" ", "")
                    stream += s
                    sf += [r[4]] * len(s)
                k = stream.find(w.text)
                if k < 0:   # fall back to the run covering most of the word
                    best = max(over, key=lambda r: min(r[2], w.x1) - max(r[1], w.x0))
                    w.fonts = [best[4]] * len(w.text)
                else:
                    w.fonts = sf[k:k + len(w.text)]


def font_table(blocks: list[Block]) -> list[tuple[str, int, str]]:
    """Fonts with character counts and an example: for exploring a document."""
    c, ex = collections.Counter(), {}
    for b in blocks:
        for ch, f in b.chars():
            if not ch.isspace():
                c[str(f)] += 1
                ex.setdefault(str(f), f"p{b.page} {b.text[:50]!r}")
    return [(k, n, ex[k]) for k, n in c.most_common()]


# ===================================================================== RST
_ENUMERATOR = re.compile(r"^(\(?)(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+|#)([.)])(\s|$)")
_OTHER = re.compile(r"^(?:[-+*•‣⁃](?:\s|$)|\.\.(?:\s|$)|:|>>>|\||=+$|-+$)")
NBSP = "\x00"     # protects "\ " (escaped space) from line wrapping
KEEP_SPACE = "\x01"   # a plain space that must not become a line break


def esc_inline(s: str, extra: str = "") -> str:
    """Escape the characters that start inline markup (plus ``extra``)."""
    return re.sub(r"([\\*`|_" + re.escape(extra) + "])", r"\\\1", s)


def esc_start(s: str) -> str:
    """Escape a paragraph start RST would read as a list, comment, etc."""
    m = _ENUMERATOR.match(s)
    if m:
        pos = 0 if m.group(1) else m.start(2)
        return s[:pos] + "\\" + s[pos:]
    if _OTHER.match(s):
        return "\\" + s
    return s


def inline(chars: list[tuple[str, Font]], strong=lambda f: f.bold, emph=lambda f: False) -> str:
    """Characters with fonts -> RST inline text, **strong** and *emphasis*
    for runs whose font matches. Runs that start or end inside a word are
    attached with escaped spaces (``\\ ``)."""
    def style(f):
        return "**" if strong(f) else "*" if emph(f) else ""
    runs: list[list] = []
    for c, f in chars:
        s = style(f) if not c.isspace() else None
        if runs and (s is None or runs[-1][1] == s):
            runs[-1][0].append(c)
        else:
            runs.append([[c], s if s is not None else ""])
    out = ""
    for i, (cs, mark) in enumerate(runs):
        s = "".join(cs)
        if not mark or not s.strip():
            out += esc_inline(s)
            continue
        core, trail = s.rstrip(), s[len(s.rstrip()):]
        nxt = runs[i + 1][0][0] if i + 1 < len(runs) else ""
        opener = mark if (not out or out[-1].isspace()) else NBSP + mark
        closer = mark if (trail or not nxt or nxt.isspace() or nxt in ".,;:!?)»”’'\"") else mark + NBSP
        out += opener + esc_inline(core) + closer + trail
    return out


def wrap(text: str, indent: str = "", first: str | None = None) -> list[str]:
    first = indent if first is None else first
    # no line may end in "-": pandoc joins it to the next line ("Científicas -"
    # + "CSIC" -> "-CSIC"), so the space after a hyphen is not a break point,
    # nor the space before a lone "-" (a line starting "- " reads as a list)
    text = text.replace(" - ", KEEP_SPACE + "- ").replace("- ", "-" + KEEP_SPACE)
    lines = textwrap.wrap(text, WRAP, initial_indent=first, subsequent_indent=indent,
                          break_long_words=False, break_on_hyphens=False)
    if any(re.fullmatch(r"\s*\W+", l.replace(NBSP, "").replace(KEEP_SPACE, " ")) or re.match(r"\s*\.\.", l)
           for l in lines[1:]):
        lines = [first + text]
    return [l.replace(NBSP, "\\ ").replace(KEEP_SPACE, " ") for l in lines] or [first.rstrip()]


def heading(text: str, ch: str, over=False, escape=True) -> list[str]:
    text = esc_inline(text) if escape else text
    bar = ch * len(text)
    return ([bar] if over else []) + [text, bar, ""]


def list_table(rows: list[list[str]], indent="", header=False, widths=None) -> list[str]:
    """``.. list-table::`` with cells already in RST inline form."""
    out = [f"{indent}.. list-table::"]
    if header:
        out.append(f"{indent}   :header-rows: 1")
    if widths:
        out.append(f"{indent}   :widths: {' '.join(map(str, widths))}")
    out.append("")
    for row in rows:
        for k, cell in enumerate(row):
            lead = f"{indent}   * - " if k == 0 else f"{indent}     - "
            cell = cell if cell.strip() else ""
            out += wrap(cell, " " * len(lead), lead) if cell else [lead.rstrip()]
    return out + [""]


def comment(text: str, indent="") -> list[str]:
    return [f"{indent}.. {text}", ""]


def comment_blocks(blocks) -> list[str]:
    """Blocks (already in the wanted order) as one RST comment: page
    furniture kept in the file but not rendered."""
    return comment(" ".join(b.text for b in blocks)) if blocks else []


def chars_of(*blocks) -> list[tuple[str, Font]]:
    """Characters of several blocks, joined with a space."""
    out = []
    for b in blocks:
        if out:
            out.append((" ", NOFONT))
        out += b.chars()
    return out


# ============================================================== verification
ADORN = re.compile(r"^([#*=\-~^\"'+])\1+$")
LIST_ITEM = re.compile(r"^(\s*)(?:[*+-]|\d+\.|#\.)\s+")
TABLE_CELL = re.compile(r"^(\s*)(?:\* - |  - |- )")


def rst_words(rst: str) -> tuple[list[str], list[bool]]:
    """Words of an RST file written by these converters, with the markup they
    emit stripped: adornments, list markers ('* ', '- ', and the '* -'/'-' of
    list-tables), directive lines, line-block bars, escapes, ** and *.
    Returns (words, in_comment flags). Enumerated list numbers ("12. ") are
    kept: in these documents they are text of the PDF."""
    words, flags = [], []
    depth = None                      # indentation of the open comment
    for line in rst.split("\n"):
        st = line.lstrip()
        ind = len(line) - len(st)
        if depth is not None:
            if not st or ind > depth:
                if st:
                    for w in st.split():
                        words.append(w)
                        flags.append(True)
                continue
            depth = None
        if not st or ADORN.match(st) or st in ("..", "-", "* -"):   # separator / empty table cell
            continue
        if st.startswith(".. "):
            if re.match(r"\.\. [\w-]+::", st):          # directive (list-table, ...)
                arg = re.sub(r"^\.\. [\w-]+::\s*", "", st)
                if arg and not st.startswith(".. list-table::"):
                    for w in clean_inline(arg).split():
                        words.append(w)
                        flags.append(False)
                continue
            depth = ind
            for w in st[3:].split():
                words.append(w)
                flags.append(True)
            continue
        if re.match(r":[\w-]+:", st):                     # directive option
            continue
        if st.startswith("| "):
            st = st[2:]
        st = re.sub(r"^\* - ", "", st) if st.startswith("* - ") else st
        st = re.sub(r"^- ", "", st) if st.startswith("- ") else st
        st = re.sub(r"^\* ", "", st) if st.startswith("* ") else st
        for w in clean_inline(st).split():
            words.append(w)
            flags.append(False)
    return words, flags


def clean_inline(s: str) -> str:
    s = s.replace("\\ ", "")
    s = re.sub(r"(?<!\\)\*\*", "", s)
    s = re.sub(r"(?<![\\\w])\*(?=\S)|(?<=\S)(?<!\\)\*(?![\w*])", "", s)
    return re.sub(r"\\(.)", r"\1", s)


_ROMAN = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
_INLINE = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
           "SmallCaps", "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math",
           "RawInline", "Link", "Image", "Note", "Span"}


def enum_label(n: int, style: str, delim: str) -> str:
    """How a reader sees item n of a pandoc OrderedList: "3.", "c)", "(iv)"..."""
    def roman(k):
        out = ""
        for v, r in _ROMAN:
            while k >= v:
                out, k = out + r, k - v
        return out
    lab = {"LowerAlpha": lambda: chr(96 + n), "UpperAlpha": lambda: chr(64 + n),
           "LowerRoman": lambda: roman(n), "UpperRoman": lambda: roman(n).upper()
           }.get(style, lambda: str(n))()
    return f"({lab})" if delim == "TwoParens" else f"{lab})" if delim == "OneParen" else f"{lab}."


@dataclass
class PandocText:
    words: list[str]                 # what a reader sees, comments excluded
    headers: list[tuple[int, str]]   # (level, text)
    markers: list[str]               # ordered-list item numbers, in order
    warnings: str                    # pandoc's stderr


def pandoc_ast(rst: str) -> tuple[dict, str]:
    """pandoc's JSON AST of the RST, and pandoc's warnings (stderr)."""
    p = subprocess.run(["pandoc", "-f", "rst", "-t", "json"], input=rst.encode(), capture_output=True)
    if p.returncode:
        raise ConvertError("pandoc: " + p.stderr.decode(errors="replace")[:300])
    return json.loads(p.stdout), p.stderr.decode(errors="replace").strip()


def pandoc_read(rst: str, list_labels: bool = True) -> PandocText:
    """Parse the RST with pandoc and return what a reader sees (see ast_text)."""
    doc, warnings = pandoc_ast(rst)
    return ast_text(doc, list_labels, warnings)


def ast_text(doc: dict, list_labels: bool = True, warnings: str = "") -> PandocText:
    """Words of a pandoc AST. Block elements always separate words; comments
    are not in the AST; ordered-list numbers are not text in pandoc's AST, so
    they are rebuilt from the list attributes (``list_labels=False`` leaves
    them out of ``words``; they are always in ``markers``)."""
    buf: list[str] = []
    headers, markers = [], []

    def walk(n):
        if isinstance(n, list):
            inline = all(isinstance(x, dict) and x.get("t") in _INLINE for x in n)
            for x in n:
                walk(x)
                if not inline:
                    buf.append(" ")
        elif isinstance(n, dict):
            t, c = n.get("t"), n.get("c")
            if t == "Str":
                buf.append(c)
            elif t in ("Space", "SoftBreak", "LineBreak"):
                buf.append(" ")
            elif t in ("Code", "CodeBlock", "RawInline", "RawBlock", "Math"):
                buf.append(f" {c[-1]} ")
            elif t == "Header":
                start = len(buf)
                walk(c[2])
                headers.append((c[0], "".join(buf[start:]).strip()))
                buf.append(" ")
            elif t == "OrderedList":
                (start, style, delim), items = c
                for k, item in enumerate(items):
                    markers.append(str(start + k))
                    if list_labels:
                        buf.append(f" {enum_label(start + k, style['t'], delim['t'])} ")
                    walk(item)
                    buf.append(" ")
            elif t == "MetaString":
                buf.append(f" {c} ")
            elif t in ("Link", "Image", "Span", "Quoted", "Cite"):
                walk(c[1])           # the inline content only: it joins what follows ("url.")
            elif c is not None:
                walk(c)
                if t not in _INLINE:
                    buf.append(" ")

    for key in ("title", "subtitle"):
        if key in doc.get("meta", {}):
            walk(doc["meta"][key])
            buf.append(" ")
    walk(doc["blocks"])
    return PandocText("".join(buf).split(), headers, markers, warnings)


def pandoc_words(rst: str) -> tuple[list[str], str]:
    r = pandoc_read(rst)
    return r.words, r.warnings


class Furniture:
    """Declared page furniture: text of the PDF the converter leaves out.
    ``lines`` are regexes matched against whole lines of plain pdftotext
    (running headers, footers, page numbers); ``leaders`` sets aside table
    of contents dot leaders. The verification removes exactly these from the
    reference, and nothing else."""

    def __init__(self, lines=(), leaders=False, page_lines=None, tokens=None):
        self.lines = [re.compile(r) for r in lines]
        self.leaders = leaders
        self.tokens = re.compile(tokens) if tokens else None    # e.g. bullet glyphs
        self.page_lines = page_lines or {}      # {page: [regex]} for one-off lines

    def drop(self, page: int, line: str) -> bool:
        s = " ".join(line.split())
        return any(r.fullmatch(s) for r in self.lines) or \
            any(re.fullmatch(r, s) for r in self.page_lines.get(page, []))


def reference_words(raw: str, furniture: Furniture) -> tuple[list[str], collections.Counter]:
    words, removed = [], collections.Counter()
    for pno, page in enumerate(raw.split("\f"), 1):
        for line in page.split("\n"):
            if not line.strip():
                continue
            if furniture.drop(pno, line):
                removed["page furniture lines"] += 1
                continue
            if furniture.leaders and re.search(r"\.{4,}", line):
                removed["dot leaders"] += 1
                line = re.sub(r"\.{4,}", " ", line)
            for w in line.split():
                if furniture.tokens and furniture.tokens.fullmatch(w):
                    removed["bullet glyphs (RST list markers instead)"] += 1
                    continue
                words.append(w)
    return words, removed


def verify(rst: str, raw: str, furniture: Furniture, report: list[str],
           moved_ok: list[list[str]] = ()) -> bool:
    """Word-by-word comparison of the RST with plain pdftotext."""
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        report.append("  FAIL: " + msg)

    out_w, flags = rst_words(rst)
    ref_w, removed = reference_words(raw, furniture)
    report.append("[1] word sequence vs plain pdftotext")
    for k, v in sorted(removed.items()):
        report.append(f"  set aside in the PDF text: {k}: {v}")
    sm = difflib.SequenceMatcher(a=ref_w, b=out_w, autojunk=False)
    moves = [list(m) for m in moved_ok]
    pending_del, pending_ins = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        A, B = ref_w[i1:i2], out_w[j1:j2]
        if A and B and "".join(A) == "".join(B):
            continue
        if A:
            pending_del.append((i1, A))
        if B:
            pending_ins.append((j1, B))
    # blocks the converter moved on purpose (declared in moved_ok)
    for mv in moves:
        d = next((x for x in pending_del if x[1] == mv), None)
        i = next((x for x in pending_ins if x[1] == mv), None)
        if d and i:
            pending_del.remove(d)
            pending_ins.remove(i)
            report.append(f"  moved (declared): {' '.join(mv)[:80]!r}")
    for i1, A in pending_del:
        fail(f"missing from the RST after …{' '.join(ref_w[max(0, i1 - 5):i1])!r}: {A[:12]!r}")
    for j1, B in pending_ins:
        fail(f"not in the PDF after …{' '.join(out_w[max(0, j1 - 5):j1])!r}: {B[:12]!r}")
    report.append(f"  PDF words {len(ref_w)}, RST words {len(out_w)}")
    if ok:
        report.append("  identical: nothing altered, omitted, added or reordered")

    report.append("[2] character inventory (non-whitespace)")
    cr, co = collections.Counter("".join(ref_w)), collections.Counter("".join(out_w))
    if cr != co:
        fail(f"only in PDF: {dict((cr - co).most_common(8))}; only in RST: {dict((co - cr).most_common(8))}")
    else:
        report.append(f"  identical ({sum(co.values())} characters)")

    report.append("[3] pandoc reading of the RST")
    try:
        pw, warn = pandoc_words(rst)
    except ConvertError as e:
        fail(str(e))
        return ok
    expect = [w for w, c in zip(out_w, flags) if not c]
    if warn:
        fail("pandoc warnings: " + warn[:300])
    if pw != expect:
        k = next((i for i, (a, b) in enumerate(zip(expect, pw)) if a != b), min(len(pw), len(expect)))
        fail(f"pandoc reads other words at word {k}: {expect[k:k + 8]!r} vs {pw[k:k + 8]!r}")
    else:
        report.append(f"  pandoc reads the same {len(pw)} words"
                      + (f" ({sum(flags)} more are in RST comments)" if any(flags) else ""))
    return ok


def layout_hyphen_breaks(pdf: str) -> list[tuple[str, str]]:
    """Words printed with a hyphen at the end of a line, as ``pdftotext
    -layout`` shows them (it keeps the hyphen; plain pdftotext drops it and
    glues the halves): [("promueve-un-", "dialogo-plural-..."), ...]."""
    layout = run(["pdftotext", "-layout", "-enc", "UTF-8", pdf, "-"])
    lines = [l for l in re.split(r"[\n\f]", layout) if l.strip()]
    out = []
    for a, b in zip(lines, lines[1:]):
        if re.search(r"\S-\s*$", a):
            out.append((a.split()[-1], b.split()[0]))
    return out


def raw_units(raw: str, join_u2010=True) -> list[str]:
    """Lines of plain pdftotext, each as a whitespace-free string; U+2010
    layout hyphens at line ends joined to the next line."""
    if join_u2010:
        raw = re.sub("\u2010[ \t]*\n", "", raw)
    units = []
    for line in re.split(r"[\n\f]", raw):
        u = "".join(line.split())
        if u:
            units.append(u)
    return units


def verify_blocks(rst: str, raw: str, report: list[str], join_u2010=True,
                  hyphen_breaks: list[tuple[str, str]] = (), strip_start: str | tuple = "",
                  skip_unreadable: bool = False, leaders: bool = False) -> bool:
    """For documents whose reading order the converter rebuilds: the RST text
    (whitespace aside) must be exactly the lines of plain pdftotext, each
    used once and kept whole, in any order. Nothing is set aside: running
    headers and footers are kept in RST comments."""
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        report.append("  FAIL: " + msg)

    out_w, flags = rst_words(rst)
    S = "".join(out_w)
    n_leaders = 0
    if leaders:                  # table-of-contents dot leaders ("Vivienda ......... 23")
        raw, n_leaders = DOT_LEADER.subn("", raw)
    # List glyphs starting a line: RST list markers instead. A glyph given
    # with a trailing space ("- ") counts only when a space follows it, so a
    # dash that is text ("-como en los casos del muflón...") stays.
    glyphs = (strip_start,) if isinstance(strip_start, str) else strip_start
    set_aside = []
    for glyph in (g for g in glyphs if g.endswith(" ")):
        raw, n = re.subn(r"(?m)^([ \t\f]*)" + re.escape(glyph.strip()) + r"[ \t]+", r"\1", raw)
        set_aside.append((glyph, n))
    units = raw_units(raw, join_u2010)
    report.append("[1] the RST is the PDF's lines, each once and whole, in any order")
    for glyph in (g for g in glyphs if g and not g.endswith(" ")):
        n = sum(u.startswith(glyph) for u in units)
        units = [u[len(glyph):] if u.startswith(glyph) else u for u in units]
        units = [u for u in units if u]
        set_aside.append((glyph, n))
    for glyph, n in sorted(set_aside, key=lambda g: glyphs.index(g[0])):
        report.append(f"  set aside: {glyph!r} starting a line (RST list markers instead): {n}")
    if leaders:
        report.append(f"  set aside: table-of-contents dot leaders: {n_leaders}")
    if skip_unreadable:          # glyphs of a font without Unicode mapping: control characters only
        import unicodedata
        bad = [u for u in units if all(unicodedata.category(c) == "Cc" for c in u)]
        units = [u for u in units if u not in bad]
        report.append(f"  set aside: lines of unreadable glyphs (font without Unicode mapping): {len(bad)}")
    if join_u2010:
        report.append(f"  layout hyphens (U+2010) joined at line ends: {len(re.findall(chr(0x2010) + r'[ \t]*\n', raw))}")
    # Real hyphens printed at a line end (shown by pdftotext -layout) that
    # plain pdftotext dropped: the RST keeps them; compare without them.
    flat_raw = "".join(raw.split())
    kept = []
    for left, right in hyphen_breaks:
        rst_form, pdf_form = left + right, left[:-1] + right
        if rst_form in S and pdf_form in flat_raw and rst_form not in flat_raw:
            S = S.replace(rst_form, pdf_form, 1)
            kept.append(rst_form)
        elif rst_form in S and sum(u.count(pdf_form) for u in units) == 1:
            # the hyphenated form is also printed inside a line elsewhere: put
            # the hyphen back on the PDF side, where the glued form is unique
            k = next(i for i, u in enumerate(units) if pdf_form in u)
            units[k] = units[k].replace(pdf_form, rst_form)
            kept.append(rst_form)
    if hyphen_breaks:
        report.append(f"  real hyphens kept at line ends (plain pdftotext drops them): {len(kept)}")
        report += [f"    {k[:90]}" for k in kept]
    covered = bytearray(len(S))
    pos = {}

    def free_occurrences(u):
        out, start = [], 0
        while True:
            k = S.find(u, start)
            if k < 0:
                return out
            if not any(covered[k:k + len(u)]):
                out.append(k)
            start = k + 1

    def place(i, k):
        covered[k:k + len(units[i])] = b"\x01" * len(units[i])
        pos[i] = k

    # 1) lines with a single possible place, repeatedly (longest first)
    todo = sorted(range(len(units)), key=lambda i: -len(units[i]))
    progress = True
    while progress:
        progress = False
        for i in list(todo):
            occ = free_occurrences(units[i])
            if len(occ) == 1:
                place(i, occ[0])
                todo.remove(i)
                progress = True
            elif not occ:
                todo.remove(i)
    # 2) ambiguous lines: the place nearest to where their neighbours went
    for i in sorted((i for i in range(len(units)) if i not in pos), key=lambda i: -len(units[i])):
        occ = free_occurrences(units[i])
        if not occ:
            continue
        prev = next((pos[j] + len(units[j]) for j in range(i - 1, -1, -1) if j in pos), 0)
        place(i, min(occ, key=lambda k: abs(k - prev)))
    for i in range(len(units)):
        if i not in pos:
            fail(f"PDF line missing or altered in the RST: {units[i][:70]!r}")
    gaps = [m for m in re.finditer(rb"\x00+", bytes(covered))]
    for m in gaps[:10]:
        fail(f"RST text not in the PDF: {S[m.start():m.end()][:70]!r}")
    if len(gaps) > 10:
        fail(f"... {len(gaps) - 10} more")
    # how much of the original order is kept (longest increasing subsequence)
    seq = [pos[i] for i in range(len(units)) if i in pos]
    import bisect
    tails = []
    for x in seq:
        j = bisect.bisect_left(tails, x)
        tails[j:j + 1] = [x]
    report.append(f"  {len(units)} PDF lines, {len(S)} characters; "
                  f"{len(tails)} kept in pdftotext order, {len(seq) - len(tails)} regrouped by layout")
    if ok:
        report.append("  identical text: nothing altered, omitted or added")

    report.append("[2] pandoc reading of the RST")
    try:
        pw, warn = pandoc_words(rst)
    except ConvertError as e:
        fail(str(e))
        return ok
    expect = [w for w, c in zip(out_w, flags) if not c]
    if warn:
        fail("pandoc warnings: " + warn[:300])
    if pw != expect:
        k = next((i for i, (a, b) in enumerate(zip(expect, pw)) if a != b), min(len(pw), len(expect)))
        fail(f"pandoc reads other words at word {k}: {expect[k:k + 8]!r} vs {pw[k:k + 8]!r}")
    else:
        report.append(f"  pandoc reads the same {len(pw)} words ({sum(flags)} more are in RST comments)")
    return ok


def main(convert, verify_fn, doc_name: str, argv=None, default_output="out.rst") -> int:
    """Shared command line: PDF -o OUT.rst [--report R] [--verify-only RST]."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=f"Convert {doc_name} (PDF) to reStructuredText and verify it")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", default=default_output)
    ap.add_argument("--report")
    ap.add_argument("--verify-only", metavar="RST")
    args = ap.parse_args(argv)
    report: list[str] = []
    try:
        report.append("extract : " + pdftotext_version())
        if args.verify_only:
            rst = open(args.verify_only, encoding="utf-8").read()
        else:
            rst = convert(args.pdf, report)
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(rst)
            report.insert(0, f"convert : wrote {args.output} ({rst.count(chr(10))} lines)")
        raw = run(["pdftotext", "-enc", "UTF-8", args.pdf, "-"])
        report.append("verify  :")
        import inspect
        extra = {"pdf": args.pdf} if "pdf" in inspect.signature(verify_fn).parameters else {}
        ok = verify_fn(rst, raw, report, **extra)
    except ConvertError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report.append("RESULT: " + ("ALL CHECKS PASSED" if ok else "PROBLEMS FOUND"))
    out = "\n".join(report) + "\n"
    sys.stdout.write(out)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(out)
    return 0 if ok else 1
