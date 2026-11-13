"""
pdf2rst_common.py - shared code for the <party>-pdf2rst.py converters.

Used by salf, partido_humanista, pnv, por_un_mundo_mas_justo, frente_obrero,
iustitia_europa, vox, pacma, teruel_existe and soria_ya (the whole engine:
extraction with fonts, RST writing, verification and the command line), and
in part by bng, erc and coalicion_canaria (``read_bbox``, ``run``, RST
escaping/wrapping, ``pandoc_read``; erc also ``inline`` and ``main``).
build.py uses the pandoc reader (``pandoc_ast``/``ast_text``), the
``pdftotext -layout`` evidence of real line-end hyphens
(``layout_hyphen_breaks``) and ``visible_pdf`` (text hidden by a page copy
drawn through a window). pp and adelante_andalucia are self-contained.

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
            # one font per character: a run may be bold only in part
            # ("<b>Exigir contrapartidas. </b>Todos los proyectos") when the
            # regular and bold faces share a family name
            chars, b, i = [], False, False
            for piece in re.split(r"(</?[bi]>)", txt):
                if piece in ("<b>", "</b>"):
                    b = piece == "<b>"
                    continue
                if piece in ("<i>", "</i>"):
                    i = piece == "<i>"
                    continue
                for c in html.unescape(re.sub(r"<[^>]+>", "", piece)):
                    chars.append((c, Font(font.family, font.size, font.color, b, i) if b or i else font))
            text = "".join(c for c, _ in chars)
            cf = [f for c, f in chars if c != " "]
            if "<b>" in txt or "<i>" in txt:
                font = Font(font.family, font.size, font.color, "<b>" in txt, "<i>" in txt)
            runs[p].append([int(top), int(left), int(left) + int(width), int(height), font, text, cf, 0])
    for b in blocks:
        rs = runs[b.page]
        for ln in b.lines:
            for w in ln.words:
                over = sorted((r for r in rs if r[1] <= w.x1 - 0.5 and r[2] >= w.x0 + 0.5
                               and r[0] - 2 <= (w.y0 + w.y1) / 2 <= r[0] + r[3] + 2),
                              key=lambda r: r[1])
                fs = {(f.family, f.size, f.color, f.bold, f.italic) for r in over for f in r[6]}
                if len(fs) == 1:
                    w.fonts = [next(f for r in over for f in r[6])] * len(w.text)
                    continue
                if not over:
                    w.fonts = [NOFONT] * len(w.text)
                    continue
                if len(over) == 1:           # a run bold in part: the word's place in it
                    r = over[0]
                    s = r[5].replace(" ", "")
                    k = s.find(w.text, r[7])
                    if k >= 0:
                        w.fonts, r[7] = r[6][k:k + len(w.text)], k + len(w.text)
                        continue
                stream, sf = "", []
                for r in over:
                    s = r[5].replace(" ", "")
                    stream += s
                    sf += r[6]
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


# ---------------------------------------------------------------------------
# Pages stamped through a window (hidden text)
#
# A page can carry a copy of another whole page drawn through a small window
# (what PyMuPDF's ``show_pdf_page`` writes): a Form XObject with a small
# /BBox (the window) and a /Matrix, whose only content is "/name Do", the
# copied page as an inner Form. Only what falls inside the window shows (a
# logo, say), but pdftotext and pdftohtml ignore that clipping and print all
# the copied page's text, mixed into the page's own lines. ``visible_pdf``
# gives a copy of the PDF with those windows emptied (an incremental update:
# the original bytes stay as they are), after checking that the text inside
# each window is the same with and without it, i.e. that only hidden text
# goes. Only for PDFs with classic xref tables; anything else is left as is.

class _Unsupported(Exception):
    pass


class _PdfFile:
    """Just enough of a PDF reader to find stamped windows: classic xref
    tables (with /Prev), plain objects, Flate streams."""

    REF = rb"(\d+)\s+(\d+)\s+R"

    def __init__(self, data: bytes):
        self.data = data
        m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
        if not m:
            raise _Unsupported("no startxref")
        self.offsets: dict[int, int] = {}
        self.trailer = None
        pos, seen = int(m.group(1)), set()
        while pos is not None and pos not in seen:
            seen.add(pos)
            if not data.startswith(b"xref", pos):
                raise _Unsupported("xref stream")
            end = data.index(b"trailer", pos)
            table = data[pos + 4:end]
            for sm in re.finditer(rb"(\d+)\s+(\d+)\s*[\r\n]+((?:\d{10} \d{5} [nf][ \r\n]{1,2})*)", table):
                first = int(sm.group(1))
                for i, em in enumerate(re.finditer(rb"(\d{10}) \d{5} ([nf])", sm.group(3))):
                    if em.group(2) == b"n":
                        self.offsets.setdefault(first + i, int(em.group(1)))
            tdict, _ = self._dict_at(end + len(b"trailer"))
            if self.trailer is None:
                self.trailer = tdict
            prev = re.search(rb"/Prev\s+(\d+)", tdict)
            pos = int(prev.group(1)) if prev else None

    def _dict_at(self, i: int) -> tuple[bytes, int]:
        """The << ... >> starting at or after i, and the index after it."""
        return self._balanced(self.data, self.data.index(b"<<", i))

    @staticmethod
    def _balanced(d: bytes, i: int) -> tuple[bytes, int]:
        """The dictionary starting at d[i] ("<<"), strings skipped, and the index after it."""
        depth, j = 0, i
        while j < len(d):
            if d.startswith(b"<<", j):
                depth, j = depth + 1, j + 2
            elif d.startswith(b">>", j):
                depth, j = depth - 1, j + 2
                if depth == 0:
                    return d[i:j], j
            elif d[j:j + 1] == b"(":                         # literal string
                k, nest = j + 1, 1
                while nest and k < len(d):
                    c = d[k:k + 1]
                    if c == b"\\":
                        k += 1
                    elif c == b"(":
                        nest += 1
                    elif c == b")":
                        nest -= 1
                    k += 1
                j = k
            else:
                j += 1
        raise _Unsupported("unterminated dictionary")

    def obj(self, num: int) -> tuple[bytes, bytes | None]:
        """(dictionary or value, decoded stream or None) of object num."""
        if num not in self.offsets:
            raise _Unsupported(f"object {num} not in the xref")
        pos = self.offsets[num]
        m = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj\s*").match(self.data, pos)
        if not m or int(m.group(1)) != num:
            raise _Unsupported(f"object {num} not at its offset")
        start = m.end()
        if not self.data.startswith(b"<<", start):
            end = self.data.index(b"endobj", start)
            return self.data[start:end].strip(), None
        d, after = self._dict_at(start)
        s = re.compile(rb"\s*stream\r?\n").match(self.data, after)
        if not s:
            return d, None
        n = re.search(rb"/Length\s+(?:" + self.REF + rb"|(\d+))", d)
        if not n:
            raise _Unsupported(f"stream {num} without /Length")
        length = int(n.group(3)) if n.group(3) else int(self.obj(int(n.group(1)))[0])
        raw = self.data[s.end():s.end() + length]
        filters = re.findall(rb"/(\w+)", (re.search(rb"/Filter\s*(\[[^\]]*\]|/\w+)", d) or [b"", b""])[1])
        import zlib
        for f in filters:
            if f != b"FlateDecode":
                raise _Unsupported(f"stream {num}: filter {f.decode()}")
            raw = zlib.decompress(raw)
        return d, raw

    def resolve(self, value: bytes) -> bytes:
        m = re.fullmatch(rb"\s*" + self.REF + rb"\s*", value)
        return self.obj(int(m.group(1)))[0] if m else value

    def entry(self, d: bytes, key: bytes) -> bytes | None:
        """The value of /key in dictionary d (dictionaries, arrays, refs, names, numbers)."""
        m = re.search(rb"/" + key + rb"(?![A-Za-z0-9])\s*", d)
        if not m:
            return None
        i = m.end()
        if d.startswith(b"<<", i):
            return self._balanced(d, i)[0]
        if d.startswith(b"[", i):
            return d[i:d.index(b"]", i) + 1]
        r = re.compile(self.REF + rb"|/[^\s/<>\[\]()]+|[-\d.]+").match(d, i)
        return r.group(0) if r else None

    def names(self, d: bytes | None) -> dict[bytes, int]:
        """/Name N 0 R pairs of a (resolved) dictionary."""
        if d is None:
            return {}
        d = self.resolve(d)
        return {m.group(1): int(m.group(2))
                for m in re.finditer(rb"/([^\s/<>\[\]()]+)\s+(\d+)\s+\d+\s+R", d)}

    def pages(self) -> list[tuple[bytes, bytes | None]]:
        """(page dictionary, inherited /Resources) in page order."""
        root = self.resolve(self.entry(self.trailer, b"Root"))
        out = []

        def walk(num, inherited, depth=0):
            d, _ = self.obj(num)
            res = self.entry(d, b"Resources") or inherited
            if re.search(rb"/Type\s*/Pages", d):
                if depth > 50:
                    raise _Unsupported("page tree too deep")
                for k in re.finditer(self.REF, self.entry(d, b"Kids") or b""):
                    walk(int(k.group(1)), res, depth + 1)
            else:
                out.append((d, res))

        walk(int(re.match(self.REF, self.entry(root, b"Pages")).group(1)), None)
        return out


def _numbers(b: bytes | None) -> list[float]:
    return [float(x) for x in re.findall(rb"-?\d*\.?\d+", b or b"")]


def _mul(m, n):
    """Matrix product m x n of PDF matrices [a b c d e f]."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F]


