"""Command-line interface.

    accessiblepdf convert report.pdf other.pdf -o out/
    accessiblepdf convert https://example.edu/files/guide.pdf -o out/
    accessiblepdf crawl https://ecrt.umich.edu/ -o out/
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from . import __version__
from .crawl import PdfLink, download_pdfs, find_pdf_links
from .extract import ExtractOptions, extract_document
from .model import Document
from .render import THEMES, RenderOptions, render_html
from .report import alt_template, json_report, markdown_report, status


@dataclass
class Result:
    source: str
    html_path: Optional[str]
    report_path: Optional[str]
    status: str
    title: str = ""
    pages: int = 0
    error: Optional[str] = None
    link: Optional[PdfLink] = None


def _slug(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r"[^\w\-]+", "-", stem).strip("-").lower() or "document"


def convert_one(pdf_path: str, out_dir: str, args, pdf_href: Optional[str], copy_pdf: bool) -> Result:
    slug = _slug(pdf_path)
    html_name = f"{slug}.html"
    files_dir = f"{slug}_files"
    alt_path = os.path.join(out_dir, f"{slug}.alt-text.json")

    alt_text = {}
    if os.path.exists(alt_path):
        with open(alt_path, encoding="utf-8") as fh:
            alt_text.update({k: v for k, v in json.load(fh).items() if v is not None})
    if args.alt_text:
        with open(args.alt_text, encoding="utf-8") as fh:
            alt_text.update({k: v for k, v in json.load(fh).items() if v is not None})

    opts = ExtractOptions(
        title=args.title, lang=args.lang, ocr=args.ocr, ocr_lang=args.ocr_lang, alt_text=alt_text,
        detect_graphics=not args.no_graphics,
    )
    doc: Document = extract_document(pdf_path, opts)

    if copy_pdf:
        dest = os.path.join(out_dir, os.path.basename(pdf_path))
        if os.path.abspath(dest) != os.path.abspath(pdf_path):
            shutil.copyfile(pdf_path, dest)
        pdf_href = urllib.parse.quote(os.path.basename(pdf_path))

    figures = doc.figures
    if figures and not args.embed_images:
        os.makedirs(os.path.join(out_dir, files_dir), exist_ok=True)
    for f in figures:
        f.filename = f"{f.id}.{f.ext}"
        if not args.embed_images:
            with open(os.path.join(out_dir, files_dir, f.filename), "wb") as fh:
                fh.write(f.data)

    ropts = RenderOptions(
        theme=args.theme,
        fragment=args.fragment,
        heading_offset=args.heading_offset if args.heading_offset is not None else (1 if args.fragment else 0),
        embed_images=args.embed_images,
        image_dir=files_dir,
        pdf_href=pdf_href,
        show_page_numbers=args.page_numbers,
        toc=not args.no_toc,
        contact=args.contact,
        site_name=args.site_name,
    )
    with open(os.path.join(out_dir, html_name), "w", encoding="utf-8") as fh:
        fh.write(render_html(doc, ropts))

    alt_name = None
    if figures:
        existing = {}
        if os.path.exists(alt_path):
            with open(alt_path, encoding="utf-8") as fh:
                existing = json.load(fh)
        merged = alt_template(doc)
        for k, v in existing.items():
            if v is not None:
                merged[k] = v
        with open(alt_path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, ensure_ascii=False)
        alt_name = os.path.basename(alt_path)

    report_path = os.path.join(out_dir, f"{slug}.report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(markdown_report(doc, html_name, alt_name))
    if args.json:
        with open(os.path.join(out_dir, f"{slug}.report.json"), "w", encoding="utf-8") as fh:
            fh.write(json_report(doc, html_name))

    return Result(pdf_path, os.path.join(out_dir, html_name), report_path, status(doc), doc.title, doc.page_count)


def _write_index(results: list[Result], out_dir: str, theme: str) -> str:
    t = THEMES.get(theme, THEMES["default"])
    rows = []
    for r in results:
        name = html.escape(r.title or os.path.basename(r.source))
        if r.html_path:
            doc_cell = f'<a href="{html.escape(os.path.basename(r.html_path))}">{name}</a>'
            rep = f'<a href="{html.escape(os.path.basename(r.report_path))}">Report<span class="vh"> for {name}</span></a>'
        else:
            doc_cell, rep = name, html.escape(r.error or "")
        src = html.escape(r.link.pdf_url if r.link else os.path.basename(r.source))
        rows.append(f"<tr><td>{doc_cell}</td><td>{src}</td><td>{r.pages or ''}</td>"
                    f"<td>{html.escape(r.status)}</td><td>{rep}</td></tr>")
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Converted documents</title>
<style>
body {{ font-family: system-ui, sans-serif; color: {t['text']}; background: {t['bg']}; margin: 0; line-height: 1.5; }}
main {{ max-width: 64rem; margin: 0 auto; padding: 1rem; }}
h1 {{ color: {t['heading']}; }}
a {{ color: {t['link']}; }}
a:focus-visible {{ outline: 3px solid {t['focus']}; outline-offset: 2px; }}
.wrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid {t['border']}; padding: 0.4em 0.6em; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
th {{ background: {t['table_head']}; }}
.vh {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }}
</style>
</head>
<body>
<main>
<h1>Converted documents</h1>
<p>Status meanings: <strong>NOT READY</strong> means something must be fixed before publishing (usually missing image
descriptions); <strong>READY AFTER REVIEW</strong> means a person should check the items in the report.</p>
<div class="wrap" role="region" aria-label="Converted documents" tabindex="0">
<table>
<thead><tr><th scope="col">Document</th><th scope="col">Original PDF</th><th scope="col">Pages</th>
<th scope="col">Status</th><th scope="col">Review</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</div>
</main>
</body>
</html>
"""
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-o", "--out", default="accessible-html", help="output folder (default: accessible-html)")
    p.add_argument("--theme", choices=sorted(THEMES), default="default", help="colour theme")
    p.add_argument("--fragment", action="store_true",
                   help="write only the content (no <html>/<head>) for pasting into a CMS such as WordPress; "
                        "headings start at h2")
    p.add_argument("--heading-offset", type=int, default=None, help="shift all heading levels by N")
    p.add_argument("--embed-images", action="store_true", help="embed images in the HTML instead of a _files folder")
    p.add_argument("--lang", help="language code if the PDF does not declare one, e.g. en, es")
    p.add_argument("--title", help="page title (single-document runs)")
    p.add_argument("--alt-text", help="JSON file mapping figure ids to alt text ('' = decorative)")
    p.add_argument("--ocr", action="store_true", help="OCR scanned pages (requires Tesseract)")
    p.add_argument("--ocr-lang", default="eng", help="Tesseract language (default: eng)")
    p.add_argument("--no-graphics", action="store_true", help="do not turn vector charts/diagrams into images")
    p.add_argument("--page-numbers", action="store_true", help="show the original page numbers as markers")
    p.add_argument("--no-toc", action="store_true", help="omit the table of contents")
    p.add_argument("--contact", help="accessibility contact (email or URL) shown in the footer")
    p.add_argument("--site-name", help="site name shown at the top of full pages")
    p.add_argument("--json", action="store_true", help="also write a machine-readable JSON report")
    p.add_argument("--strict", action="store_true", help="exit with status 2 if any document is NOT READY")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="accessiblepdf",
        description="Convert PDFs into WCAG 2.1 AA accessible web pages, with a review report for each.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="convert PDF files, folders of PDFs, or PDF URLs")
    c.add_argument("inputs", nargs="*", help="PDF files, folders, or https:// links to PDFs")
    c.add_argument("--url-list", help="text file with one PDF link per line (lines starting with # are ignored)")
    c.add_argument("--pdf-base-url", help="link the 'Download the original PDF' text to BASE_URL/<file name>")
    c.add_argument("--no-pdf-link", action="store_true", help="do not link to or copy the original PDF")
    _add_common(c)

    w = sub.add_parser("crawl", help="find the PDFs linked from web pages and convert them")
    w.add_argument("pages", nargs="+",
                   help="web page URLs to scan for PDF links, or pages saved from a browser (.html) with --base-url")
    w.add_argument("--base-url", help="web address of a saved page, used to resolve its relative links")
    w.add_argument("--any-domain", action="store_true",
                   help="also convert PDFs hosted on other domains (default: same site only)")
    w.add_argument("--delay", type=float, default=1.0, help="seconds between downloads (default: 1)")
    w.add_argument("--list-only", action="store_true", help="only list the PDF links found")
    w.add_argument("--no-pdf-link", action="store_true", help="do not link back to the original PDF")
    _add_common(w)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    results: list[Result] = []

    if args.command == "convert":
        pdfs: list[tuple[str, Optional[str], Optional[PdfLink]]] = []
        url_links = []
        inputs = list(args.inputs)
        if args.url_list:
            with open(args.url_list, encoding="utf-8") as fh:
                inputs += [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        if not inputs:
            print("error: give PDF files, folders, links, or --url-list", file=sys.stderr)
            return 1
        for item in inputs:
            if re.match(r"^https?://", item):
                url_links.append(PdfLink("", item, ""))
            elif os.path.isdir(item):
                for root, _, files in os.walk(item):
                    pdfs += [(os.path.join(root, f), None, None) for f in sorted(files) if f.lower().endswith(".pdf")]
            elif os.path.isfile(item):
                pdfs.append((item, None, None))
            else:
                print(f"error: {item} not found", file=sys.stderr)
                return 1
        tmp = None
        if url_links:
            tmp = tempfile.mkdtemp(prefix="accessiblepdf-")
            for link, path, err in download_pdfs(url_links, tmp, delay=0.5):
                if path:
                    pdfs.append((path, link.pdf_url, link))
                else:
                    results.append(Result(link.pdf_url, None, None, "ERROR", error=err, link=link))
        for path, href, link in pdfs:
            copy = not args.no_pdf_link and href is None and not args.pdf_base_url
            if href is None and args.pdf_base_url and not args.no_pdf_link:
                href = args.pdf_base_url.rstrip("/") + "/" + urllib.parse.quote(os.path.basename(path))
            if args.no_pdf_link:
                href = None
            results.append(_safe_convert(path, args, href, copy, link))
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    else:  # crawl
        links: list[PdfLink] = []
        for page in args.pages:
            try:
                if os.path.isfile(page):
                    if not args.base_url:
                        print(f"error: {page} is a saved file; add --base-url with the page's web address",
                              file=sys.stderr)
                        return 1
                    found = find_pdf_links(args.base_url, same_site_only=not args.any_domain, saved_html=page)
                else:
                    found = find_pdf_links(page, same_site_only=not args.any_domain)
            except Exception as exc:
                print(f"error: could not read {page}: {exc}", file=sys.stderr)
                continue
            print(f"{page}: {len(found)} PDF link(s)")
            links += found
        if args.list_only:
            for l in links:
                print(f"  {l.pdf_url}  [{l.link_text}]")
            return 0
        pdf_dir = os.path.join(args.out, "_downloaded")
        for link, path, err in download_pdfs(links, pdf_dir, delay=args.delay):
            if not path:
                results.append(Result(link.pdf_url, None, None, "ERROR", error=err, link=link))
                continue
            results.append(_safe_convert(path, args, None if args.no_pdf_link else link.pdf_url, False, link))
        _write_link_map(results, args.out)

    if not results:
        print("No PDFs converted.")
        return 1
    index = _write_index(results, args.out, args.theme)
    width = max(len(os.path.basename(r.source)) for r in results)
    print()
    for r in results:
        detail = r.html_path if r.html_path else r.error
        print(f"  {r.status:<19} {os.path.basename(r.source):<{width}}  {detail}")
    print(f"\nSummary page: {index}")
    if any(r.status == "NOT READY" for r in results):
        print("Documents marked NOT READY need fixes listed in their .report.md before publishing.")
    if args.strict and any(r.status in ("NOT READY", "ERROR") for r in results):
        return 2
    return 0


def _safe_convert(path, args, href, copy, link) -> Result:
    try:
        r = convert_one(path, args.out, args, href, copy)
        r.link = link
        return r
    except Exception as exc:
        return Result(path, None, None, "ERROR", error=f"{type(exc).__name__}: {exc}", link=link)


def _write_link_map(results: list[Result], out_dir: str) -> None:
    path = os.path.join(out_dir, "link-map.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["found_on_page", "pdf_url", "link_text", "html_file", "status"])
        for r in results:
            if r.link:
                w.writerow([r.link.page_url, r.link.pdf_url, r.link.link_text,
                            os.path.basename(r.html_path) if r.html_path else "", r.status if not r.error else r.error])


if __name__ == "__main__":
    sys.exit(main())
