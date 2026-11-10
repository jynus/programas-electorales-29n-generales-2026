#!/usr/bin/env python3
"""Convert the PP programme "Reconstrucción Nacional" (PDF) to reStructuredText.

Pipeline
  1. extract   pdftotext (poppler-utils) -> plain text, one line per PDF line
  2. clean     join the lines of each text block into one paragraph
               (dropping end-of-line hyphenation), giving the "filtered" text
  3. convert   rebuild the document structure (title, index, chapters, sections,
               numbered measures, placeholder boxes, pull quotes, page markers)
               following the PDF layout, and write RST
  4. verify    compare the RST, token by token, against the raw pdftotext output
               and fail (exit code 1) on anything added, omitted or altered

The only content changes allowed (and checked) are:
  * layout hyphens at the end of a PDF line ("PEQUE-" + "ÑOS" -> "PEQUEÑOS")
  * page numbers, removed by default (--page-numbers keeps them as
    RST comments ".. N", which don't render)
  * a few blocks moved, unchanged, to where the PDF prints them (chapter titles,
    the decorative ‘‘ marks of the pull quotes, the "Permanezca atento..."
    boxes and one sub-heading in section 3.2)

The structural rules in step 3 are specific to this document.

Usage:
  python3 pdf2rst.py LA-RECONSTRUCCION-NACIONAL-1.pdf -o reconstruccion-nacional.rst
  python3 pdf2rst.py input.pdf -o out.rst --save-text filtered.txt   # also keep step 2
  python3 pdf2rst.py input.pdf -o out.rst --page-numbers             # keep page markers
  python3 pdf2rst.py input.pdf --verify-only existing.rst            # only run step 4
"""
import argparse
import collections
import difflib
import re
import shutil
import subprocess
import sys
import textwrap

WIDTH = 79
PLACEHOLDER = 'Permanezca atento a la campaña para conocer esta nueva medida'
CHAPTER_TITLES = ['REPARAR EL ESTADO', 'REGENERAR LA DEMOCRACIA',
                  'REUNIR A LA NACIÓN', 'RECUPERAR LA AMBICIÓN Y LA AUTOESTIMA']
# Where the intro texts start a new paragraph in the PDF (pdftotext loses this).
PARA_STARTS = [
    'Lo urgente es responder', 'Creemos que esta es la forma',
    'Medio siglo después', '¿Y por qué hace falta', 'Sí, el Estado sigue',
    'Y con la democracia ha pasado', 'La Nación también se ha debilitado',
    'Nos hemos acostumbrado', 'El problema no son España',
    '■ Reparar', '■ Regenerar', '■ Reunir', '■ Recuperar', 'Ha llegado el momento. Debemos',
    'Hoy España no necesita', 'El ciudadano lo sufre', 'Cuando falla en lo más básico',
    'Este deterioro tiene arreglo', 'Queremos un Estado fuerte',
    'Una democracia sana exige', 'El sistema tiene cinco momentos', 'Ahí concentraremos',
    'Un Gobierno debe poder gobernar',
    'El resultado es una España', 'Nosotros creemos otra cosa', 'Por eso vamos a reunir',
    'Los españoles no tenemos que pensar igual para volver a hacer cosas juntos. Es suficiente',
    'Creemos en la ambición porque', 'Por eso las ambiciones', 'Queremos que ese joven pueda',
]
UPPER = 'A-ZÁÉÍÓÚÜÑ«¿'
LOWER = 'a-záéíóúüñ'
ADORN = re.compile(r'([=\-~])\1+')


# =============================================================== 1. extract
def extract(pdf, mode=None):
    exe = shutil.which('pdftotext')
    if not exe:
        sys.exit('pdftotext not found: install poppler-utils')
    ver = subprocess.run([exe, '-v'], capture_output=True, text=True)
    if mode is None:
        print('extract :', (ver.stderr or ver.stdout).splitlines()[0])
    res = subprocess.run([exe, '-enc', 'UTF-8'] + ([mode] if mode else []) + [pdf, '-'],
                         capture_output=True, check=True)
    return res.stdout.decode('utf-8')


# =============================================================== 2. clean
def clean(raw):
    """Join the lines of every blank-line separated block into one paragraph."""
    out = []
    for block in raw.split('\n\n'):
        s = ''
        for line in block.split('\n'):
            if s.endswith('-'):
                s = s[:-1] + line          # end-of-line hyphenation
            elif s:
                s = s + ' ' + line
            else:
                s = line
        out.append(s)
    return '\n\n'.join(out)


