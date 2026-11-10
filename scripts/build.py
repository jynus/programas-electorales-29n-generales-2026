#!/usr/bin/env python3
"""
build.py - Rebuild every electoral programme of the repository from its
original PDF, verify the result and regenerate README.md.

Steps
-----
1. Read ``documentos_originales/latest.json`` (one entry per party: party,
   version, program_url, sha256sum).
2. Check the SHA-256 of every original PDF against latest.json and, when it
   exists, ``documentos_originales/SHA256SUM.txt`` (both must agree).
3. Run ``scripts/<party>-pdf2rst.py PDF -o OUT.rst`` for every entry. Each
   converter carries its own document-specific verification and must exit 0.
4. Run checks that do not depend on the converters' code:
     a. independent text check: pandoc parses the RST and its words are
        compared with the plain ``pdftotext`` output of the PDF. Allowed:
        whitespace re-segmentation, line-end de-hyphenation, omitted words
        (page furniture: footers, page numbers, running headers; all listed
        in the report) and blocks moved unchanged. Every RST word must be
        accounted for by a distinct word of the PDF, so any added or altered
        word fails.
     b. hyphen review: ``pdftotext -layout`` keeps the line-end hyphens that
        the default mode silently drops; joins that look like real hyphens
        (number ranges, compounds hyphenated elsewhere) become warnings.
     c. comparison with the files already in ``programas_electorales/``:
        unchanged / updated / new. With ``--check`` any difference fails.
5. If everything passed, install the .rst files into
   ``programas_electorales/`` and write ``README.md`` with the list of
   programmes linking to each .rst and its original PDF. Nothing is written
   when any step fails.

Usage
-----
    python3 build.py                 # rebuild, verify, write .rst + README.md
    python3 build.py --check         # verify only; fail if anything would change
    python3 build.py --only bng pp   # restrict to some parties
    python3 build.py --reports DIR   # keep converter logs and check reports

Exit status: 0 all good, 1 a verification failed, 2 bad input (missing file,
checksum mismatch, unknown converter...).

Requirements: python3, poppler-utils (pdftotext), pandoc, plus whatever each
converter needs (adelante_andalucia-pdf2rst.py uses pdfplumber).
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- layout
ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = "documentos_originales"     # original PDFs + latest.json + SHA256SUM.txt
OUTPUT_DIR = "programas_electorales"      # generated .rst files
SCRIPTS_DIR = "scripts"                   # <party>-pdf2rst.py converters
MANIFEST = "latest.json"
CHECKSUMS = "SHA256SUM.txt"
README = "README.md"

# Display names for README; an entry may also carry a "name" key in latest.json.
PARTY_NAMES = {
    "pp": "Partido Popular (PP)",
    "adelante_andalucia": "Adelante Andalucía",
    "bng": "Bloque Nacionalista Galego (BNG)",
    "coalicion_canaria_y_municipalistas": "Coalición Canaria y Municipalistas",
}
REQUIRED_KEYS = ("party", "version", "program_url", "sha256sum")
DEHYPHEN_WINDOW = 12   # max words of page furniture between "consen-" and "so"


class InputError(Exception):
    pass


@dataclass
class Source:
    party: str
    version: object
    url: str
    sha256: str
    name: str
    pdf: str              # path relative to ROOT
    script: str           # path relative to ROOT
    rst: str              # final path relative to ROOT


@dataclass
class Result:
    src: Source
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tmp_rst: str = ""
    status: str = ""      # unchanged / updated / new
    title: str = ""
    words: int = 0
    pages: int = 0
    log: str = ""

    def fail(self, msg):
        self.ok = False
        self.errors.append(msg)


def rel(*parts):
    return os.path.join(*parts)


def absp(path):
    return os.path.join(ROOT, path)


# --------------------------------------------------------------------------- 1. manifest
def load_manifest(only: list[str] | None) -> list[Source]:
    path = absp(rel(SOURCES_DIR, MANIFEST))
    try:
        entries = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        raise InputError(f"{rel(SOURCES_DIR, MANIFEST)} not found")
    except json.JSONDecodeError as e:
        raise InputError(f"{rel(SOURCES_DIR, MANIFEST)} is not valid JSON: {e}")
    if not isinstance(entries, list):
        raise InputError(f"{MANIFEST} must contain a list of entries")
    sources, seen = [], set()
    for i, e in enumerate(entries):
        missing = [k for k in REQUIRED_KEYS if k not in e]
        if missing:
            raise InputError(f"{MANIFEST} entry {i}: missing {', '.join(missing)}")
        party = e["party"]
        if not re.fullmatch(r"[a-z0-9_]+", party):
            raise InputError(f"{MANIFEST} entry {i}: bad party slug {party!r}")
        if party in seen:
            raise InputError(f"{MANIFEST}: party {party!r} listed twice")
        seen.add(party)
        if not re.fullmatch(r"[0-9a-f]{64}", e["sha256sum"].lower()):
            raise InputError(f"{party}: sha256sum is not a SHA-256 hex digest")
        pdf_name = e.get("file") or os.path.basename(e["program_url"].split("?")[0])
        sources.append(Source(
            party=party, version=e["version"], url=e["program_url"],
            sha256=e["sha256sum"].lower(),
            name=e.get("name") or PARTY_NAMES.get(party) or party.replace("_", " ").title(),
            pdf=rel(SOURCES_DIR, pdf_name),
            script=rel(SCRIPTS_DIR, f"{party}-pdf2rst.py"),
            rst=rel(OUTPUT_DIR, f"{party}.rst")))
    if only:
        unknown = set(only) - {s.party for s in sources}
        if unknown:
            raise InputError(f"--only: not in {MANIFEST}: {', '.join(sorted(unknown))}")
        sources = [s for s in sources if s.party in only]
    return sources


# --------------------------------------------------------------------------- 2. checksums
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sha256sum_txt() -> dict[str, str] | None:
    path = absp(rel(SOURCES_DIR, CHECKSUMS))
    if not os.path.exists(path):
        return None
    table = {}
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not m:
            raise InputError(f"{CHECKSUMS} line {n} is malformed: {line!r}")
        table[m.group(2)] = m.group(1).lower()
    return table


def check_sources(sources: list[Source]) -> list[str]:
    """Returns a list of problems (empty if every source is intact)."""
    problems = []
    table = read_sha256sum_txt()
    for s in sources:
        if not os.path.isfile(absp(s.pdf)):
            problems.append(f"{s.party}: {s.pdf} not found")
            continue
        if not os.path.isfile(absp(s.script)):
            problems.append(f"{s.party}: converter {s.script} not found")
        actual = sha256_file(absp(s.pdf))
        if actual != s.sha256:
            problems.append(f"{s.party}: {s.pdf} SHA-256 is {actual}, {MANIFEST} says {s.sha256}")
        if table is not None:
            listed = table.get(os.path.basename(s.pdf))
            if listed is None:
                problems.append(f"{s.party}: {os.path.basename(s.pdf)} missing from {CHECKSUMS}")
            elif listed != s.sha256:
                problems.append(f"{s.party}: {CHECKSUMS} and {MANIFEST} disagree "
                                f"({listed} vs {s.sha256})")
    return problems


# --------------------------------------------------------------------------- 4a. independent check
INLINE = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
          "SmallCaps", "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math",
          "RawInline", "Link", "Image", "Note", "Span"}


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                 (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + r, n - v
    return out


def _enum_label(n: int, style: str, delim: str) -> str:
    lab = {"LowerAlpha": lambda: chr(96 + n), "UpperAlpha": lambda: chr(64 + n),
           "LowerRoman": lambda: _roman(n), "UpperRoman": lambda: _roman(n).upper()
           }.get(style, lambda: str(n))()
    return f"({lab})" if delim == "TwoParens" else f"{lab})" if delim == "OneParen" else f"{lab}."


def pandoc_document(rst_path: str) -> dict:
    proc = subprocess.run(["pandoc", "-f", "rst", "-t", "json", rst_path], capture_output=True)
    if proc.returncode:
        raise RuntimeError("pandoc could not parse the RST: " + proc.stderr.decode(errors="replace")[:500])
    return json.loads(proc.stdout)


def pandoc_words(doc: dict) -> list[str]:
    """The words a reader sees once pandoc has parsed the RST. Comments are
    dropped by the parser; list numbers are rebuilt from the list attributes."""
    buf: list[str] = []

    def walk(n):
        if isinstance(n, list):
            inline = all(isinstance(x, dict) and x.get("t") in INLINE for x in n)
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
                walk(c[2])
                buf.append(" ")
            elif t == "OrderedList":
                (start, style, delim), items = c
                for k, item in enumerate(items):
                    buf.append(f" {_enum_label(start + k, style['t'], delim['t'])} ")
                    walk(item)
            elif t == "MetaString":
                buf.append(f" {c} ")
            elif c is not None:
                walk(c)
                if t not in INLINE:
                    buf.append(" ")

    meta = doc.get("meta", {})
    for key in ("title", "subtitle"):
        if key in meta:
            walk(meta[key])
    walk(doc["blocks"])
    return "".join(buf).split()


def first_heading(doc: dict) -> str:
    meta = doc.get("meta", {})
    target = meta["title"]["c"] if "title" in meta else next(
        (b["c"][2] for b in doc["blocks"] if b["t"] == "Header"), [])
    words = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            if n.get("t") == "Str":
                words.append(n["c"])
            elif n.get("t") in ("Space", "SoftBreak", "LineBreak"):
                words.append(" ")
            elif n.get("c") is not None:
                walk(n["c"])
    walk(target)
    return " ".join("".join(words).split())


class Pool:
    """Words of the PDF that are not in place in the RST, in their original
    runs. Each can account for at most one word of the RST."""

    def __init__(self, runs: list[list[str]]):
        self.runs = runs
        self.used = [[False] * len(r) for r in runs]
        self.index = collections.defaultdict(list)
        for ri, r in enumerate(runs):
            for ti, t in enumerate(r):
                self.index[t].append((ri, ti))

    def free(self, ri, ti):
        return ti < len(self.runs[ri]) and not self.used[ri][ti]

    def take(self, positions):
        for ri, ti in positions:
            self.used[ri][ti] = True

    def longest_run(self, tokens: list[str]) -> list[tuple[int, int]]:
        """Longest prefix of tokens present, contiguous and unused, in one run."""
        best: list[tuple[int, int]] = []
        for ri, ti in self.index.get(tokens[0], []):
            n = 0
            while n < len(tokens) and self.free(ri, ti + n) and self.runs[ri][ti + n] == tokens[n]:
                n += 1
            if n > len(best):
                best = [(ri, ti + q) for q in range(n)]
        return best

    def find_free(self, token: str):
        return next(((ri, ti) for ri, ti in self.index.get(token, []) if self.free(ri, ti)), None)

    def dehyphenated(self, word: str):
        """'consen-' + (up to DEHYPHEN_WINDOW furniture words) + 'so' == 'consenso'."""
        for ri, r in enumerate(self.runs):
            for ti, t in enumerate(r):
                if not self.free(ri, ti) or len(t) < 2 or not t.endswith("-") \
                        or not word.startswith(t[:-1]):
                    continue
                for tj in range(ti + 1, min(ti + 1 + DEHYPHEN_WINDOW, len(r))):
                    if self.free(ri, tj) and t[:-1] + r[tj] == word:
                        return [(ri, ti), (ri, tj)]
        return None

    def composed(self, word: str):
        """Consecutive unused words that glue into `word` (letter-spaced headings
        "F I N" -> "FIN", or a mark next to a word "españoles" "‘‘")."""
        for ri, r in enumerate(self.runs):
            for ti in range(len(r)):
                acc, tj = "", ti
                while self.free(ri, tj) and word.startswith(acc + r[tj]):
                    acc += r[tj]
                    tj += 1
                    if acc == word and tj - ti > 1:
                        return [(ri, q) for q in range(ti, tj)]
        for cut in range(1, len(word)):              # two pieces from anywhere
            a = self.find_free(word[:cut])
            if a:
                self.used[a[0]][a[1]] = True
                b = self.find_free(word[cut:])
                self.used[a[0]][a[1]] = False
                if b:
                    return [a, b]
        return None

    def remaining(self) -> list[str]:
        out = []
        for r, used in zip(self.runs, self.used):
            run = " ".join(t for t, u in zip(r, used) if not u)
            if run:
                out.append(run)
        return out


def split_glued(pdf_words: list[str], rst_words: list[str]):
    """pdftotext sometimes glues overlapping words ("SiLasganas"). A PDF word
    that never occurs in the RST is split into the fewest pieces (2-4, each at
    least 2 characters) that all occur as RST words; every split is reported."""
    vocab = set(rst_words)
    out, splits, cache = [], [], {}

    def best_split(w):
        if w in cache:
            return cache[w]
        best = None
        n = len(w)
        # dp[i] = fewest pieces covering w[:i]
        dp = [None] * (n + 1)
        dp[0] = []
        for i in range(n):
            if dp[i] is None or len(dp[i]) >= 4:
                continue
            for j in range(i + 2, n + 1):
                if w[i:j] in vocab and (dp[j] is None or len(dp[i]) + 1 < len(dp[j])):
                    dp[j] = dp[i] + [w[i:j]]
        if dp[n] and len(dp[n]) >= 2:
            best = dp[n]
        cache[w] = best
        return best

    present = {}

    def occurs(w):        # also inside an RST word, e.g. "ellos" in "ellos‘‘"
        if w not in present:
            present[w] = w in vocab or any(w in t for t in vocab)
        return present[w]

    for w in pdf_words:
        pieces = None if (w.endswith("-") or len(w) < 4 or occurs(w)) else best_split(w)
        if pieces:
            out += pieces
            splits.append((w, pieces))
        else:
            out.append(w)
    return out, splits


_NOT_COMMENT = re.compile(r"^\.\.\s+(?:[\w-]+::|_|\||\[)")


def rst_comments(rst: str) -> list[str]:
    """Text of each RST comment ('.. text' plus its indented continuation),
    excluding directives, targets, substitutions and footnotes."""
    out, depth = [], None
    for line in rst.split("\n"):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if depth is not None:
            if stripped and indent > depth:
                out[-1] += " " + stripped
                continue
            if not stripped:
                continue
            depth = None
        if stripped.startswith("..") and (stripped == ".." or stripped[2:3].isspace()) \
                and not _NOT_COMMENT.match(stripped):
            depth = indent
            out.append(stripped[2:].strip())
    return out


def is_subsequence(small: str, big: str) -> bool:
    it = iter(big)
    return all(ch in it for ch in small)


def is_furniture(run: str, shape: collections.Counter) -> bool:
    """Running headers/footers repeat from page to page (digits aside); page
    numbers are numbers; a footer on a single page is short and numbered."""
    if shape[re.sub(r"\d+", "#", run)] >= 2:
        return True
    if re.fullmatch(r"[\d\W\s]+", run):
        return True
    return len(run.split()) <= 6 and bool(re.search(r"(?:^|\s)\d{1,3}(?:\s|$)", run))


def independent_check(pdf: str, rst_path: str, res: Result) -> list[str]:
    report = []
    ref = subprocess.run(["pdftotext", "-enc", "UTF-8", absp(pdf), "-"],
                         capture_output=True, check=True).stdout.decode("utf-8")
    doc = pandoc_document(rst_path)
    res.title = first_heading(doc)
    a, b = ref.split(), pandoc_words(doc)
    res.words = len(b)
    a, glued = split_glued(a, b)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    in_place = sum(m.size for m in sm.get_matching_blocks())
    counts = collections.Counter()
    deleted, inserted = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        A, B = a[i1:i2], b[j1:j2]
        if A and B and "".join(A) == "".join(B):
            counts["re-segmented (whitespace only)"] += 1
            continue
        if A and B and "".join(t[:-1] if t.endswith("-") and k < len(A) - 1 else t
                               for k, t in enumerate(A)) == "".join(B):
            counts["line-end hyphens joined"] += sum(1 for t in A[:-1] if t.endswith("-"))
            continue
        if A:
            deleted.append(A)
        if B:
            inserted.append((j1, B))

    pool = Pool(deleted)
    unexplained = []
    for j, B in inserted:
        k = 0
        while k < len(B):
            run = pool.longest_run(B[k:])
            if run:
                pool.take(run)
                counts["words moved unchanged"] += len(run)
                k += len(run)
                continue
            w = B[k]
            hit = pool.dehyphenated(w)
            if hit:
                counts["line-end hyphens joined across page furniture"] += 1
            else:
                hit = pool.composed(w)
                if hit:
                    counts["words re-assembled from moved pieces"] += 1
            if hit:
                pool.take(hit)
            else:
                ctx = " ".join(b[max(0, j + k - 6):j + k])
                unexplained.append(f"…{ctx} [{w}]")
            k += 1

    # Omissions: words kept in RST comments are not lost (pandoc just doesn't
    # render comments); the rest must look like page furniture.
    comment_texts = rst_comments(open(rst_path, encoding="utf-8").read())
    comments = collections.Counter(w for c in comment_texts for w in c.split())
    in_comments = 0
    for ri, r in enumerate(pool.runs):
        for ti, t in enumerate(r):
            if pool.free(ri, ti) and comments[t] > 0:
                comments[t] -= 1
                pool.used[ri][ti] = True
                in_comments += 1
    # letter-spaced side tabs ("S E R V I C I O S") kept closed up in a comment
    squeezed = ["".join(c.split()) for c in comment_texts]
    omitted = []
    for run in pool.remaining():
        flat = "".join(run.split())
        if any(len(c) >= len(flat) and is_subsequence(flat, c) for c in squeezed):
            in_comments += len(run.split())
        else:
            omitted.append(run)
    if in_comments:
        counts["words kept in RST comments"] = in_comments
    n_omitted = sum(len(o.split()) for o in omitted)
    shape = collections.Counter(re.sub(r"\d+", "#", o) for o in omitted)
    content = [o for o in omitted if not is_furniture(o, shape)]
    report.append(f"PDF words (pdftotext): {len(a)}   RST words (pandoc): {len(b)}   in place: {in_place}")
    for g, pieces in glued:
        report.append(f"  glued PDF word split: {g} -> {' '.join(pieces)}")
    for k_, v in sorted(counts.items()):
        report.append(f"  {k_}: {v}")
    report.append(f"  omitted PDF words: {n_omitted} ({100 * n_omitted / max(len(a), 1):.1f}%)")
    for text, n in collections.Counter(omitted).most_common():
        report.append(f"    {n:3d} x {text[:150]}")
    if unexplained:
        res.fail(f"independent check: {len(unexplained)} RST word(s) not found in the PDF")
        report.append(f"  NOT IN THE PDF ({len(unexplained)}):")
        report += [f"    {u}" for u in unexplained]
    else:
        report.append("  every RST word is accounted for by the PDF text")
    if content:
        res.fail(f"independent check: {sum(len(c.split()) for c in content)} PDF word(s) "
                 f"missing from the RST that are not page furniture")
        report.append(f"  MISSING FROM THE RST, NOT PAGE FURNITURE ({len(content)} runs):")
        report += [f"    {c[:200]}" for c in content]
    else:
        report.append("  every omitted PDF word is page furniture (repeated header/footer, "
                      "page number) or kept in an RST comment")
    res.notes.append(f"{in_place}/{len(b)} words in place, {n_omitted} omitted, "
                     f"{counts['words moved unchanged']} moved")
    return report


# --------------------------------------------------------------------------- 4b. hyphen review
def hyphen_review(pdf: str, rst_path: str, res: Result) -> list[str]:
    layout = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", absp(pdf), "-"],
                            capture_output=True, check=True).stdout.decode("utf-8")
    rst_words = collections.Counter(re.findall(r"\w+", open(rst_path, encoding="utf-8").read()))
    compounds = set(re.findall(r"\w+-\w+", layout))
    lines = [l for l in layout.split("\n") if l.strip()]
    found = set()
    for x, y in zip(lines, lines[1:]):
        m, n = re.search(r"(\w+)-\s*$", x), re.match(r"\s*(\w+)", y)
        if m and n:
            p, q = m.group(1), n.group(1)
            if rst_words[p + q] and ((p[-1].isdigit() and q[0].isdigit()) or f"{p}-{q}" in compounds):
                found.add((p, q))
    for p, q in sorted(found):
        res.warnings.append(f'"{p}-" + "{q}" appears as "{p}{q}" (real hyphen lost at a line end?)')
    return [f"hyphen review: {len(found)} suspicious join(s)"] + \
           [f"  {p}- {q} -> {p}{q}" for p, q in sorted(found)]


# --------------------------------------------------------------------------- 3+4. per source
def process(src: Source, workdir: str, check_only: bool) -> Result:
    res = Result(src)
    res.tmp_rst = os.path.join(workdir, f"{src.party}.rst")
    cmd = [sys.executable, absp(src.script), absp(src.pdf), "-o", res.tmp_rst]
    proc = subprocess.run(cmd, capture_output=True, cwd=workdir)
    res.log = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    report = [f"$ {' '.join(os.path.relpath(c, ROOT) if c.startswith(ROOT) else c for c in cmd)}",
              f"converter exit status: {proc.returncode}"]
    if proc.returncode != 0 or not os.path.isfile(res.tmp_rst):
        res.fail(f"converter failed (exit {proc.returncode}): "
                 + (res.log.strip().splitlines() or ["no output"])[-1][:200])
        return res
    for line in res.log.splitlines():
        if re.search(r"\b(REVIEW|WARNING|WARN)\b", line):
            res.warnings.append("converter: " + line.strip())

    try:
        report += ["", "INDEPENDENT TEXT CHECK"] + independent_check(src.pdf, res.tmp_rst, res)
        report += ["", "HYPHEN REVIEW"] + hyphen_review(src.pdf, res.tmp_rst, res)
    except (RuntimeError, subprocess.CalledProcessError) as e:
        res.fail(str(e))

    old = absp(src.rst)
    new_bytes = open(res.tmp_rst, "rb").read()
    if not os.path.exists(old):
        res.status = "new"
    elif open(old, "rb").read() == new_bytes:
        res.status = "unchanged"
    else:
        res.status = "updated"
        diff = list(difflib.unified_diff(open(old, encoding="utf-8").read().splitlines(),
                                         new_bytes.decode("utf-8").splitlines(),
                                         src.rst, "regenerated", lineterm="", n=1))
        report += ["", f"DIFFERENCES WITH THE EXISTING {src.rst} ({len(diff)} diff lines)"] + diff[:200]
    if check_only and res.status != "unchanged":
        res.fail(f"{src.rst} is {'missing' if res.status == 'new' else 'out of date or edited by hand'}")
    pages = subprocess.run(["pdfinfo", absp(src.pdf)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", pages, re.M)
    res.pages = int(m.group(1)) if m else 0
    res.log += "\n\n" + "\n".join(report) + "\n"
    return res


# --------------------------------------------------------------------------- 5. README
def md_link(text: str, path: str) -> str:
    from urllib.parse import quote
    return f"[{text.replace('[', '(').replace(']', ')')}]({quote(path)})"


def render_readme(results: list[Result]) -> str:
    lines = [
        "# Programas electorales: elecciones generales del 29 de noviembre de 2026",
        "",
        "Programas electorales convertidos de su PDF original a reStructuredText, "
        "con el texto verificado frente al PDF.",
        "",
        "| Partido | Programa | Texto (RST) | PDF original | Versión | Páginas | Palabras |",
        "|---|---|---|---|---|---:|---:|",
    ]
    for r in results:
        s = r.src
        lines.append("| " + " | ".join([
            s.name,
            r.title or "—",
            md_link(os.path.basename(s.rst), s.rst),
            f"{md_link(os.path.basename(s.pdf), s.pdf)} ([fuente]({s.url}))",
            str(s.version),
            str(r.pages or "—"),
            f"{r.words:,}".replace(",", "."),
        ]) + " |")
    lines += [
        "",
        "## Sumas de verificación de los originales",
        "",
        "| Fichero | SHA-256 |",
        "|---|---|",
    ]
    for r in results:
        lines.append(f"| {md_link(os.path.basename(r.src.pdf), r.src.pdf)} | `{r.src.sha256}` |")
    lines += [
        "",
        "## Cómo se generan",
        "",
        f"Todo se regenera con `python3 build.py`, que lee `{SOURCES_DIR}/{MANIFEST}`, "
        f"comprueba el SHA-256 de cada PDF (también frente a `{SOURCES_DIR}/{CHECKSUMS}`), "
        f"ejecuta `{SCRIPTS_DIR}/<partido>-pdf2rst.py` y verifica el resultado antes de "
        f"escribir `{OUTPUT_DIR}/` y este README:",
        "",
        "* cada conversor compara su RST con el texto del PDF y falla ante cualquier "
        "texto añadido, omitido o alterado;",
        "* una comprobación independiente lee el RST con pandoc y exige que cada palabra "
        "proceda del texto del PDF (`pdftotext`); solo se permiten cambios de espacios, "
        "unión de palabras cortadas a final de línea, bloques movidos sin cambios y la "
        "omisión de elementos de página (números de página, cabeceras y pies);",
        "* `python3 build.py --check` falla si algún fichero de "
        f"`{OUTPUT_DIR}/` no coincide con lo que se genera a partir de los originales.",
        "",
        "Este fichero se genera automáticamente; no lo edites a mano.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify only: write nothing, fail if any output would change")
    ap.add_argument("--only", nargs="+", metavar="PARTY", help="process only these parties")
    ap.add_argument("--reports", metavar="DIR", help="write <party>.log reports into DIR")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                    help="converters to run in parallel (default: %(default)s)")
    args = ap.parse_args(argv)

    for tool in ("pdftotext", "pdfinfo", "pandoc"):
        if not shutil.which(tool):
            print(f"error: {tool} not found (needs poppler-utils and pandoc)", file=sys.stderr)
            return 2

    try:
        sources = load_manifest(args.only)
        print(f"[1] {rel(SOURCES_DIR, MANIFEST)}: {len(sources)} programme(s)")
        problems = check_sources(sources)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if problems:
        print("[2] checksums: FAILED", file=sys.stderr)
        for p in problems:
            print(f"    {p}", file=sys.stderr)
        return 2
    print(f"[2] checksums: all {len(sources)} originals match {MANIFEST}"
          + (f" and {CHECKSUMS}" if read_sha256sum_txt() is not None else ""))

    print(f"[3] converting and [4] verifying ({args.jobs} at a time)...")
    with tempfile.TemporaryDirectory(prefix="build-programas-") as work:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            results = list(ex.map(lambda s: process(s, work, args.check), sources))

        if args.reports:
            os.makedirs(args.reports, exist_ok=True)
            for r in results:
                with open(os.path.join(args.reports, f"{r.src.party}.log"), "w", encoding="utf-8") as f:
                    f.write(r.log)

        for r in results:
            mark = "OK  " if r.ok else "FAIL"
            print(f"    {mark} {r.src.party:36s} {r.status or '-':9s} {'; '.join(r.notes)}")
            for e in r.errors:
                print(f"         error: {e}")
            for w in r.warnings:
                print(f"         warning: {w}")
        if not all(r.ok for r in results):
            print("RESULT: FAILED - nothing was written"
                  + (f" (reports in {args.reports})" if args.reports
                     else " (use --reports DIR for details)"))
            return 1

        readme = render_readme(results) if not args.only else None
        if args.check:
            if readme is not None:
                current = open(absp(README), encoding="utf-8").read() if os.path.exists(absp(README)) else ""
                if current != readme:
                    print(f"RESULT: FAILED - {README} is missing or out of date")
                    return 1
            print("RESULT: PASS - every output is verified and up to date")
            return 0

        os.makedirs(absp(OUTPUT_DIR), exist_ok=True)
        for r in results:
            if r.status != "unchanged":
                shutil.copyfile(r.tmp_rst, absp(r.src.rst))
        print(f"[5] {OUTPUT_DIR}/: " + ", ".join(f"{r.src.party} {r.status}" for r in results))
    if readme is not None:
        with open(absp(README), "w", encoding="utf-8", newline="\n") as f:
            f.write(readme)
        print(f"[5] {README} written")
    else:
        print(f"[5] {README} not rewritten (--only lists a subset)")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