_CONTENT_TOKEN = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>|<<|>>|\[|\]|"
                            rb"/[^\s/<>\[\]()]+|[^\s/<>\[\]()]+")


def _window_draws(content: bytes, wanted: set[bytes]) -> list[tuple[bytes, list[float]]]:
    """(name, CTM) of each "/name Do" in a content stream, for the wanted names."""
    out, stack, ctm, operands = [], [], [1, 0, 0, 1, 0, 0], []
    for t in _CONTENT_TOKEN.findall(content):
        if t == b"q":
            stack.append(ctm)
        elif t == b"Q":
            ctm = stack.pop() if stack else ctm
        elif t == b"cm" and len(operands) >= 6:
            ctm = _mul([float(x) for x in operands[-6:]], ctm)
        elif t == b"Do" and operands and operands[-1][1:] in wanted:
            out.append((operands[-1][1:], ctm))
        elif t in (b"BI",):
            raise _Unsupported("inline image in a page with a window")
        if re.fullmatch(rb"[-+]?\d*\.?\d+|/[^\s/<>\[\]()]+", t):
            operands.append(t)
        elif t not in (b"[", b"]"):
            operands = []
    return out


def _windows(pf: _PdfFile) -> tuple[dict[int, bytes], dict[int, list[tuple[int, int, tuple]]]]:
    """Window forms {object: dictionary} and, per page (1-based), the
    windows drawn on it: (window object, copied page object, box in
    pdftotext coordinates x0, y0, x1, y1)."""
    forms: dict[int, bytes] = {}
    per_page: dict[int, list] = {}
    for pno, (pd, res) in enumerate(pf.pages(), 1):
        xobjects = pf.names(pf.entry(pf.resolve(res), b"XObject") if res else None)
        candidates = {}
        for name, num in xobjects.items():
            d, stream = pf.obj(num)
            if not re.search(rb"/Subtype\s*/Form", d) or stream is None:
                continue
            m = re.fullmatch(rb"\s*(?:q\s+)?/([^\s/<>\[\]()]+)\s+Do\s*(?:Q\s*)?", stream)
            inner = pf.names(pf.entry(pf.resolve(pf.entry(d, b"Resources") or b"<<>>"), b"XObject"))
            if m and m.group(1) in inner and re.search(rb"/Subtype\s*/Form", pf.obj(inner[m.group(1)])[0]):
                candidates[name] = (num, d, inner[m.group(1)])
        if not candidates:
            continue
        contents = pf.entry(pd, b"Contents") or b""
        stream = b"\n".join(pf.obj(int(r.group(1)))[1] or b"" for r in re.finditer(pf.REF, contents))
        mb = _numbers(pf.entry(pd, b"CropBox") or pf.entry(pd, b"MediaBox"))
        rotate = _numbers(pf.entry(pd, b"Rotate"))
        if len(mb) != 4 or rotate and rotate[0] % 360:
            raise _Unsupported("page box or rotation not understood")
        for name, ctm in _window_draws(stream, set(candidates)):
            num, d, inner = candidates[name]
            x0, y0, x1, y1 = _numbers(pf.entry(d, b"BBox"))
            mat = _numbers(pf.entry(d, b"Matrix")) or [1, 0, 0, 1, 0, 0]
            full = _mul(mat, ctm)
            pts = [(x * full[0] + y * full[2] + full[4], x * full[1] + y * full[3] + full[5])
                   for x in (x0, x1) for y in (y0, y1)]
            xs, ys = [p[0] - mb[0] for p in pts], [mb[3] - p[1] for p in pts]   # top-left origin
            forms[num] = d
            per_page.setdefault(pno, []).append((num, inner, (min(xs), min(ys), max(xs), max(ys))))
    return forms, per_page


