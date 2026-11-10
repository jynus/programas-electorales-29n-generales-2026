#!/usr/bin/env python3
"""
pdf2rst.py - Convert the "Programa electoral 29N" PDF (Adelante Andalucía) to reStructuredText.

Pipeline
  1. Text:      run poppler's `pdftotext` (default mode, or -raw with --mode raw) and join the
                lines of each paragraph (whitespace only). Every word is kept, numbers included.
                Alternatively pass an existing text file with --txt.
  2. Layout:    read word positions and fonts from the PDF (pdfplumber) to learn the real
                reading order and the role of every line (heading, proposal title, body,
                "Porque..." rationale, running header, footer, side tab...).
  3. Align:     map every word of the text onto its place in the PDF (greedy string tiling).
                The PDF only decides order and structure; every character written to the
                .rst comes from the text.
  4. Write RST: headings, proposal cards as list items (**number + title**, body, *rationale*),
                tables of contents with their page numbers, page furniture (running headers,
                footers with page numbers, side tabs, repeated logos) kept as RST comments.
  5. Verify:    checks, independent of the conversion code, that the .rst has exactly the text's
                characters and words (whitespace aside), that each text line appears verbatim
                (possibly cut at layout boundaries), that it parses cleanly with pandoc and renders
                exactly the same characters, and that each of the 719 proposal cards has the same
                number, title, body and rationale as the PDF card at the same position.

Only whitespace is ever changed: letter-spaced headings ("S E R V I C I O S") are closed up,
words broken after a dash at a PDF line end ("Almería– Aguadulce") are rejoined, and words
that pdftotext glued together ("SiLasganas", "07PROGRAMA" in raw mode) are split.

Differences between the pdftotext modes (both pass every check):
  default  "AFECTIVOSEXUAL" (pdftotext drops the line-end hyphen), "SiLasganas" split by the
           script; "dietistas-nutricionistas" as one word.
  raw      "AFECTIVO-SEXUAL" and "Si ganas" as printed; but the two words Typst hyphenates with
           a repeated hyphen keep both hyphens ("dietistas- -nutricionistas") and the
           letter-spaced logo stays spaced ("G E N E R A L E S 2 9 N").

The layout rules (fonts, sizes, page types) are specific to this PDF.

Requirements: poppler-utils (pdftotext), python3 with pdfplumber; pandoc (optional, for the
parse check).

Usage:
  python3 pdf2rst.py programa.pdf -o programa.rst
  python3 pdf2rst.py programa.pdf -o programa.rst --mode raw
  python3 pdf2rst.py programa.pdf -o programa.rst --txt programa.txt
  python3 pdf2rst.py programa.pdf -o programa.rst --save-txt programa.txt -v
  python3 pdf2rst.py programa.pdf -o programa.rst --verify-only     # re-check an existing .rst
Exit status is 0 only when every check passes.
"""
import argparse
import collections
import heapq
import json
import re
import shutil
import statistics
import subprocess
import sys
import textwrap
import unicodedata

import pdfplumber

# =============================================================================== 1. text
def run_pdftotext(pdf_path, mode='default'):
    if not shutil.which('pdftotext'):
        sys.exit('pdftotext not found: install poppler-utils')
    extra = ['-raw'] if mode == 'raw' else []
    r = subprocess.run(['pdftotext', '-enc', 'UTF-8'] + extra + [pdf_path, '-'], capture_output=True)
    if r.returncode != 0:
        sys.exit('pdftotext failed: ' + r.stderr.decode('utf-8', 'replace'))
    return r.stdout.decode('utf-8')


def join_lines(raw):
    """Join the lines of each blank-line separated block with spaces, as in the original
    '-filtered.txt' (whitespace only; every word, including number-only lines, is kept).
    Form feeds (page breaks) are kept."""
    return '\n\n'.join(' '.join(block.split('\n')) for block in raw.split('\n\n'))


# =============================================================================== 2. layout
MID = 297.6  # page centre (A4 width / 2)


def _numfam(w):
    return 'NUM' if (w['font'] == 'ClashDisplay-Semibold' and w['size'] >= 20) else 'TXT'


