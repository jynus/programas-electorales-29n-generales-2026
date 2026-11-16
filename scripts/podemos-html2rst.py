#!/usr/bin/env python3
"""
podemos-html2rst.py - Convert the Podemos programme "Paz, casa y trabajo."
(a web page, https://pazcasaytrabajo.es/, saved as HTML: there is no PDF) to
reStructuredText, repeatably, and verify that no text was altered, omitted
or added.

The programme is the page's title and its "Así de básico:" section, the five
measures; the rest of the page (social links, donations, volunteer form,
footer) is not part of it. Read like a screen reader reads it: the text
marked ``aria-hidden="true"`` (the title drawn again on two lines, the "/"
between "01" and "05", decorations) is left out and the text for screen
readers only (``sr-only``: "Medida", "de") kept, so "Medida 01 de 05".

1. Parse the HTML with Python's html.parser; take the elements with the ids
   in PROGRAMME_IDS (latest.json lists the same ones for build.py's check).
2. Write RST:
     * the page's title (h1 "Paz, casa y trabajo.") -> document title
     * "Así de básico:" (h2) -> level 1; "Pulsa una medida para leerla" -> a
       paragraph
     * each measure: "Medida 01 de 05" -> level 2, its title ("Las casas son"
       / "para vivir.", two lines) -> a bold paragraph, its text -> a
       paragraph
3. Verify against the page's text: the RST text is exactly the text pieces
   of those elements, each used once and whole, in any order; pandoc must
   read the same words, without warnings (``verify_blocks`` in
   pdf2rst_common.py, the PDF lines replaced by the HTML text pieces).

Usage:  python3 podemos-html2rst.py Irene_Montero___Paz__casa_y_trabajo.html -o podemos.rst
        python3 podemos-html2rst.py Irene_Montero___Paz__casa_y_trabajo.html --verify-only podemos.rst
"""
import argparse
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2rst_common as C  # noqa: E402

PROGRAMME_IDS = ("hero-title", "contenidos")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP = {"script", "style", "svg", "template", "noscript"}


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def find(self, pred):
        if pred(self):
            yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.find(pred)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:      # tolerate unclosed elements
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(path):
    b = TreeBuilder()
    b.feed(open(path, encoding="utf-8").read())
    b.close()
    return b.root


def hidden(node):
    return node.tag in SKIP or node.attrs.get("aria-hidden") == "true"


def pieces(node):
    """The text pieces of node, as a screen reader gets them."""
    if hidden(node):
        return []
    out = []
    for c in node.children:
        if isinstance(c, str):
            if c.strip():
                out.append(c)
        else:
            out += pieces(c)
    return out


def text(node):
    """Text of node: its pieces with whitespace collapsed; pieces of block
    spans ("Las casas son" / "para vivir.") are separate lines: a space."""
    def raw(n):
        if hidden(n):
            return ""
        parts = []
        for c in n.children:
            if isinstance(c, str):
                parts.append(c)
            else:
                parts.append(f" {raw(c)} " if "block" in c.classes() else raw(c))
        return "".join(parts)
    return re.sub(r"\s+", " ", raw(node)).strip()


def heading(t, ch, over=False):
    return C.heading(C.esc_start(C.esc_inline(t)), ch, over, escape=False)


def paragraph(t, bold=False):
    t = C.esc_start(C.esc_inline(t))
    return C.wrap(f"**{t}**" if bold else t) + [""]


def programme(root):
    found = {i: next(root.find(lambda n: n.attrs.get("id") == i), None) for i in PROGRAMME_IDS}
    missing = [i for i, n in found.items() if n is None]
    if missing:
        raise C.ConvertError(f"elements not found: {', '.join(missing)}")
    return found


def convert(path, report):
    found = programme(parse(path))
    out = heading(text(found["hero-title"]), "#", over=True)
    sec = found["contenidos"]
    counts = {"measures": 0, "paragraphs": 0}
    for n in sec.find(lambda n: n.tag in ("h2", "p", "summary", "details") and not hidden(n)):
        if n.tag == "h2":
            out += heading(text(n), "=")
        elif n.tag == "summary":
            h3 = next(n.find(lambda x: x.tag == "h3"))
            spans = [c for c in h3.children if isinstance(c, Node) and not hidden(c) and text(c)]
            if len(spans) != 2:
                raise C.ConvertError(f"measure heading not understood: {text(h3)!r}")
            out += heading(text(spans[0]), "-") + paragraph(text(spans[1]), bold=True)
            counts["measures"] += 1
        elif n.tag == "p":
            out += paragraph(text(n))
            counts["paragraphs"] += 1
    while out and out[-1] == "":
        out.pop()
    report.append("structure: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return "\n".join(out) + "\n"


def reference(path):
    """The programme's text pieces, one per line (the 'PDF lines' of verify_blocks)."""
    found = programme(parse(path))
    return "\n".join(re.sub(r"\s+", " ", p).strip() for i in PROGRAMME_IDS for p in pieces(found[i])) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert the Podemos programme (HTML) to reStructuredText and verify it")
    ap.add_argument("html")
    ap.add_argument("-o", "--output", default="podemos.rst")
    ap.add_argument("--report")
    ap.add_argument("--verify-only", metavar="RST")
    args = ap.parse_args(argv)
    report = [f"extract : html.parser (Python {sys.version.split()[0]}); elements {', '.join(PROGRAMME_IDS)}"]
    try:
        if args.verify_only:
            rst = open(args.verify_only, encoding="utf-8").read()
        else:
            rst = convert(args.html, report)
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(rst)
            report.insert(0, f"convert : wrote {args.output} ({rst.count(chr(10))} lines)")
        raw = reference(args.html)
        report.append("verify  : (the text pieces of the page stand for the PDF lines)")
        ok = C.verify_blocks(rst, raw, report)
    except C.ConvertError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    report.append("RESULT: " + ("ALL CHECKS PASSED" if ok else "PROBLEMS FOUND"))
    out = "\n".join(report) + "\n"
    sys.stdout.write(out)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