def _page_words(pages: list[BBoxPage], pno: int, box=None) -> list[str]:
    out = []
    for b in pages[pno - 1].blocks:
        for ln in b.lines:
            for x0, y0, x1, y1, t in ln.words:
                if not t.strip():
                    continue
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if box is None or box[0] - 1 <= cx <= box[2] + 1 and box[1] - 1 <= cy <= box[3] + 1:
                    out.append(t)
    return out


import contextlib  # noqa: E402


def _without_forms(data: bytes, pf: _PdfFile, forms: dict[int, bytes]) -> bytes:
    """The PDF with the given Form XObjects replaced by empty ones (same
    /BBox), appended as an incremental update."""
    size = int(re.search(rb"/Size\s+(\d+)", pf.trailer).group(1))
    out = bytearray(data if data.endswith(b"\n") else data + b"\n")
    offsets = {}
    for num, d in sorted(forms.items()):
        bbox = pf.entry(d, b"BBox").decode("latin-1")
        offsets[num] = len(out)
        out += (f"{num} 0 obj\n<</Type/XObject/Subtype/Form/BBox{bbox}/Length 0>>\n"
                f"stream\n\nendstream\nendobj\n").encode("latin-1")
    xref = len(out)
    out += b"xref\n" + b"".join(f"{n} 1\n{o:010d} 00000 n \n".encode() for n, o in sorted(offsets.items()))
    keep = b"".join(b"/" + k + b" " + v for k in (b"Root", b"Info", b"ID")
                    if (v := pf.entry(pf.trailer, k)) is not None)
    prev = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data).group(1)
    out += b"trailer\n<</Size %d %s/Prev %s>>\nstartxref\n%d\n%%%%EOF\n" % (size, keep, prev, xref)
    return bytes(out)