def is_page_block(i, block):
    """Page numbers: a form feed followed by digits; pages 1 and 7 have no form feed."""
    q = block.replace('\x0c', '')
    return (i == 0 and q == '1') or (i == 11 and q == '7') or \
           (block.startswith('\x0c') and q.isdigit())


# =============================================================== 3. convert
def convert(text, keep_pages=False):
    paras = text.split('\n\n')
    out = []
    state = {'open': None, 'pages': [], 'ph_needed': 0, 'ph_seen': 0, 'qmarks': 0,
             'fortaleceremos_pending': True}

    # ---- tokenise
    items = []
    for i, p in enumerate(paras):
        if p == '':
            continue
        if is_page_block(i, p):
            items.append(('PAGE', p.replace('\x0c', '')))
            continue
        if p.startswith('\x0c'):
            p = p[1:]
            if p == '':
                continue
        assert '\x0c' not in p and '\n' not in p, i
        if p == '‘‘':
            items.append(('QMARK', p))
        elif p == PLACEHOLDER:
            items.append(('PLACEHOLDER', p))
        else:
            items.append(('TEXT', p))

    # ---- helpers
    def join(a, b):
        if not a:
            return b
        if a.endswith('-'):
            return a[:-1] + b
        return a + ' ' + b

    def wrap(s, indent=''):
        return textwrap.fill(s, WIDTH, initial_indent=indent, subsequent_indent=indent,
                             break_long_words=False, break_on_hyphens=False)

    def heading(title, ch, over=False):
        line = ch * len(title)
        return f'{line}\n{title}\n{line}' if over else f'{title}\n{line}'

    def page_comment(n):
        return f'.. {n}'

    def split_measure(num, body):
        """Measure title = leading all-caps run ending in '.'."""
        m = re.search(f'[{LOWER}]', body)
        cut = body.rfind('.', 0, m.start()) if m else len(body) - 1
        title, rest = body[:cut + 1], body[cut + 1:].strip()
        assert title.endswith('.') and not re.search(f'[{LOWER}]', title), (num, body[:80])
        return title, rest

    def emit_measure(num, body):
        if body == '':
            state['ph_needed'] += 1
            out.append(f'.. admonition:: {num}\n\n   *{PLACEHOLDER}*')
            return
        title, rest = split_measure(num, body)
        out.append(wrap(f'**{num} {title}** {rest}'))

    def emit_prose(s, marks=()):
        cuts = []
        for start in PARA_STARTS:
            k = s.find(' ' + start)
            if k != -1:
                assert s.count(' ' + start) == 1, start
                cuts.append(k)
        cuts.sort()
        pieces, prev = [], 0
        for k in cuts:
            pieces.append(s[prev:k])
            prev = k + 1
        pieces.append(s[prev:])
        for i, p in enumerate(pieces):
            out.append(wrap(p, '   ') if p.startswith('■') else wrap(p))
            for n, off in marks:                       # page break fell in this paragraph
                if sum(1 for c in cuts if c < off) == i:
                    out.append(page_comment(n))

    def close():
        b = state['open']
        if b:
            state['open'] = None
            marks = list(state['pages'])
            state['pages'].clear()
            if b['kind'] == 'measure':
                body = b['text']
                if b['num'] == '110':      # PDF puts the 3rd sub-heading of 3.2 here
                    k = body.index(' Una comunidad política se mide')
                    emit_measure('110', body[:k])
                    out.extend(page_comment(n) for n, off in marks if off <= k)
                    out.append(heading('Fortaleceremos el pacto entre personas', '~'))
                    emit_prose(body[k + 1:], [(n, off - k - 1) for n, off in marks if off > k])
                else:
                    emit_measure(b['num'], body)
                    out.extend(page_comment(n) for n, off in marks)
            else:
                emit_prose(b['text'], marks)
        assert not state['pages']

    def start_measure(num, body=''):
        close()
        state['open'] = {'kind': 'measure', 'num': num, 'text': body}

    # ---- front matter
    k = 0
    assert items[0] == ('PAGE', '1')
    out.append(page_comment('1'))
    k = 1
    assert items[k] == ('TEXT', 'RECONSTRUCCIÓN NACIONAL')
    out.append(heading('RECONSTRUCCIÓN NACIONAL', '=', over=True))
    k += 1
    while items[k][0] == 'PAGE':
        out.append(page_comment(items[k][1]))
        k += 1
    assert items[k] == ('TEXT', 'EL PROGRAMA DEL CAMBIO NECESARIO PARA ESPAÑA')
    out.append(heading('EL PROGRAMA DEL CAMBIO NECESARIO PARA ESPAÑA', '-', over=True))
    k += 1

    # ---- body
    while k < len(items):
        kind, s = items[k]
        k += 1
        if kind == 'PAGE':
            if state['open']:
                state['pages'].append((s, len(state['open']['text'])))
            else:
                out.append(page_comment(s))
            continue
        if kind == 'PLACEHOLDER':          # emitted where its measure number is
            state['ph_seen'] += 1
            continue
        if kind == 'QMARK':                # closing pull-quote mark, placed in the epigraph
            state['qmarks'] += 1
            continue

        if s == 'ÍNDICE':
            close()
            out.append(heading('ÍNDICE', '='))
            _, idx = items[k]
            k += 1
            entries = []
            for part in re.split(r'(\d\.\d+ .*? - Pág\. \d+) ?', idx):
                part = part.strip()
                if not part:
                    continue
                if re.match(r'\d\.\d+ ', part):
                    entries.append(('  ', part))
                elif part == 'LA ESPAÑA QUE HAREMOS ENTRE TODOS REPARAR EL ESTADO':
                    entries += [('', 'LA ESPAÑA QUE HAREMOS ENTRE TODOS'), ('', 'REPARAR EL ESTADO')]
                else:
                    entries.append(('', part))
            lines, prev = [], None
            for ind, txt in entries:
                if prev is not None and ind != prev:
                    lines.append('')
                lines.append(f'{ind}- {txt}')
                prev = ind
            out.append('\n'.join(lines))
            continue

        if s.startswith('LA ESPAÑA QUE HAREMOS ENTRE TODOS '):
            close()
            out.append(heading('LA ESPAÑA QUE HAREMOS ENTRE TODOS', '='))
            state['open'] = {'kind': 'prose', 'text': s[len('LA ESPAÑA QUE HAREMOS ENTRE TODOS '):]}
            continue

        m = re.match(r'^(0\d) (.*)$', s)
        if m:                              # chapter: "01 subtitle", ‘‘, quote, "TITLE intro..."
            close()
            num, subtitle = m.groups()
            assert items[k] == ('QMARK', '‘‘')
            k += 1
            state['qmarks'] += 1
            _, quote = items[k]
            _, intro = items[k + 1]
            k += 2
            title = CHAPTER_TITLES[int(num) - 1]
            assert intro.startswith(title + ' ')
            out.append(heading(f'{num} {title}', '='))
            out.append(wrap(f'*{subtitle}*'))
            out.append('.. epigraph::\n\n' + wrap(f'‘‘{quote}‘‘', '   '))
            state['open'] = {'kind': 'prose', 'text': intro[len(title) + 1:]}
            continue

        if re.match(r'^\d\.\d+ ', s):     # section heading (+ maybe first measure)
            close()
            m2 = re.match(rf'^(\d\.\d+ .+?) (\d{{1,3}})(?: ([{UPPER}].*))?$', s)
            if s.endswith(' Reconstruiremos el pacto territorial'):
                out.append(heading(s[:-len(' Reconstruiremos el pacto territorial')], '-'))
                out.append(heading('Reconstruiremos el pacto territorial', '~'))
            elif m2:
                out.append(heading(m2.group(1), '-'))
                start_measure(m2.group(2), m2.group(3) or '')
            else:
                raise SystemExit(f'unparsed section: {s}')
            continue

        if s == 'Reconstruiremos el pacto entre generaciones':
            close()
            out.append(heading(s, '~'))
            continue
        if s == 'Fortaleceremos el pacto entre personas':
            assert state['fortaleceremos_pending']   # already emitted (PDF order)
            state['fortaleceremos_pending'] = False
            continue
        if s == 'EL CAMBIO NECESARIO':
            close()
            out.append(heading(s, '='))
            continue

        if re.fullmatch(r'\d{1,3}', s):
            start_measure(s)
            continue
        m = re.match(rf'^(\d{{1,3}}) ([{UPPER}].*)$', s)
        if m:
            start_measure(m.group(1), m.group(2))
            continue

        b = state['open']
        if b and (b['kind'] == 'measure' or b['text'].endswith('-')
                  or re.match(f'[{LOWER}]', s)):
            b['text'] = join(b['text'], s)
        else:
            close()
            state['open'] = {'kind': 'prose', 'text': s}

    close()
    assert state['ph_needed'] == state['ph_seen'] == 8, state
    assert state['qmarks'] == 8, state
    assert not state['fortaleceremos_pending']
    if not keep_pages:
        out = [blk for blk in out if not re.fullmatch(r'\.\. \d+', blk)]
    return '\n\n'.join(out) + '\n'