def extract_layout(pdf_path):
    """Per page: line segments (words on one row, one column) with dominant font and column."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for pg in pdf.pages:
            ws = pg.extract_words(extra_attrs=['fontname', 'size'], keep_blank_chars=False)
            for w in ws:
                w['font'] = w['fontname'].split('+')[-1]
                w['size'] = round(w['size'], 1)
            rows = []
            for w in sorted(ws, key=lambda w: w['bottom']):
                if rows and abs(rows[-1][-1]['bottom'] - w['bottom']) < 2.5:
                    rows[-1].append(w)
                else:
                    rows.append([w])
            segs = []
            for row in rows:
                row.sort(key=lambda w: w['x0'])
                cur = None
                for w in row:
                    if cur is not None and -1 < w['x0'] - cur['x1'] < 14 and _numfam(cur['words'][-1]) == _numfam(w) \
                            and abs(cur['words'][-1]['size'] - w['size']) < 3:
                        cur['words'].append(w); cur['x1'] = w['x1']
                    else:
                        cur = {'top': w['top'], 'bottom': w['bottom'], 'x0': w['x0'], 'x1': w['x1'], 'words': [w]}
                        segs.append(cur)
            for s in segs:
                # re-join words that pdfplumber split only because the font changed mid-word: "(176),"
                merged = []
                for w in s['words']:
                    if merged and w['x0'] - merged[-1]['x1'] < 1.0:
                        m = merged[-1]
                        m['parts'] = m.get('parts', [(m['text'], m['font'], m['size'])]) + [(w['text'], w['font'], w['size'])]
                        m['text'] += w['text']; m['x1'] = w['x1']
                    else:
                        merged.append(dict(w))
                s['words'] = merged
                fonts = {}
                for w in s['words']:
                    k = (w['font'], w['size']); fonts[k] = fonts.get(k, 0) + len(w['text'])
                s['font'] = max(fonts, key=fonts.get)
                s['text'] = ' '.join(w['text'] for w in s['words'])
                s['col'] = 'F' if (s['x0'] < MID and s['x1'] > MID + 15) else ('L' if s['x0'] < MID else 'R')
            pages.append(segs)
    return pages


def _sortkey(s):
    # numbers labelling a card/entry sit a hair lower than their title: read them first
    bias = 5 if s['font'][0].startswith('ClashDisplay') else 0
    return (s['top'] - bias, s['x0'])


def reading_order(segs):
    """Page furniture apart; the rest read in horizontal bands separated by full-width lines,
    left column then right column."""
    head = [s for s in segs if s['top'] < 50 and s['font'] == ('WorkSans-Bold', 6.3)]
    foot = sorted([s for s in segs if s['top'] > 770 and s['font'] in (('WorkSans-Bold', 6.3), ('ClashDisplay-Semibold', 10.0))],
                  key=lambda s: s['x0'])
    tabs = [s for s in segs if s['font'] == ('ClashDisplay-Semibold', 8.0) and (s['x1'] < 30 or s['x0'] > 565)]
    furn = head + foot + tabs
    body = [s for s in segs if s not in furn]
    spans = sorted([s for s in body if s['col'] == 'F'], key=lambda s: s['top'])
    rest = [s for s in body if s['col'] != 'F']
    out = []
    bounds = [s['top'] for s in spans] + [1e9]
    for i, hi in enumerate(bounds):
        if i > 0:
            out.append(spans[i - 1])
        band = [s for s in rest if s['top'] < hi]
        out += sorted([s for s in band if s['col'] == 'L'], key=_sortkey)
        out += sorted([s for s in band if s['col'] == 'R'], key=_sortkey)
        rest = [s for s in rest if s not in band]
    for s in segs: s.pop('role', None)
    for s in head: s['role'] = 'head'
    for s in foot: s['role'] = 'foot'
    for s in tabs: s['role'] = 'tab'
    return head + out + tabs + foot


# =============================================================================== 3. align
# pdftotext glued three overlapping PDF words (title "Si ganas aquí..." over body "Las grandes...")
MANUAL_SPLITS = {'SiLasganas': ['Si', 'Las', 'ganas']}


def split_tok(w):
    """Matching unit: words with no lowercase letter (headings, numbers) are compared character by
    character, so letter-spaced text ("S E R V I C I O S") matches the PDF word ("SERVICIOS")."""
    w = unicodedata.normalize('NFKC', w)
    if not re.search(r'[a-zà-ÿ]', w):
        return list(w)
    return [w]


def gst(T, P, Tbreak, Tstart, Pstart):
    """Greedy String Tiling of txt units T onto PDF units P, longest runs first. Runs start and end
    on word boundaries on both sides and never cross a txt line. Returns P index per T index."""
    nT, nP = len(T), len(P)
    tm = [None] * nT; pm = [None] * nP
    Tend = {i - 1 for i in Tstart if i > 0} | {nT - 1}
    Pend = {j - 1 for j in Pstart if j > 0} | {nP - 1}
    pos = collections.defaultdict(list)
    for j, p in enumerate(P): pos[p].append(j)

    def ext(i, j):
        if i not in Tstart or j not in Pstart: return 0
        L = good = 0
        while i + L < nT and j + L < nP and tm[i + L] is None and pm[j + L] is None and T[i + L] == P[j + L] \
                and (L == 0 or (i + L) not in Tbreak):
            L += 1
            if (i + L - 1) in Tend and (j + L - 1) in Pend: good = L
        return good

    heap = []
    for i, t in enumerate(T):
        for j in pos.get(t, []):
            L = ext(i, j)
            if L: heapq.heappush(heap, (-L, abs(i / nT - j / nP), i, j))
    while heap:
        nl, d, i, j = heapq.heappop(heap)
        L = ext(i, j)
        if L == 0: continue
        if L < -nl:
            heapq.heappush(heap, (-L, d, i, j)); continue
        for k in range(L):
            tm[i + k] = j + k; pm[j + k] = i + k
    return tm


def merge_line_breaks(segs, page_text, notes):
    """Mirror what pdftotext did with hyphens at PDF line ends, deciding from the text itself
    (default mode collapses them, raw mode keeps them as printed)."""
    flat = ''.join(page_text.split())
    for a, b in zip(segs, segs[1:]):
        if not a['words'] or not b['words']: continue
        wa, wb = a['words'][-1], b['words'][0]
        if not (wa['text'].endswith('-') and a['font'] == b['font']): continue
        if wb['text'].startswith('-') and len(wb['text']) > 1:
            # Typst repeats the hyphen on the next line ("dietistas-" / "-nutricionistas")
            once = wa['text'] + wb['text'][1:]
            if once in flat and wa['text'] + wb['text'] not in flat:
                wa['text'] = once
                b['words'] = b['words'][1:]
                notes.append(('repeated line-break hyphen already single in the text', once))
        elif wb['text'][:1].isalpha() and a['col'] == b['col'] and 0 < b['top'] - a['top'] < 15:
            glued = wa['text'][:-1] + wb['text']
            if glued in flat and wa['text'] + wb['text'] not in flat:
                # pdftotext dropped this line-end hyphen and glued the halves: mirror the text
                wa['text'] = glued
                b['words'] = b['words'][1:]
                notes.append(('line-end hyphen missing in the text', glued))
    return [s for s in segs if s['words']]


def need_space(p, t):
    """Space between two tokens unless they belong to the same PDF word (closes up letter-spaced
    headings), to the same text word (keeps the text's own words), or the PDF line broke right
    after an en dash inside a word ("Almería–" / "Aguadulce")."""
    return not (t['pw'] == p['pw'] or t['tw'] == p['tw']
                or (p.get('eol_dash') and t.get('sol') and not t['t'].startswith('-')))


def join_toks(toks):
    out = ''
    for k, t in enumerate(toks):
        if k and need_space(toks[k - 1], t): out += ' '
        out += t['t']
    return out


def align(layout, text):
    tpages = text.split('\f')
    if tpages and not tpages[-1].strip():
        tpages = tpages[:-1]
    if len(tpages) != len(layout):
        sys.exit(f'text has {len(tpages)} pages but the PDF has {len(layout)}')
    notes, unmatched, dropped = [], [], []
    pages = []
    for pi, raw_segs in enumerate(layout):
        segs = merge_line_breaks(reading_order(raw_segs), tpages[pi], notes)
        P, Pref, Pstart = [], [], set()
        for si, s in enumerate(segs):
            for wi, w in enumerate(s['words']):
                Pstart.add(len(P))
                for ci, t in enumerate(split_tok(w['text'])):
                    P.append(t); Pref.append((si, wi, ci))
        T, Torig, Tword, Tbreak, Tstart = [], [], [], set(), set()
        twid = 0
        for line in tpages[pi].split('\n'):
            first = True
            for w in line.split():
                if w in MANUAL_SPLITS:
                    notes.append(('word split', w, MANUAL_SPLITS[w]))
                for w2 in MANUAL_SPLITS.get(w, [w]):
                    units = split_tok(w2)
                    origs = [w2] if len(units) == 1 else list(w2)
                    if len(units) != len(origs):
                        sys.exit(f'cannot split word {w2!r}')
                    Tstart.add(len(T)); twid += 1
                    for u, o in zip(units, origs):
                        if first: Tbreak.add(len(T)); first = False
                        T.append(u); Torig.append(o); Tword.append((pi, twid))
        tm = gst(T, P, Tbreak, Tstart, Pstart)
        got = {}
        for i, j in enumerate(tm):
            if j is None: unmatched.append((pi + 1, Torig[i]))
            else: got[Pref[j]] = (Torig[i], Tword[i])
        for j, ref in enumerate(Pref):
            if ref not in got:
                dropped.append((pi + 1, P[j], segs[ref[0]]['font']))
        pwid = 0
        for si, s in enumerate(segs):
            s['toks'] = []
            for wi, w in enumerate(s['words']):
                pwid += 1
                chars_font = []
                for ptxt, pf, _ in w.get('parts', [(w['text'], w['font'], w['size'])]):
                    chars_font += [pf] * len(ptxt)
                n = len(split_tok(w['text']))
                for ci in range(n):
                    g = got.get((si, wi, ci))
                    if g is None: continue
                    f = chars_font[ci] if n > 1 and ci < len(chars_font) else max(set(chars_font), key=chars_font.count)
                    s['toks'].append({'t': g[0], 'pw': (pi, pwid), 'tw': g[1], 'font': f})
            if s['toks']:
                lw = s['words'][-1]['text']
                if lw.endswith(('–', '-')) and len(lw) > 1 and s['toks'][-1]['pw'] == (pi, pwid):
                    s['toks'][-1]['eol_dash'] = True
                s['toks'][0]['sol'] = True
            s['out'] = join_toks(s['toks'])
        pages.append(segs)
    return pages, notes, unmatched, dropped


# =============================================================================== 4. rst
class RstWriter:
    WIDTH = 79
    HEAD = {0: ('=', True), 1: ('#', True), 2: ('=', False), 3: ('-', False), 4: ('~', False)}
    WEIGHT = [('ExtraBold', 4), ('SemiBold', 2), ('Bold', 3), ('Medium', 1)]
    OPEN_OK = set(" \t-:/'\"<([{«“‘")
    CLOSE_OK = set(" \t-.,:;!?\\/'\")]}>»”’")
    ROLE = {
        ('ClashDisplay-Semibold', 21.0): 'num',
        ('FrauncesTexto-Bold', 10.5): 'ptitle',
        ('WorkSans-Regular', 8.6): 'pbody',
        ('FrauncesTexto-Italic', 8.8): 'pwhy',
        ('WorkSans-Bold', 8.0): 'subhead',
        ('WorkSans-Regular', 9.0): 'intro',
        ('WorkSans-Regular', 10.0): 'intro',
        ('ClashDisplay-Semibold', 40.0): 'secnum',
        ('FrauncesTitular-ExtraBold', 34.0): 'sectitle',
        ('FrauncesTitular-ExtraBold', 26.0): 'sectitle',
        ('WorkSans-Regular', 8.4): 'box',
    }

    def __init__(self):
        self.out, self.log = [], []
        self.in_section = False
        self.emitted, self.tabs = set(), []

    # ---------------------------------------------------------------- text helpers
    def plain(self, segs):
        self.emitted.update(id(s) for s in segs)
        return join_toks([t for s in segs for t in s['toks']])

    def weight(self, font):
        for k, v in self.WEIGHT:
            if k in font: return v
        return 0

    def rich(self, segs):
        """Text with **inline bold** for runs set heavier than the lightest weight in the block."""
        self.emitted.update(id(s) for s in segs)
        toks = [t for s in segs for t in s['toks']]
        if not toks: return ''
        base = min(self.weight(t['font']) for t in toks)
        pieces = [[t['t'], self.weight(t['font']) > base, ' ' if k and need_space(toks[k - 1], t) else '']
                  for k, t in enumerate(toks)]
        s, k = '', 0
        while k < len(pieces):
            txt, bold, sep = pieces[k]
            if not bold:
                s += sep + txt; k += 1; continue
            j, run = k, txt
            while j + 1 < len(pieces) and pieces[j + 1][1]:
                run += pieces[j + 1][2] + pieces[j + 1][0]; j += 1
            before = (s + sep)[-1:] or ' '
            after = (pieces[j + 1][2] or pieces[j + 1][0][:1]) if j + 1 < len(pieces) else ' '
            if before in self.OPEN_OK and after in self.CLOSE_OK:
                s += sep + '**' + run + '**'
            else:
                self.log.append(('emphasis skipped', run))
                s += sep + run
            k = j + 1
        return s

    # ---------------------------------------------------------------- emitters
    def blank(self):
        if self.out and self.out[-1] != '':
            self.out.append('')

    def para(self, text, indent='', first=None, tight=False):
        if not text: return
        if not tight: self.blank()
        w = textwrap.TextWrapper(width=self.WIDTH, initial_indent=indent if first is None else first,
                                 subsequent_indent=indent, break_long_words=False, break_on_hyphens=False)
        self.out.extend(w.wrap(text))

    def heading(self, text, level):
        ch, over = self.HEAD[level]
        self.blank()
        if over: self.out.append(ch * len(text))
        self.out += [text, ch * len(text)]

    def comment(self, text):
        if text:
            self.blank(); self.out.append('.. ' + text)

    def lineblock(self, lines):
        lines = [l for l in lines if l]
        if not lines: return
        self.blank()
        for l in lines:
            w = textwrap.TextWrapper(width=self.WIDTH, initial_indent='| ', subsequent_indent='  ',
                                     break_long_words=False, break_on_hyphens=False)
            self.out.extend(w.wrap(l))

    def bullets(self, items):
        compact = all(len([p for p in it if p]) <= 1 for it in items)
        for k, paras in enumerate(items):
            paras = [p for p in paras if p]
            if not paras: continue
            if not (compact and k > 0): self.blank()
            self.para(paras[0], indent='  ', first='- ', tight=compact and k > 0)
            for p in paras[1:]:
                self.para(p, indent='  ')

    # ---------------------------------------------------------------- grouping helpers
    @staticmethod
    def by_font(segs, *fonts):
        return [s for s in segs if s['font'] in fonts]

    @staticmethod
    def rows(segs):
        return sorted(segs, key=lambda s: (s['top'], s['x0']))

    def anchor_groups(self, anchors, members, tol=4):
        """Attach each member line to the last anchor (number) above it in the same column."""
        groups = {id(a): [] for a in anchors}
        for m in self.rows(members):
            cands = [a for a in anchors if (a['col'] == m['col'] or 'F' in (a['col'], m['col'])) and a['top'] <= m['top'] + tol]
            if not cands:
                self.log.append(('orphan line', m['out'])); continue
            groups[id(max(cands, key=lambda a: a['top']))].append(m)
        return [(a, groups[id(a)]) for a in anchors]

    @staticmethod
    def paragraphs(segs):
        """Split lines into paragraphs where the vertical gap is clearly larger than usual."""
        segs = list(segs)
        if not segs: return []
        same = lambda a, b: a['col'] == b['col'] or ('F' in (a['col'], b['col']) and 'R' not in (a['col'], b['col']))
        gaps = [b['top'] - a['top'] for a, b in zip(segs, segs[1:]) if same(a, b) and b['top'] > a['top']]
        med = statistics.median(gaps) if gaps else 0
        out = [[segs[0]]]
        for a, b in zip(segs, segs[1:]):
            if med and b['top'] - a['top'] > 1.5 * med and same(a, b): out.append([b])
            else: out[-1].append(b)
        return out

    def split(self, segs):
        head = [s for s in segs if s.get('role') == 'head']
        foot = [s for s in segs if s.get('role') == 'foot']
        body = [s for s in segs if s.get('role') not in ('head', 'foot', 'tab')]
        self.tabs = [s for s in segs if s.get('role') == 'tab']
        return head, body, foot

    def footer(self, foot):
        """Page furniture as comments: side tab (area number) and footer (page number, 'ÍNDICE')."""
        for t in self.tabs: self.comment(self.plain([t]))
        if foot: self.comment(self.plain(sorted(foot, key=lambda s: s['x0'])))

    def entry(self, groups, pagefont):
        """TOC entry text: number, title lines, then the page number."""
        out = []
        for a, m in groups:
            pages = [x for x in m if x['font'] == pagefont]
            out.append([self.plain([a] + self.rows([x for x in m if x not in pages]) + pages)])
        return out

    def check_used(self, pn, body, used):
        ids = {id(s) for s in used}
        for s in body:
            if id(s) not in ids and s['toks']:
                self.log.append(('UNUSED LINE', pn, s['font'], s['out']))

    @staticmethod
    def logo(s):
        return s['font'][0].startswith('ClashDisplay') and s['font'][1] in (13.2, 14.3, 15.0, 16.2, 17.6, 20.0)

    # ---------------------------------------------------------------- page types
    def page_cover(self, pn, segs):
        head, body, foot = self.split(segs)
        bf = self.by_font
        title = self.rows(bf(body, ('FrauncesTitular-ExtraBold', 72.0)))
        self.heading(self.plain(title), 0)
        self.lineblock([self.plain(bf(body, ('WorkSans-Bold', 7.5))),
                        self.plain([s for s in bf(body, ('WorkSans-Bold', 8.0)) if s['x0'] < 200]),
                        self.plain([s for s in bf(body, ('WorkSans-Bold', 8.0)) if s['x0'] >= 200]),
                        self.plain(self.rows(bf(body, ('ClashDisplay-Semibold', 15.0), ('ClashDisplay-Medium', 13.2))))])
        self.para(self.plain(self.rows(bf(body, ('WorkSans-Regular', 10.5)))))
        self.check_used(pn, body, title + bf(body, ('WorkSans-Bold', 7.5), ('WorkSans-Bold', 8.0), ('ClashDisplay-Semibold', 15.0),
                                             ('ClashDisplay-Medium', 13.2), ('WorkSans-Regular', 10.5)))

    def page_part_cover(self, pn, segs):
        head, body, foot = self.split(segs)
        title = sorted([s for s in body if s['font'][1] == 92.0], key=lambda s: (s['top'] > 150, s['x0']))
        self.heading(self.plain(title), 1)
        logo = self.rows([s for s in body if self.logo(s)])
        self.comment(self.plain(logo))
        self.check_used(pn, body, title + logo)

    def page_intro(self, pn, segs):
        head, body, foot = self.split(segs)
        intro = [s for s in body if s['font'][1] == 14.5]
        for p in self.paragraphs(self.rows(intro)):
            self.para(self.rich(p))
        label = self.by_font(body, ('WorkSans-Bold', 8.0))
        self.para('**' + self.plain(label) + '**')
        anchors = self.by_font(body, ('ClashDisplay-Semibold', 12.0))
        titles = self.by_font(body, ('FrauncesTexto-Bold', 10.5))
        pagenums = self.by_font(body, ('ClashDisplay-Medium', 10.5))
        items = []
        for col in ('L', 'R'):
            ga = sorted([a for a in anchors if a['col'] == col], key=lambda a: a['top'])
            items += self.entry(self.anchor_groups(ga, [t for t in titles + pagenums if t['col'] == col]),
                                ('ClashDisplay-Medium', 10.5))
        self.bullets(items)
        self.check_used(pn, body, intro + label + anchors + titles + pagenums)
        self.footer(foot)

    def page_medida(self, pn, segs):
        head, body, foot = self.split(segs)
        bf = self.by_font
        num = bf(body, ('ClashDisplay-Semibold', 120.0))
        title = self.rows([s for s in body if s['font'][0] == 'FrauncesTitular-ExtraBold' and s['font'][1] in (34.0, 44.0)])
        self.heading(self.plain(num + title), 2)
        box = self.rows(bf(body, ('FrauncesTitular-BoldItalic', 30.0), ('FrauncesTitular-ExtraBold', 30.0)))
        self.comment(self.plain(box))
        label = bf(body, ('WorkSans-Bold', 7.5))
        self.para(self.plain(label))
        bullets = bf(body, ('WorkSans-Regular', 11.0))
        if bullets:
            items = []
            for col in ('L', 'R'):
                for s in sorted([b for b in bullets if b['col'] == col], key=lambda s: s['top']):
                    if s['out'].startswith('–') or not items: items.append([s])
                    else: items[-1].append(s)
            self.lineblock([self.plain(it) for it in items])
        text = self.rows(bf(body, ('WorkSans-Regular', 12.0)))
        for p in self.paragraphs(text):
            self.para(self.plain(p))
        dev = self.rows(bf(body, ('WorkSans-SemiBold', 8.0)))
        if dev: self.para('*' + self.plain(dev) + '*')
        self.check_used(pn, body, num + title + box + label + bullets + text + dev)
        self.footer(foot)

    def page_index(self, pn, segs):
        head, body, foot = self.split(segs)
        title = self.rows(self.by_font(body, ('FrauncesTitular-ExtraBold', 40.0)))
        self.heading(self.plain(title), 1)
        label = self.by_font(body, ('WorkSans-Bold', 7.5))
        self.para('**' + self.plain(label) + '**')
        anchors = sorted(self.by_font(body, ('ClashDisplay-Semibold', 20.0)), key=lambda a: a['top'])
        for a in anchors: a['col'] = 'F'
        entries = [s for s in body if s['font'][0] == 'FrauncesTitular-ExtraBold' and s['font'][1] in (13.0, 14.0)]
        pagenums = self.by_font(body, ('ClashDisplay-Medium', 11.0))
        self.bullets(self.entry(self.anchor_groups(anchors, entries + pagenums, tol=8), ('ClashDisplay-Medium', 11.0)))
        self.check_used(pn, body, title + label + anchors + entries + pagenums)
        self.footer(foot)

    def page_area(self, pn, segs):
        self.in_section = False
        head, body, foot = self.split(segs)
        bf = self.by_font
        for h in head: self.comment(self.plain([h]))
        num = bf(body, ('ClashDisplay-Semibold', 150.0))
        title = self.rows([s for s in body if s['font'][0] == 'FrauncesTitular-ExtraBold' and s['font'][1] >= 30])
        self.heading(self.plain(num + title), 2)
        labels = self.rows(bf(body, ('WorkSans-Bold', 7.5)))
        self.lineblock([self.plain([l]) for l in labels])
        intro = self.rows(bf(body, ('WorkSans-Regular', 9.0), ('WorkSans-Regular', 10.0)))
        for p in self.paragraphs(intro):
            self.para(self.rich(p))
        toch = bf(body, ('WorkSans-Bold', 7.0))
        used = num + title + labels + intro + toch
        if toch:
            self.para('**' + self.plain(toch) + '**')
            anchors = sorted(bf(body, ('ClashDisplay-Semibold', 11.0)), key=lambda a: a['top'])
            pagenums = bf(body, ('ClashDisplay-Medium', 9.0))
            entries = bf(body, ('FrauncesTexto-Bold', 10.0))
            if anchors:
                items = self.entry(self.anchor_groups(anchors, entries + pagenums, tol=8), ('ClashDisplay-Medium', 9.0))
            else:  # unnumbered entries (area 11): each title line is an entry, with its page number
                ents = self.rows(entries)
                items = self.entry(self.anchor_groups(ents, pagenums, tol=4), ('ClashDisplay-Medium', 9.0))
            self.bullets(items)
            used += anchors + entries + pagenums
        self.check_used(pn, body, used)
        self.footer(foot)

    def page_program(self, pn, segs):
        head, body, foot = self.split(segs)
        for h in head: self.comment(self.plain([h]))
        for s in body:
            s['r'] = self.ROLE.get(s['font'])
            if s['r'] is None:
                self.log.append(('UNKNOWN ROLE', pn, s['font'], s['out']))
        sec = [s for s in body if s['r'] in ('secnum', 'sectitle')]
        state = {'sec_done': False, 'card': None, 'items': [], 'run': None}
        new_card = lambda: {'num': [], 'title': [], 'body': [], 'why': []}

        def flush_cards():
            if state['items']:
                self.bullets(state['items']); state['items'] = []

        def close_card():
            card = state['card']
            if card is None: return
            paras = []
            t = self.plain(card['num'] + card['title'])
            if t: paras.append('**' + t + '**')
            b = self.rich(card['body'])
            if b: paras.append(b)
            w = self.plain(card['why'])
            if w: paras.append('*' + w + '*')
            if not card['title']:
                self.log.append(('card without title', pn, paras[:1]))
            state['items'].append(paras)
            state['card'] = None

        def close_run():
            if state['run'] is None: return
            role, ss = state['run']
            if role == 'subhead':
                self.heading(self.plain(ss), 4 if self.in_section else 3)
            elif role == 'intro':
                for p in self.paragraphs(ss): self.para(self.rich(p))
            elif role == 'box':
                self.para(self.rich(ss))
            state['run'] = None

        for s in body:
            r = s['r']
            if r in ('secnum', 'sectitle'):
                if not state['sec_done']:
                    close_card(); flush_cards(); close_run()
                    self.heading(self.plain(sorted([x for x in sec if x['r'] == 'secnum'], key=lambda x: x['top']) +
                                            self.rows([x for x in sec if x['r'] == 'sectitle'])), 3)
                    state['sec_done'] = True
                    self.in_section = True
                continue
            if r in ('num', 'ptitle', 'pbody', 'pwhy'):
                close_run()
                card = state['card']
                if r == 'num':
                    close_card(); state['card'] = new_card(); state['card']['num'].append(s)
                elif r == 'ptitle':
                    if card is None or card['body'] or card['why']:
                        close_card(); state['card'] = new_card()
                    state['card']['title'].append(s)
                elif r == 'pbody':
                    if card is None or card['why']:
                        self.log.append(('body without card', pn, s['out']))
                        close_card(); state['card'] = new_card()
                    state['card']['body'].append(s)
                else:
                    if card is None:
                        self.log.append(('rationale without card', pn, s['out']))
                        state['card'] = new_card()
                    state['card']['why'].append(s)
                continue
            close_card(); flush_cards()
            run = state['run']
            if run is not None and run[0] == r == 'box':
                run[1].append(s)
            elif run is not None and run[0] == r and s['col'] == run[1][-1]['col'] and 0 < s['top'] - run[1][-1]['top'] < 30:
                run[1].append(s)
            else:
                close_run(); state['run'] = (r, [s])
        close_card(); flush_cards(); close_run()
        self.footer(foot)

    def page_back(self, pn, segs):
        head, body, foot = self.split(segs)
        bf = self.by_font
        title = self.rows(bf(body, ('FrauncesTitular-ExtraBold', 40.0)))
        self.heading(self.plain(title), 1)
        url = bf(body, ('ClashDisplay-Semibold', 15.0))
        self.para(self.plain(url))
        txt = self.rows(bf(body, ('WorkSans-Regular', 10.0)))
        self.para(self.plain(txt))
        logo = self.rows([s for s in body if self.logo(s) and s not in url])
        self.comment(self.plain(logo))
        small = self.rows(bf(body, ('WorkSans-Regular', 7.0)))
        self.para(self.plain(small))
        self.check_used(pn, body, title + url + txt + logo + small + foot)

    # ---------------------------------------------------------------- driver
    def build(self, pages):
        for pi, segs in enumerate(pages):
            pn = pi + 1
            if not any(s['toks'] for s in segs):
                continue
            fonts = {s['font'] for s in segs}
            if pn == 1: self.page_cover(pn, segs)
            elif pn == 2: self.page_part_cover(pn, segs)
            elif pn == 3: self.page_intro(pn, segs)
            elif ('ClashDisplay-Semibold', 120.0) in fonts: self.page_medida(pn, segs)
            elif ('FrauncesTitular-ExtraBold', 40.0) in fonts and pn < len(pages): self.page_index(pn, segs)
            elif ('WorkSans-Bold', 7.5) in fonts: self.page_area(pn, segs)
            elif pn == len(pages): self.page_back(pn, segs)
            else: self.page_program(pn, segs)
            for s in segs:
                if s['toks'] and id(s) not in self.emitted:
                    self.log.append(('UNUSED LINE', pn, s['font'], s['out']))
        res = []
        for l in self.out:
            if l == '' and (not res or res[-1] == ''): continue
            res.append(l.rstrip())
        while res and res[-1] == '': res.pop()
        return '\n'.join(res) + '\n'



# ------------------------------------------------------------------------------- card check
CARD_FONTS = {('FrauncesTexto-Bold', 10.5): 0, ('WorkSans-Regular', 8.6): 1, ('WorkSans-SemiBold', 8.6): 1,
              ('FrauncesTexto-Italic', 8.8): 2}


def pdf_cards(pdf_path):
    """Proposal cards read straight from the PDF by geometry (independent of the converter's
    reading-order code): each big number starts a card that owns the title/body/rationale text
    below it in the same column, down to the next number or other block."""
    cards = []
    with pdfplumber.open(pdf_path) as pdf:
        for pn, pg in enumerate(pdf.pages, 1):
            ws = pg.extract_words(extra_attrs=['fontname', 'size'])
            for w in ws:
                w['f'] = (w['fontname'].split('+')[-1], round(w['size'], 1))
            nums = [w for w in ws if w['f'] == ('ClashDisplay-Semibold', 21.0)]
            stops = [w for w in ws if w['f'] not in CARD_FONTS and w['f'][0] != 'ClashDisplay-Semibold' and w['top'] < 770]
            for col in (0, 1):
                incol = lambda w: (w['x0'] >= MID) == bool(col)
                cn = sorted([n for n in nums if incol(n)], key=lambda n: n['top'])
                for k, n in enumerate(cn):
                    lo = n['top'] - 4
                    hi = min([m['top'] - 4 for m in cn[k + 1:]] +
                             [w['top'] - 2 for w in stops if incol(w) and w['top'] > n['top'] + 2] + [1e9])
                    mine = [w for w in ws if w['f'] in CARD_FONTS and incol(w) and lo <= w['top'] < hi]
                    parts = []
                    for role in (0, 1, 2):
                        rw = sorted([w for w in mine if CARD_FONTS[w['f']] == role], key=lambda w: (round(w['bottom']), w['x0']))
                        parts.append([w['text'] for w in rw])
                    cards.append({'page': pn, 'num': n['text'], 'title': parts[0], 'body': parts[1], 'why': parts[2]})
    return cards


def rst_cards(rst):
    """Proposal list items of the .rst: '- **title**', then body paragraph(s), then '*rationale*'."""
    lines, items, k = rst.split('\n'), [], 0
    while k < len(lines):
        if lines[k].startswith('- **'):
            j = k + 1
            while j < len(lines) and (lines[j] == '' or lines[j].startswith('  ')):
                j += 1
            block = '\n'.join([lines[k][2:]] + [l[2:] for l in lines[k + 1:j]]).strip()
            paras = [' '.join(p.split()) for p in block.split('\n\n') if p.strip()]
            title = paras[0][2:-2] if paras[0].startswith('**') and paras[0].endswith('**') else None
            why = paras[-1][1:-1] if len(paras) > 1 and paras[-1].startswith('*') and not paras[-1].startswith('**') else None
            body = ' '.join(p.replace('**', '') for p in paras[1:-1 if why is not None else None])
            items.append({'line': k + 1, 'title': title, 'body': body, 'why': why})
            k = j
        else:
            k += 1
    return items


def check_cards(pdf_path, rst):
    """Each .rst card must hold the same title, body and rationale as the PDF card at the same
    position. Only differences allowed: whitespace, and numbers absent from the text file."""
    def norm(words):
        s = ''.join(''.join(words).split())
        return s.replace('--', '-')            # Typst repeats a hyphen across line breaks
    def strip_nums(words):
        return [w for w in words if not re.fullmatch(r'\d+', w)]
    pc, rc = pdf_cards(pdf_path), rst_cards(rst)
    errors, num_missing, nums_shown = [], [], 0
    if len(pc) != len(rc):
        errors.append(f'{len(pc)} cards in the PDF but {len(rc)} in the rst')
    for p, r in zip(pc, rc):
        if r['title'] is None or r['why'] is None:
            errors.append(f'rst line {r["line"]}: card without bold title or italic rationale'); continue
        tw = r['title'].split()
        if tw and tw[0] == p['num']:
            tw = tw[1:]; nums_shown += 1
        for part, rtext in (('title', ' '.join(tw)), ('body', r['body']), ('why', r['why'])):
            a, b = norm(p[part]), norm(rtext.split())
            if a == b: continue
            if norm(strip_nums(p[part])) == b:
                num_missing.append((p['page'], p['num'], part)); continue
            errors.append(f'rst line {r["line"]} (PDF p.{p["page"]}, proposal {p["num"]}) {part} differs: '
                          f'PDF {" ".join(p[part])[:80]!r} / rst {rtext[:80]!r}')
    return len(pc), nums_shown, num_missing, errors


# =============================================================================== 5. verify
def verify(text, rst, rst_path, use_pandoc=True, verbose=False, pdf_path=None):
    """Checks, independent of how the .rst was produced, that it holds exactly the text."""
    ok = True
    report = []

    def say(msg): report.append(msg); print(msg)

    def fail(msg):
        nonlocal ok
        ok = False
        say('FAIL: ' + msg)

    def nws(s): return [c for c in s if not c.isspace()]

    if '*' in text:
        fail("the text contains '*', which this checker treats as RST markup")

    # strip RST markup from the source
    lines = rst.split('\n')
    adorn = re.compile(r'^([=#\-~])\1+$')
    text_lines, comments = [], []
    for k, l in enumerate(lines):
        if adorn.match(l):
            nb = [lines[k - 1] if k else '', lines[k + 1] if k + 1 < len(lines) else '']
            if not any(len(n) == len(l) and n.strip() and not adorn.match(n) for n in nb):
                fail(f'line {k + 1} looks like a heading underline but is not one: {l}')
            continue
        s = l.strip()
        if s.startswith('.. '):
            comments.append(s[3:]); continue
        if s.startswith('| ') or s.startswith('- '): s = s[2:]
        text_lines.append(s)
    src = '\n'.join(text_lines).replace('*', '') + '\n' + '\n'.join(comments)

    # 1. characters
    ct, cr = collections.Counter(nws(text)), collections.Counter(nws(src))
    say(f'[1] non-whitespace characters: text {sum(ct.values())}, rst without markup {sum(cr.values())}')
    if ct != cr: fail(f'characters differ: only in text {dict(ct - cr)}, only in rst {dict(cr - ct)}')
    else: say('    OK - exactly the same characters')

    # 2. words
    wt, wr = collections.Counter(text.split()), collections.Counter(src.split())
    only_t, only_r = wt - wr, wr - wt
    say(f'[2] words: text {sum(wt.values())}, rst {sum(wr.values())}, identical {sum((wt & wr).values())}')
    if collections.Counter(''.join(only_t.elements())) != collections.Counter(''.join(only_r.elements())):
        fail('some words differ by more than whitespace')
    else:
        say(f'    OK - the other {sum(only_t.values())} text words differ only by whitespace '
            f'(they became {sum(only_r.values())} words in the rst)')

    # 3. sequence: every text line appears verbatim (whitespace aside), possibly cut in pieces
    R = ''.join(nws(src))
    Rmap = [i for i, c in enumerate(src) if not c.isspace()]
    Rstarts, p = set(), 0
    for w in src.split():
        Rstarts.add(p); p += len(w)
    dash = {i + 1 for i, c in enumerate(R) if c in '–-'}   # words rejoined after a line-end dash or hyphen
    Rends = Rstarts | {len(R)} | dash
    Rstarts |= dash
    K = 8
    idx = collections.defaultdict(list)
    for i in range(len(R) - K + 1): idx[R[i:i + K]].append(i)
    hist, bad, ws_changes = collections.Counter(), [], []
    for li, line in enumerate(text.split('\n')):
        T = ''.join(nws(line))
        if not T: continue
        Tmap = [i for i, c in enumerate(line) if not c.isspace()]
        tends, q = {len(T)}, 0
        for w in line.split():
            tends.add(q)
            # cuts allowed inside: the known glued word, and all-caps/number words, which the converter
            # compares letter by letter (letter-spaced headings, '07PROGRAMA' glued by pdftotext -raw)
            if w in MANUAL_SPLITS or len(split_tok(w)) > 1: tends |= set(range(q, q + len(w)))
            q += len(w)
        pos = n = 0
        while pos < len(T):
            best, bestc, bestsame = 0, None, False
            nxt = min(b for b in tends if b > pos)
            cands = idx.get(T[pos:pos + K], []) if len(T) - pos >= K else []
            if not cands:
                cands = [m.start() for m in re.finditer(re.escape(T[pos:nxt]), R)]
            for c in cands:
                if c not in Rstarts: continue
                L = 0
                while pos + L < len(T) and c + L < len(R) and T[pos + L] == R[c + L]: L += 1
                while L > 0 and ((pos + L) not in tends or (c + L) not in Rends): L -= 1
                if L:
                    same = ' '.join(line[Tmap[pos]:Tmap[pos + L - 1] + 1].split()) == ' '.join(src[Rmap[c]:Rmap[c + L - 1] + 1].split())
                    if (L, same) > (best, bestsame): best, bestc, bestsame = L, c, same
            if not best:
                bad.append((li + 1, T[pos:pos + 40])); break
            a = ' '.join(line[Tmap[pos]:Tmap[pos + best - 1] + 1].split())
            b = ' '.join(src[Rmap[bestc]:Rmap[bestc + best - 1] + 1].split())
            if a != b: ws_changes.append((li + 1, a, b))
            pos += best; n += 1
        hist[n] += 1
    say(f'[3] text lines: {sum(hist.values())}; found in one piece {hist[1]}; '
        f'cut where the PDF separates title/body/rationale {dict(sorted((k, v) for k, v in hist.items() if k > 1))}')
    if bad: fail(f'{len(bad)} text fragments not found in the rst: {bad[:5]}')
    else: say('    OK - every piece of every text line appears verbatim in the rst')
    spaced = lambda a: sum(len(w) == 1 for w in a.split()) >= 0.5 * len(a.split())
    n_spaced = sum(1 for c in ws_changes if spaced(c[1]))
    say(f'    whitespace-only edits: {len(ws_changes)} ({n_spaced} letter-spaced headings closed up, '
        f'{len(ws_changes) - n_spaced} other, e.g. "Almería– Aguadulce" rejoined)')
    if verbose:
        for c in ws_changes: say(f'      line {c[0]}: {c[1]!r} -> {c[2]!r}')

    # 4. pandoc parse + rendered text
    if use_pandoc and shutil.which('pandoc'):
        r = subprocess.run(['pandoc', '-f', 'rst', '-t', 'json', '--fail-if-warnings', rst_path],
                           capture_output=True, text=True)
        if r.returncode != 0 or r.stderr.strip():
            fail('pandoc: ' + r.stderr.strip()[:2000])
        else:
            ast = json.loads(r.stdout)
            strs, kinds, headers = [], collections.Counter(), collections.Counter()

            def walk(x):
                if isinstance(x, dict):
                    t = x.get('t')
                    if t: kinds[t] += 1
                    if t == 'Str': strs.append(x['c'])
                    if t == 'Header': headers[x['c'][0]] += 1
                    if t in ('Code', 'CodeBlock', 'RawBlock', 'RawInline', 'OrderedList', 'DefinitionList'):
                        fail(f'unexpected {t} in parsed document: {str(x)[:100]}')
                    for v in x.values(): walk(v)
                elif isinstance(x, list):
                    for v in x: walk(v)
            walk(ast)
            cp = collections.Counter(nws(''.join(strs) + ' '.join(comments)))
            say(f'[4] pandoc: headings by level {dict(sorted(headers.items()))}, lists {kinds["BulletList"]}, '
                f'bold {kinds["Strong"]}, italic {kinds["Emph"]}, comments (page furniture) {len(comments)}')
            if cp != ct: fail(f'rendered text differs: only in text {dict(ct - cp)}, only rendered {dict(cp - ct)}')
            else: say('    OK - parses without warnings; rendered text plus comments has exactly the text characters')
    else:
        say('[4] pandoc check skipped')

    # 5. every proposal card in its right place, compared with the PDF itself
    if pdf_path:
        n, shown, missing, errors = check_cards(pdf_path, rst)
        say(f'[5] proposal cards: {n} in the PDF, compared one by one and in order (title, body, rationale); '
            f'{shown} keep their number, the text file has no number for the other {n - shown}')
        for m in missing:
            say(f'    note: PDF p.{m[0]} proposal {m[1]}: a number in the {m[2]} is absent from the text file')
        for e in errors[:20]: fail(e)
        if not errors: say('    OK - every card matches the PDF card at the same position')
    return ok


# =============================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pdf')
    ap.add_argument('-o', '--output', help='output .rst (default: PDF name with .rst)')
    ap.add_argument('--txt', help='use this text file instead of running pdftotext')
    ap.add_argument('--mode', choices=['default', 'raw'], default='default',
                    help="pdftotext mode: 'default' (reading-order heuristics) or 'raw' (content-stream order)")
    ap.add_argument('--save-txt', help='also write the pdftotext text (lines joined) here')
    ap.add_argument('--no-pandoc', action='store_true', help='skip the pandoc parse check')
    ap.add_argument('--verify-only', action='store_true',
                    help='do not write anything: check the existing output .rst against the text')
    ap.add_argument('-v', '--verbose', action='store_true', help='list every whitespace-only edit')
    a = ap.parse_args()
    out_path = a.output or re.sub(r'\.pdf$', '', a.pdf, flags=re.I) + '.rst'

    if a.txt:
        text = open(a.txt, encoding='utf-8').read()
        print(f'text: {a.txt}')
    else:
        raw = run_pdftotext(a.pdf, a.mode)
        text = join_lines(raw) if a.mode == 'default' else raw   # raw mode has no blank-line paragraphs
        print(f'text: pdftotext ({a.mode} mode)')
        if a.save_txt:
            open(a.save_txt, 'w', encoding='utf-8').write(text)

    if a.verify_only:
        print(f'\nverification of {out_path}')
        ok = verify(text, open(out_path, encoding='utf-8').read(), out_path, not a.no_pandoc, a.verbose, a.pdf)
        print('\nRESULT:', 'ALL CHECKS PASSED' if ok else 'FAILED')
        sys.exit(0 if ok else 1)

    layout = extract_layout(a.pdf)
    pages, notes, unmatched, dropped = align(layout, text)
    print(f'layout: {len(layout)} pages; alignment notes: {len(notes)}')
    for n in notes: print('   ', *n)
    if unmatched:
        sys.exit(f'{len(unmatched)} text words could not be placed in the PDF layout, e.g. {unmatched[:10]}')
    odd = [d for d in dropped if not d[2][0].startswith('ClashDisplay')]
    print(f'PDF text absent from the text file: {len(dropped)} units (numbers mostly)'
          + (f'; outside number fonts: {"".join(d[1] for d in odd)!r} on pages {sorted({d[0] for d in odd})}' if odd else ''))

    w = RstWriter()
    rst = w.build(pages)
    open(out_path, 'w', encoding='utf-8').write(rst)
    print(f'wrote {out_path} ({rst.count(chr(10))} lines)')
    problems = [l for l in w.log if l[0].isupper() or 'without' in l[0] or 'orphan' in l[0]]
    for l in w.log: print('   note:', *l)

    print('\nverification')
    ok = verify(text, rst, out_path, use_pandoc=not a.no_pandoc, verbose=a.verbose, pdf_path=a.pdf)
    if problems:
        ok = False
        print(f'FAIL: {len(problems)} layout problems (see notes above)')
    print('\nRESULT:', 'ALL CHECKS PASSED' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