@contextlib.contextmanager
def visible_pdf(pdf: str):
    """Yields (path, notes): the PDF itself, or a temporary copy with the
    page copies drawn through windows emptied (see above); notes says what
    was set aside. A window whose own text changes without it shows text:
    it is kept."""
    import os
    import tempfile
    data = open(pdf, "rb").read()
    try:
        pf = _PdfFile(data)
        forms, per_page = _windows(pf)
    except (_Unsupported, ValueError, IndexError, AttributeError, TypeError):
        forms, per_page = {}, {}
    if not forms:
        yield pdf, []
        return
    with tempfile.TemporaryDirectory(prefix="visible-") as tmp:
        path = os.path.join(tmp, os.path.basename(pdf))
        before = read_bbox(pdf)
        while True:
            with open(path, "wb") as f:
                f.write(_without_forms(data, pf, forms))
            after = read_bbox(path)
            if len(before) != len(after):
                raise ConvertError("visible copy: page count changed")
            showing = {num for pno, wins in per_page.items() for num, _, box in wins if num in forms
                       and sorted(_page_words(before, pno, box)) != sorted(_page_words(after, pno, box))}
            if not showing:
                break
            forms = {n: d for n, d in forms.items() if n not in showing}
            if not forms:
                yield pdf, []
                return
        hidden = {p: len(_page_words(before, p)) - len(_page_words(after, p))
                  for p, wins in per_page.items() if any(w[0] in forms for w in wins)}
        if not any(hidden.values()):
            yield pdf, []
            return
        notes = []
        for num in sorted(forms):
            pages = sorted(p for p, wins in per_page.items() if any(w[0] == num for w in wins))
            inner = sorted({w[1] for p in pages for w in per_page[p] if w[0] == num})
            box = next(w[2] for w in per_page[pages[0]] if w[0] == num)
            notes.append(f"{_page_ranges(pages)}: a copy of another page (object {', '.join(map(str, inner))}) "
                         f"is drawn through a {box[2] - box[0]:.0f}x{box[3] - box[1]:.0f} pt window at "
                         f"x {box[0]:.0f}-{box[2]:.0f}, y {box[1]:.0f}-{box[3]:.0f} (object {num}): only "
                         "the window shows, the text inside it is the same without it; the rest of "
                         "that page's text is hidden: set aside")
        notes.append("hidden words set aside (pdftotext words, original minus visible copy): "
                     + ", ".join(f"p{p}: {n}" for p, n in sorted(hidden.items()))
                     + f"; total {sum(hidden.values())}")
        yield path, notes


def _page_ranges(pages: list[int]) -> str:
    pages, out = sorted(pages), []
    for p in pages:
        if out and p == out[-1][1] + 1:
            out[-1][1] = p
        else:
            out.append([p, p])
    return "pages " + ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


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
        # hidden page copies (see visible_pdf) are set aside for both the
        # conversion and the verification
        with visible_pdf(args.pdf) as (pdf, hidden):
            report += ["hidden  : " + n for n in hidden]
            if args.verify_only:
                rst = open(args.verify_only, encoding="utf-8").read()
            else:
                rst = convert(pdf, report)
                with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                    f.write(rst)
                report.insert(0, f"convert : wrote {args.output} ({rst.count(chr(10))} lines)")
            raw = run(["pdftotext", "-enc", "UTF-8", pdf, "-"])
            report.append("verify  :")
            import inspect
            extra = {"pdf": pdf} if "pdf" in inspect.signature(verify_fn).parameters else {}
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
