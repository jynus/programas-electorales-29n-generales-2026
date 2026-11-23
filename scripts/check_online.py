#!/usr/bin/env python3
"""
check_online.py - Check whether the parties have changed their published
programmes: download every program_url of documentos_originales/latest.json
and compare its SHA-256 with the one recorded there.

Nothing in the repository is modified. With --save DIR, every document that
changed is saved in DIR (named as its "file" in latest.json) so it can be
reviewed and, if it is a new version, added like any other original.

A site whose HTTPS certificate has expired fails with CERTIFICATE_VERIFY_FAILED:
that is the site's fault, not a missing library. --no-verify-tls downloads
from it anyway: the download is still compared with the SHA-256 recorded in
latest.json, so the worst a tampered download can do is show as "CAMBIADO";
review it before adding it as a new version.

Usage:  python3 scripts/check_online.py               # report only
        python3 scripts/check_online.py --save nuevos  # also keep the changed files
        python3 scripts/check_online.py --only pnv pp  # restrict to some parties
        python3 scripts/check_online.py --only compromis_front_valencia --no-verify-tls

Exit status: 0 nothing changed, 1 some document changed, 2 some download failed.
"""
import argparse
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "documentos_originales", "latest.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def fetch(url, timeout, context=None):
    url = urllib.parse.quote(url, safe=":/?&=%#+~@!$,;")     # a space in a path, as "%20"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
        return r.read(), r.headers.get("Content-Type", ""), r.headers.get("Last-Modified", "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save", metavar="DIR", help="save the documents that changed in DIR")
    ap.add_argument("--only", nargs="+", metavar="PARTY", help="only these party ids")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--no-verify-tls", action="store_true",
                    help="download even when the site's certificate is not valid (expired...)")
    args = ap.parse_args(argv)
    context = None
    if args.no_verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        print("aviso: sin verificar los certificados TLS (--no-verify-tls)\n")

    entries = json.load(open(MANIFEST, encoding="utf-8"))
    if args.only:
        entries = [e for e in entries if e["party"] in args.only]
    changed, failed = [], []
    for e in sorted(entries, key=lambda e: (e["party"], e["language"])):
        name = e.get("file") or os.path.basename(e["program_url"].split("?")[0])
        label = f"{e['party']} ({e['language']})"
        try:
            data, ctype, modified = fetch(e["program_url"], args.timeout, context)
        except (urllib.error.URLError, OSError, ValueError) as err:
            hint = (" (certificado del sitio no válido: prueba --no-verify-tls)"
                    if "CERTIFICATE_VERIFY_FAILED" in str(err) else "")
            print(f"ERROR     {label:32} {e['program_url']}: {err}{hint}")
            failed.append(label)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest == e["sha256sum"].lower():
            print(f"igual     {label:32} {name}")
            continue
        extra = ", ".join(x for x in (ctype, f"Last-Modified: {modified}" if modified else "") if x)
        print(f"CAMBIADO  {label:32} {name}: {len(data)} bytes, sha256 {digest} ({extra})")
        changed.append(label)
        if args.save:
            os.makedirs(args.save, exist_ok=True)
            with open(os.path.join(args.save, name), "wb") as f:
                f.write(data)
    print(f"\n{len(entries)} documentos: {len(changed)} cambiados, {len(failed)} sin descargar, "
          f"{len(entries) - len(changed) - len(failed)} iguales")
    if failed:
        return 2
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