# =============================================================== 4. verify
def verify(raw, rst_text, layout, keep_pages=False):
    """Compare the RST against the raw pdftotext output. Returns True if all checks pass."""
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        print('   FAIL:', msg)

    # ---- source: raw pdftotext, page-number blocks set aside, line breaks kept
    src_pages, src_blocks = [], []
    for i, b in enumerate(raw.split('\n\n')):
        if is_page_block(i, b):
            src_pages.append(b.replace('\x0c', ''))
        else:
            src_blocks.append(b.replace('\x0c', ''))
    src = '\n\n'.join(src_blocks)
    for ch in '*`|':
        assert ch not in src, f'source contains RST-special {ch!r}; markup stripping would be ambiguous'

    # ---- target: RST with markup stripped
    lines = rst_text.split('\n')
    rst_pages, content = [], []
    for n, line in enumerate(lines):
        if '\t' in line:
            fail(f'tab on line {n + 1}')
        if m := re.fullmatch(r'\.\. (\d+)', line):
            rst_pages.append(m.group(1))
            continue
        if ADORN.fullmatch(line):
            title = lines[n - 1] if lines[n - 1] and not ADORN.fullmatch(lines[n - 1]) else lines[n + 1]
            if len(line) != len(title):
                fail(f'heading adornment length mismatch on line {n + 1}')
            continue
        if line == '.. epigraph::':
            continue
        if m := re.fullmatch(r'\.\. admonition:: (\d+)', line):
            content.append(m.group(1))
            continue
        if line.startswith('.. '):
            fail(f'unexpected directive/comment on line {n + 1}: {line}')
        if re.match(r'^\s*- (\d\.\d+ |[A-ZÁÉÍÓÚÑ])', line):     # index list bullet
            line = re.sub(r'^\s*- ', '', line)
        content.append(line.strip().replace('**', '').replace('*', ''))
    tgt = ' '.join(c for c in content if c)

    # [1] page numbers
    print('[1] page numbers')
    if not keep_pages:
        if rst_pages:
            fail(f'{len(rst_pages)} page comments present; pass --page-numbers to check a file that keeps them')
        else:
            print(f'   {len(src_pages)} page numbers removed (default; --page-numbers keeps them)')
    elif src_pages != rst_pages:
        fail(f'page sequence differs: pdf={src_pages} rst={rst_pages}')
    else:
        print(f'   {len(src_pages)} page numbers, same order')

    # [2] token sequence; "-\n" is the only form of hyphen that may disappear
    print('[2] token sequence')
    tok_src = re.findall(r'\w+|-[ \t]*\n|[^\w\s]', src)
    tok_src = ['-\n' if re.fullmatch(r'-[ \t]*\n', t) else t for t in tok_src]
    tok_tgt = re.findall(r'\w+|[^\w\s]', tgt)
    sm = difflib.SequenceMatcher(None, tok_src, tok_tgt, autojunk=False)
    joins, dels, ins = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        a, b = tok_src[i1:i2], tok_tgt[j1:j2]
        if (op == 'replace' and len(a) == 3 and a[1] == '-\n' and len(b) == 1
                and a[0] + a[2] == b[0]):
            joins.append((a[0], a[2]))
            continue
        if op in ('delete', 'replace'):
            dels.append(a)
        if op in ('insert', 'replace'):
            ins.append(b)
    # a stray line-end hyphen that was NOT joined would show up as a bare deletion
    moves = []
    for a in list(dels):
        if a in ins:
            ins.remove(a)
            dels.remove(a)
            moves.append(' '.join(a))
    # runs that difflib grouped differently on each side but hold the same tokens
    if dels and collections.Counter(t for a in dels for t in a) == \
            collections.Counter(t for b in ins for t in b) and '-\n' not in sum(dels, []):
        moves += [' '.join(a) for a in dels]
        dels, ins = [], []
    same = sum(blk.size for blk in sm.get_matching_blocks())
    print(f'   pdf tokens {len(tok_src)}, rst tokens {len(tok_tgt)}, in place {same}')
    print(f'   end-of-line hyphens joined: {len(joins)}')
    print(f'   blocks moved unchanged: {len(moves)}')
    for mv in moves:
        print('      ·', mv[:90])
    if dels or ins:
        fail(f'unexplained differences: removed={dels[:5]} added={ins[:5]}')
    else:
        print('   no text added, omitted or altered')

    # [3] character inventory
    print('[3] character inventory (non-whitespace)')
    cs = collections.Counter(c for c in src if not c.isspace())
    ct = collections.Counter(c for c in tgt if not c.isspace())
    cs['-'] -= len(joins)
    if +cs != +ct:
        fail(f'only in pdf: {dict(cs - ct)}  only in rst: {dict(ct - cs)}')
    else:
        print(f'   identical ({sum(ct.values())} characters) once joined hyphens are discounted')

    # [4] pdftotext itself silently re-joins words hyphenated at a line end.
    #     Re-extract with -layout (which keeps those hyphens) to list them and
    #     flag joins that may have swallowed a real hyphen (ranges, compounds).
    print('[4] hyphens joined by pdftotext itself (cross-check with -layout)')
    layout_lines = [l for l in layout.split('\n') if l.strip()]
    layout_words = set(re.findall(r'\w+', layout))
    compounds = set(re.findall(r'\w+-\w+', layout))
    pdft_joins = {}
    for a, b in zip(layout_lines, layout_lines[1:]):
        m, n = re.search(r'(\w+)-\s*$', a), re.match(r'\s*(\w+)', b)
        if m and n:
            pdft_joins[m.group(1) + n.group(1)] = (m.group(1), n.group(1))
    for a, b in joins:
        pdft_joins[a + b] = (a, b)
    tgt_words = set(re.findall(r'\w+', tgt))
    review = sorted({(a, b) for w, (a, b) in pdft_joins.items() if w in tgt_words
                     and ((a.isdigit() and b.isdigit()) or f'{a}-{b}' in compounds)})
    print(f'   {len(pdft_joins)} line-end hyphenations found in the -layout text')
    for a, b in review:
        print(f'   REVIEW: "{a}-" + "{b}" became "{a}{b}" (looks like a real hyphen: range/compound)')
    unmatched = sorted(w for w in tgt_words if w not in layout_words and w not in pdft_joins)
    if unmatched:
        print('   note: not matched in -layout text (multi-column/page-crossing lines):', unmatched)

    # [5] RST structure
    print('[5] RST structure')
    risky = re.compile(r'^(\s*)([-*+•‣⁃] |\d+[.)] |#[.)] |\(?[A-Za-z][.)] |\(?[IVXLCDM]+[.)] |:\w|>>> |\|)')
    for blk in rst_text.split('\n\n'):
        first = blk.split('\n')[0]
        if first.startswith(('.. ', '- ', '  - ')) or ADORN.fullmatch(first):
            continue
        if risky.match(first):
            fail(f'paragraph start may be parsed as markup: {first[:60]}')
        if ' '.join(blk.split('\n')).count('**') % 2:
            fail(f'unbalanced ** in: {first[:60]}')
    found = re.findall(r'^\*\*(\d+) |^\.\. admonition:: (\d+)$', rst_text, re.M)
    nums = sorted(int(a or b) for a, b in found)
    if nums != list(range(1, 250)):
        fail('measures are not numbered 1..249 without gaps')
    else:
        n_ph = len(re.findall(r'^\.\. admonition::', rst_text, re.M))
        print(f'   measures 1..249 complete ({249 - n_ph} with text, {n_ph} placeholders)')
    try:
        import docutils.core
        import docutils.utils
        docutils.core.publish_doctree(rst_text, settings_overrides={
            'report_level': docutils.utils.Reporter.WARNING_LEVEL,
            'halt_level': docutils.utils.Reporter.WARNING_LEVEL})
        print('   docutils parse: no warnings')
    except ImportError:
        print('   docutils not installed: skipped full parse (pip install docutils to enable)')
    except Exception as e:      # docutils raises SystemMessage on warnings at halt_level
        fail(f'docutils: {e}')

    print('RESULT:', 'ALL CHECKS PASSED' if ok else 'PROBLEMS FOUND')
    return ok


# =============================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('pdf')
    ap.add_argument('-o', '--output', default='reconstruccion-nacional.rst')
    ap.add_argument('--save-text', metavar='TXT', help='also write the cleaned text (step 2)')
    ap.add_argument('--page-numbers', action='store_true',
                    help='keep the page numbers as ".. N" RST comments (removed by default)')
    ap.add_argument('--verify-only', metavar='RST', help='verify an existing RST file and exit')
    args = ap.parse_args()

    raw = extract(args.pdf)
    if args.verify_only:
        rst = open(args.verify_only, encoding='utf-8').read()
    else:
        text = clean(raw)
        if args.save_text:
            open(args.save_text, 'w', encoding='utf-8').write(text)
        rst = convert(text, keep_pages=args.page_numbers)
        open(args.output, 'w', encoding='utf-8').write(rst)
        print(f'convert : wrote {args.output} ({rst.count(chr(10))} lines)')
    print('verify  :')
    layout = extract(args.pdf, '-layout')
    sys.exit(0 if verify(raw, rst, layout, keep_pages=args.page_numbers) else 1)


if __name__ == '__main__':
    main()
