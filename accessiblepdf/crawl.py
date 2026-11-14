"""Find and download the PDFs linked from web pages."""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

USER_AGENT = "accessiblepdf/1.0 (PDF to accessible HTML converter)"
MAX_PDF_BYTES = 100 * 1024 * 1024


@dataclass
class PdfLink:
    page_url: str
    pdf_url: str
    link_text: str


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []
        elif tag == "img" and self._href is not None:
            alt = dict(attrs).get("alt")
            if alt:
                self._text.append(alt)

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._href = None


def _fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"larger than {max_bytes // (1024 * 1024)} MB")
        return data, ctype


def _robots_allows(url: str, cache: dict) -> bool:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            # Fetched with our own User-Agent: RobotFileParser.read() uses Python's default one,
            # which some sites block, and it then wrongly treats the whole site as disallowed.
            data, _ = _fetch(base + "/robots.txt", 1024 * 1024)
            rp.parse(data.decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as exc:
            # RFC 9309: a 4xx robots.txt means no restrictions; 5xx means assume full disallow.
            rp = None if 400 <= exc.code < 500 else False
        except Exception:
            rp = False
        cache[base] = rp
    rp = cache[base]
    if rp is False:
        return False
    return rp is None or rp.can_fetch(USER_AGENT, url)


def find_pdf_links(page_url: str, same_site_only: bool = True, saved_html: Optional[str] = None) -> list[PdfLink]:
    """PDF links on a page. With saved_html (a page saved from a browser), page_url is only used
    to resolve relative links, so sites that block automated page requests still work."""
    if saved_html is not None:
        with open(saved_html, "rb") as fh:
            data, ctype = fh.read(), ""
    else:
        try:
            data, ctype = _fetch(page_url, 20 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                raise RuntimeError(
                    f"the site refused the request (HTTP {exc.code}); it may block automated page requests. "
                    "Save the page from your browser (File > Save Page As, 'HTML only') and run: "
                    f"accessiblepdf crawl saved.html --base-url {page_url}"
                ) from exc
            raise
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        charset = m.group(1)
    parser = _LinkParser()
    parser.feed(data.decode(charset, errors="replace"))
    host = urllib.parse.urlsplit(page_url).hostname or ""
    seen = set()
    out = []
    for href, text in parser.links:
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        absolute = urllib.parse.urljoin(page_url, href)
        path = urllib.parse.urlsplit(absolute).path.lower()
        if not path.endswith(".pdf"):
            continue
        if same_site_only and not _same_site(urllib.parse.urlsplit(absolute).hostname or "", host):
            continue
        key = urllib.parse.urldefrag(absolute)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(PdfLink(page_url, key, text))
    return out


def _same_site(a: str, b: str) -> bool:
    """True for the same host or two hosts under the same registered domain (e.g. *.umich.edu)."""
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    tail = lambda h: ".".join(h.split(".")[-2:])
    return tail(a) == tail(b)


def download_pdfs(links: list[PdfLink], dest: str, delay: float = 1.0, robots_cache: Optional[dict] = None
                  ) -> list[tuple[PdfLink, Optional[str], Optional[str]]]:
    """Download each PDF. Returns (link, local path or None, error or None)."""
    os.makedirs(dest, exist_ok=True)
    robots_cache = robots_cache if robots_cache is not None else {}
    results = []
    used = set()
    for i, link in enumerate(links):
        if not _robots_allows(link.pdf_url, robots_cache):
            results.append((link, None, "blocked by robots.txt"))
            continue
        name = os.path.basename(urllib.parse.unquote(urllib.parse.urlsplit(link.pdf_url).path)) or "document.pdf"
        name = re.sub(r"[^\w.\-]+", "-", name)
        stem, ext = os.path.splitext(name)
        n = 2
        while name.lower() in used:
            name = f"{stem}-{n}{ext}"
            n += 1
        used.add(name.lower())
        try:
            data, _ = _fetch(link.pdf_url, MAX_PDF_BYTES)
            if not data.startswith(b"%PDF"):
                results.append((link, None, "not a PDF file"))
                continue
            path = os.path.join(dest, name)
            with open(path, "wb") as fh:
                fh.write(data)
            results.append((link, path, None))
        except Exception as exc:
            results.append((link, None, str(exc)))
        if i < len(links) - 1:
            time.sleep(delay)
    return results
