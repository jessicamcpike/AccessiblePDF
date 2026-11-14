"""Turn a PDF into the intermediate Document model.

The extractor works from the visual layout of the PDF (font sizes, weights,
positions) because most real-world PDFs are untagged or badly tagged. Anything
it has to guess is recorded as an Issue so a person can verify it.
"""

from __future__ import annotations

import functools
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import pymupdf

from .model import (
    Document,
    Figure,
    Footnote,
    Heading,
    Issue,
    ListBlock,
    ListItem,
    PageAnchor,
    Paragraph,
    Span,
    Table,
    TableCell,
)


# --------------------------------------------------------------------------- options


@dataclass
class ExtractOptions:
    title: Optional[str] = None
    lang: Optional[str] = None
    ocr: bool = False
    ocr_lang: str = "eng"
    alt_text: dict = field(default_factory=dict)  # figure id -> alt ("" = decorative)
    detect_graphics: bool = True
    strip_headers_footers: bool = True


# --------------------------------------------------------------------------- patterns

BULLET_CHARS = "•●○◦▪▫■□‣⁃∙·–—-*➢➤►▶▸✓✔❖◆◇"
BULLET_RE = re.compile(r"^\s*([" + re.escape(BULLET_CHARS) + r"]|[-])\s+")
BULLET_ONLY_RE = re.compile(r"^\s*([" + re.escape(BULLET_CHARS) + r"]|[-])\s*$")
ORDERED_RE = re.compile(r"^\s*\(?((\d{1,2})|([a-zA-Z])|([ivxlcdmIVXLCDM]{1,6}))[\.\)]\s+(?!\d{3}[-.\s]\d{4})")
BARE_MARKER_RE = re.compile(r"^\s*(\d{1,3}|[a-zA-Z])\s*$")
ORDERED_ONLY_RE = re.compile(r"^\s*\(?((\d{1,3})|([a-zA-Z])|([ivxlcdmIVXLCDM]{1,6}))[\.\)]\s*$")
PAGE_NUMBER_RE = re.compile(
    r"^\s*(page\s*)?[-–]?\s*\d{1,4}\s*[-–]?(\s*(of|/)\s*\d{1,4})?\s*$", re.IGNORECASE
)
JUNK_TITLE_RE = re.compile(
    r"^(microsoft\s+(word|powerpoint|excel)|untitled|document\d*$|slide\s*\d+$|"
    r".*\.(docx?|pdf|pptx?|indd|xlsx?|rtf|txt)$)",
    re.IGNORECASE,
)
TERMINAL_PUNCT = tuple('.!?:;"”’)')
CAPTION_TABLE_RE = re.compile(r"^\s*table\s+\d+[\.:\s]", re.IGNORECASE)
CAPTION_FIGURE_RE = re.compile(r"^\s*(figure|fig\.|chart|graph|image)\s+\d+[\.:\s]", re.IGNORECASE)
BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)
ITALIC_FONT_RE = re.compile(r"italic|oblique", re.IGNORECASE)

HEADING_RATIO = 1.12  # text this much larger than body text is treated as a heading

ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def roman_to_int(s: str) -> Optional[int]:
    s = s.lower()
    total, prev = 0, 0
    for ch in reversed(s):
        v = ROMAN.get(ch)
        if v is None:
            return None
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total or None


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xad", "")  # soft hyphen
    text = "".join(ch for ch in text if ch in "\t\n" or unicodedata.category(ch)[0] != "C" or ch >= "")
    return text


def norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


# --------------------------------------------------------------------------- raw layout units


@dataclass
class RawSpan:
    text: str
    size: float
    bold: bool
    italic: bool
    sup: bool
    bbox: tuple
    href: Optional[str] = None


@dataclass
class Line:
    page: int
    block: tuple  # (page, block number) identity
    bbox: tuple
    spans: list[RawSpan]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @functools.cached_property
    def size(self) -> float:
        total = sum(len(s.text.strip()) for s in self.spans) or 1
        return round(sum(s.size * len(s.text.strip()) for s in self.spans) / total * 2) / 2

    @functools.cached_property
    def bold(self) -> bool:
        chars = [s for s in self.spans if s.text.strip()]
        n = sum(len(s.text.strip()) for s in chars)
        b = sum(len(s.text.strip()) for s in chars if s.bold)
        return n > 0 and b / n >= 0.9

    @property
    def height(self) -> float:
        return max(1.0, self.bbox[3] - self.bbox[1])


@dataclass
class PageItem:
    kind: str  # "text", "table", "figure"
    bbox: tuple
    lines: list[Line] = field(default_factory=list)
    payload: object = None

    @property
    def yc(self) -> float:
        if self.kind == "text" and self.lines:
            b = self.lines[0].bbox
            return (b[1] + b[3]) / 2
        return self.bbox[1]


@dataclass
class RawPara:
    lines: list[Line]
    page: int
    marker: Optional[str] = None
    marker_x: float = 0.0

    @property
    def text(self) -> str:
        return " ".join(l.text.strip() for l in self.lines).strip()

    @property
    def size(self) -> float:
        total = sum(len(l.text.strip()) for l in self.lines) or 1
        return round(sum(l.size * len(l.text.strip()) for l in self.lines) / total * 2) / 2

    @property
    def bold(self) -> bool:
        return all(l.bold for l in self.lines if l.text.strip())

    @property
    def x0(self) -> float:
        return self.lines[0].bbox[0]


# --------------------------------------------------------------------------- helpers


def _rect_overlap_ratio(a, b) -> float:
    """Fraction of rect a's area that lies inside rect b."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return (x1 - x0) * (y1 - y0) / area


def _center_in(a, b) -> bool:
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def _link_targets(page) -> list[tuple[tuple, str]]:
    out = []
    for link in page.get_links():
        rect = tuple(link.get("from", (0, 0, 0, 0)))
        if link.get("kind") == pymupdf.LINK_URI and link.get("uri"):
            out.append((rect, link["uri"]))
        elif link.get("kind") in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and link.get("page", -1) >= 0:
            out.append((rect, f"#page-{link['page'] + 1}"))
    return out


def _span_from_dict(s: dict, links) -> Optional[RawSpan]:
    text = clean_text(s.get("text", ""))
    if not text:
        return None
    font = s.get("font", "")
    flags = s.get("flags", 0)
    bbox = tuple(s["bbox"])
    href = None
    for rect, target in links:
        if _rect_overlap_ratio(bbox, rect) > 0.5:
            href = target
            break
    return RawSpan(
        text=text,
        size=float(s.get("size", 0)),
        bold=bool(flags & 16) or bool(BOLD_FONT_RE.search(font)),
        italic=bool(flags & 2) or bool(ITALIC_FONT_RE.search(font)),
        sup=bool(flags & 1),
        bbox=bbox,
        href=href,
    )


def _order_items(items: list[PageItem], page_width: float) -> tuple[list[PageItem], bool]:
    """Sort page items into reading order, handling simple two-column layouts."""

    def cmp(a: PageItem, b: PageItem) -> int:
        if abs(a.yc - b.yc) < 3:
            return -1 if a.bbox[0] < b.bbox[0] else (1 if a.bbox[0] > b.bbox[0] else 0)
        return -1 if a.yc < b.yc else 1

    mid = page_width / 2

    def column(it: PageItem) -> int:
        if it.bbox[2] <= mid + 6:
            return 0
        if it.bbox[0] >= mid - 6:
            return 1
        return -1

    def chars(it):
        return sum(len(l.text.strip()) for l in it.lines)

    texts = [i for i in items if i.kind == "text"]
    total = sum(chars(i) for i in texts) or 1
    left = [i for i in texts if column(i) == 0]
    right = [i for i in texts if column(i) == 1]
    two_col = (
        len(left) >= 2
        and len(right) >= 2
        and sum(chars(i) for i in left) / total > 0.2
        and sum(chars(i) for i in right) / total > 0.2
        # Columns must actually overlap vertically, otherwise it is just indentation.
        and min(i.bbox[3] for i in left + right) < max(i.bbox[1] for i in left + right)
        and _columns_overlap(left, right)
    )
    if not two_col:
        return sorted(items, key=functools.cmp_to_key(cmp)), False

    ordered: list[PageItem] = []
    seg_l: list[PageItem] = []
    seg_r: list[PageItem] = []
    for it in sorted(items, key=lambda i: i.bbox[1]):
        col = column(it)
        if col == -1:
            ordered += sorted(seg_l, key=functools.cmp_to_key(cmp))
            ordered += sorted(seg_r, key=functools.cmp_to_key(cmp))
            seg_l, seg_r = [], []
            ordered.append(it)
        elif col == 0:
            seg_l.append(it)
        else:
            seg_r.append(it)
    ordered += sorted(seg_l, key=functools.cmp_to_key(cmp))
    ordered += sorted(seg_r, key=functools.cmp_to_key(cmp))
    return ordered, True


def _columns_overlap(left, right) -> bool:
    l0, l1 = min(i.bbox[1] for i in left), max(i.bbox[3] for i in left)
    r0, r1 = min(i.bbox[1] for i in right), max(i.bbox[3] for i in right)
    overlap = min(l1, r1) - max(l0, r0)
    return overlap > 0.3 * min(l1 - l0, r1 - r0)


def _absorb_labels(page, r: tuple, page_rect, margin: float = 20) -> tuple:
    """Grow a chart's rectangle to include short text labels just outside it (axis labels, legends)."""
    lines: dict = {}
    for x0, y0, x1, y1, word, bno, lno, _ in page.get_text("words"):
        key = (bno, lno)
        if key in lines:
            b, words = lines[key]
            lines[key] = ((min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)), words + [word])
        else:
            lines[key] = ((x0, y0, x1, y1), [word])
    grown = (r[0] - margin, r[1] - margin, r[2] + margin, r[3] + margin)
    out = list(r)
    for bbox, words in lines.values():
        cy = (bbox[1] + bbox[3]) / 2
        # Labels beside or below the drawing; text above is usually a heading or title, so keep it as text.
        if _center_in(bbox, r) or (_center_in(bbox, grown) and cy >= r[1] and len(" ".join(words)) <= 40):
            out = [min(out[0], bbox[0]), min(out[1], bbox[1]), max(out[2], bbox[2]), max(out[3], bbox[3])]
    pad = 4
    return (max(page_rect.x0, out[0] - pad), max(page_rect.y0, out[1] - pad),
            min(page_rect.x1, out[2] + pad), min(page_rect.y1, out[3] + pad))


def _is_white_fill(d: dict) -> bool:
    fill, stroke = d.get("fill"), d.get("color")
    white = lambda c: c is not None and all(v > 0.97 for v in c)
    return white(fill) and (stroke is None or white(stroke))


def _attach_detached_superscripts(items: list) -> None:
    """Footnote numbers drawn as their own text object: join them to the line they follow."""
    lines = [l for it in items if it.kind == "text" for l in it.lines]
    for it in [i for i in items if i.kind == "text"]:
        for tiny in list(it.lines):
            t = tiny.text.strip()
            if not (t.isdigit() and len(t) <= 3):
                continue
            for l in lines:
                if l is tiny or tiny.spans[0].size > l.size * 0.8:
                    continue
                b = l.bbox
                overlap = min(b[3], tiny.bbox[3]) - max(b[1], tiny.bbox[1])
                if overlap > 0 and -2 <= tiny.bbox[0] - b[2] <= 6:
                    span = tiny.spans[0]
                    span.sup = True
                    span.text = t
                    l.spans.append(span)
                    l.bbox = (b[0], min(b[1], tiny.bbox[1]), tiny.bbox[2], b[3])
                    it.lines.remove(tiny)
                    break
        if not it.lines:
            items.remove(it)


def _mark_superscripts(spans: list, line_bbox: tuple) -> None:
    """Small raised text (footnote references) is not always flagged as superscript in the PDF."""
    dominant = max(spans, key=lambda s: len(s.text.strip())).size
    for s in spans:
        t = s.text.strip()
        if t and len(t) <= 4 and s.size <= dominant * 0.8 and s.bbox[3] <= line_bbox[3] - 0.12 * dominant:
            s.sup = True


def _split_columns(lines: list) -> list[list]:
    """Split a block whose lines sit in separate horizontal bands (e.g. OCR output spanning two columns)."""
    if len(lines) < 2:
        return [lines] if lines else []
    groups: list[list] = []
    spans: list[list[float]] = []
    for l in sorted(lines, key=lambda l: l.bbox[0]):
        if spans and l.bbox[0] < spans[-1][1] + 10:
            spans[-1][1] = max(spans[-1][1], l.bbox[2])
            groups[-1].append(l)
        else:
            spans.append([l.bbox[0], l.bbox[2]])
            groups.append([l])
    if len(groups) == 1:
        return [lines]
    order = {id(l): i for i, l in enumerate(lines)}
    return [sorted(g, key=lambda l: order[id(l)]) for g in groups]


def _pixmap_png(pix) -> bytes:
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("png")


def _extract_image(doc, page, info) -> Optional[tuple[bytes, str, int, int]]:
    xref = info.get("xref", 0)
    if xref:
        try:
            raw = doc.extract_image(xref)
            if raw and raw.get("ext") in ("jpeg", "jpg") and not raw.get("smask") and raw.get("colorspace", 3) in (1, 3):
                return raw["image"], "jpg", raw["width"], raw["height"]
            pix = pymupdf.Pixmap(doc, xref)
            if raw and raw.get("smask"):
                try:
                    pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, raw["smask"]))
                except Exception:
                    pass
            return _pixmap_png(pix), "png", pix.width, pix.height
        except Exception:
            pass
    try:
        pix = page.get_pixmap(clip=pymupdf.Rect(info["bbox"]), dpi=150)
        return pix.tobytes("png"), "png", pix.width, pix.height
    except Exception:
        return None


def _detect_marker(text: str) -> tuple[Optional[str], str]:
    m = BULLET_RE.match(text)
    if m:
        return "•", text[m.end():]
    m = ORDERED_RE.match(text)
    if m:
        return m.group(0).strip(), text[m.end():]
    return None, text


def _strip_prefix_from_spans(spans: list[RawSpan], n_chars: int) -> list[RawSpan]:
    out = []
    for s in spans:
        if n_chars <= 0:
            out.append(s)
            continue
        if len(s.text) <= n_chars:
            n_chars -= len(s.text)
            continue
        out.append(RawSpan(s.text[n_chars:], s.size, s.bold, s.italic, s.sup, s.bbox, s.href))
        n_chars = 0
    return out


# --------------------------------------------------------------------------- tagged PDF alt text


def _figure_alts_from_tags(path: str) -> Optional[list[Optional[str]]]:
    """Return the /Alt of each Figure structure element in logical order, or None."""
    try:
        import logging

        logging.getLogger("pypdf").setLevel(logging.ERROR)
        from pypdf import PdfReader
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    except Exception:
        return None
    try:
        reader = PdfReader(path)
        root = reader.trailer["/Root"]
        if "/StructTreeRoot" not in root:
            return None
        tree = root["/StructTreeRoot"].get_object()
        role_map = {}
        if "/RoleMap" in tree:
            role_map = {str(k): str(v) for k, v in tree["/RoleMap"].get_object().items()}
        alts: list[Optional[str]] = []
        seen: set = set()

        def walk(node, depth=0):
            if depth > 300:
                return
            if isinstance(node, IndirectObject):
                key = (node.idnum, node.generation)
                if key in seen:
                    return
                seen.add(key)
                node = node.get_object()
            if isinstance(node, ArrayObject):
                for k in node:
                    walk(k, depth + 1)
            elif isinstance(node, DictionaryObject):
                s = node.get("/S")
                if s is not None:
                    s = str(s)
                    s = role_map.get(s, s)
                    if s == "/Figure":
                        alt = node.get("/Alt")
                        alts.append(str(alt).strip() if alt is not None else None)
                        return
                if "/K" in node:
                    walk(node["/K"], depth + 1)

        walk(tree.get("/K"))
        return alts
    except Exception:
        return None


# --------------------------------------------------------------------------- main entry


def extract_document(path: str, options: Optional[ExtractOptions] = None) -> Document:
    options = options or ExtractOptions()
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)  # repairable-file chatter, not useful to users
    except Exception:
        pass
    doc = pymupdf.open(path)
    if doc.needs_pass:
        raise ValueError("PDF is password protected; remove the password first.")
    issues: list[Issue] = []
    source_name = os.path.basename(path)

    # ---- document-level metadata
    meta = doc.metadata or {}
    lang = options.lang
    if not lang:
        try:
            kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
            if kind == "string" and value.strip():
                lang = value.strip()
        except Exception:
            pass
    if not lang:
        lang = "en"
        issues.append(Issue("review", "3.1.1 Language of Page",
                            "The PDF does not declare a language; assumed English (lang=\"en\"). "
                            "Use --lang if the document is in another language."))

    toc = {}
    try:
        for level, title, pno in doc.get_toc(simple=True):
            if title.strip():
                toc[norm_key(title)] = (level, pno)
    except Exception:
        pass

    if doc.is_form_pdf:
        n = sum(1 for p in doc for _ in p.widgets())
        issues.append(Issue("fail", "1.3.1 / 4.1.2",
                            f"The PDF contains {n} fillable form field(s). Form fields are not converted; "
                            "recreate the form as an accessible web form (or link to an online form) before publishing."))

    # ---- pass 1: collect page items
    pages_items: list[list[PageItem]] = []
    page_heights: list[float] = []
    seen_image_xrefs: set = set()
    fig_counter = 0
    tiny_images = 0
    multi_column_pages: list[int] = []
    decorated_pages: set = set()

    for pno, page in enumerate(doc):
        pnum = pno + 1
        page_rect = page.rect
        page_area = max(1.0, page_rect.width * page_rect.height)
        page_heights.append(page_rect.height)
        links = _link_targets(page)
        flags = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES) | pymupdf.TEXT_DEHYPHENATE

        textpage = None
        raw_chars = len(page.get_text("text").strip())
        image_infos = page.get_image_info(xrefs=True)
        big_image = any(
            _rect_overlap_ratio(page_rect, tuple(i["bbox"])) > 0.5 for i in image_infos
        )
        is_scan = raw_chars < 20 and big_image
        ocr_used = False
        if is_scan and options.ocr:
            try:
                textpage = page.get_textpage_ocr(dpi=300, full=True, language=options.ocr_lang)
                ocr_used = True
                issues.append(Issue("review", "1.1.1 Non-text Content",
                                    "This page was a scanned image; its text was produced by OCR. "
                                    "Proofread it against the original.", page=pnum))
            except Exception as exc:
                issues.append(Issue("fail", "1.1.1 Non-text Content",
                                    f"OCR failed ({exc}). Is Tesseract installed?", page=pnum))

        items: list[PageItem] = []

        # tables
        table_rects = []
        try:
            for tab in page.find_tables().tables:
                data = []
                for row in tab.rows:
                    data.append([
                        None if cell is None else page.get_text("text", clip=pymupdf.Rect(cell), textpage=textpage)
                        for cell in row.cells
                    ])
                if not data or len(data) < 2 or max(len(r) for r in data) < 2:
                    continue
                cells = [c for r in data for c in r if c not in (None, "") and c.strip()]
                if len(cells) < 3:
                    continue
                table_rects.append(tuple(tab.bbox))
                items.append(PageItem("table", tuple(tab.bbox), payload=(data, None, pnum)))
        except Exception:
            pass

        # vector graphics (charts, diagrams)
        graphic_rects = []
        if options.detect_graphics:
            try:
                # Ignore invisible white fills behind text.
                drawings = [d for d in page.get_drawings() if not _is_white_fill(d)]
                clusters = page.cluster_drawings(drawings=drawings) if drawings else []
                for r in clusters:
                    r = tuple(r)
                    w, h = r[2] - r[0], r[3] - r[1]
                    if w < 72 or h < 50 or (w * h) / page_area > 0.85:
                        continue
                    if any(_rect_overlap_ratio(r, t) > 0.3 or _rect_overlap_ratio(t, r) > 0.3 for t in table_rects):
                        continue
                    n_paths = sum(1 for d in drawings if _center_in(tuple(d["rect"]), r))
                    # Underlines and rules alone (no shapes) are not a chart.
                    n_shapes = sum(1 for d in drawings if _center_in(tuple(d["rect"]), r) and min(d["rect"].width, d["rect"].height) > 2.5)
                    if n_paths < 8 or n_shapes < 2:
                        continue
                    inner_text = page.get_text("text", clip=pymupdf.Rect(r)).strip()
                    if len(inner_text) > 300:
                        decorated_pages.add(pnum)
                        continue
                    r = _absorb_labels(page, r, page_rect)
                    inner_text = page.get_text("text", clip=pymupdf.Rect(r)).strip()
                    pix = page.get_pixmap(clip=pymupdf.Rect(r), dpi=150)
                    fig_counter += 1
                    fig = Figure(
                        id=f"fig-{fig_counter}", page=pnum, data=pix.tobytes("png"), ext="png",
                        width=round((r[2] - r[0]) * 4 / 3), height=round((r[3] - r[1]) * 4 / 3), kind="graphic",
                        context_text=re.sub(r"\s+", " ", inner_text),
                    )
                    graphic_rects.append(r)
                    items.append(PageItem("figure", r, payload=fig))
            except Exception:
                pass

        # raster images
        for info in image_infos:
            bbox = tuple(info["bbox"])
            bbox = (max(bbox[0], page_rect.x0), max(bbox[1], page_rect.y0),
                    min(bbox[2], page_rect.x1), min(bbox[3], page_rect.y1))
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if w <= 0 or h <= 0:
                continue
            if w < 16 or h < 16:
                tiny_images += 1
                continue
            if any(_rect_overlap_ratio(bbox, g) > 0.8 for g in graphic_rects):
                continue
            covers = (w * h) / page_area
            if is_scan and ocr_used and covers > 0.5:
                continue
            if covers > 0.85 and raw_chars >= 20:
                issues.append(Issue("info", "1.1.1 Non-text Content",
                                    "A full-page background image was omitted as decorative.", page=pnum))
                continue
            xref = info.get("xref", 0)
            if xref and xref in seen_image_xrefs:
                continue  # repeated logo / watermark: keep only the first instance
            if xref:
                seen_image_xrefs.add(xref)
            got = _extract_image(doc, page, info)
            if not got:
                continue
            data, ext, iw, ih = got
            fig_counter += 1
            # width/height attributes use the size the image had on the page (in CSS px)
            fig = Figure(id=f"fig-{fig_counter}", page=pnum, data=data, ext=ext,
                         width=round(w * 4 / 3), height=round(h * 4 / 3),
                         kind="scan" if (is_scan and covers > 0.5) else "image")
            items.append(PageItem("figure", bbox, payload=fig))

        if is_scan and not ocr_used and not options.ocr:
            issues.append(Issue("fail", "1.1.1 Non-text Content",
                                "This page is a scanned image with no real text. Its content is unavailable to "
                                "screen readers. Re-run with --ocr (requires Tesseract) or retype the content.",
                                page=pnum))

        # text
        tdict = page.get_text("dict", flags=flags, textpage=textpage) if textpage else page.get_text("dict", flags=flags)
        excluded = table_rects + graphic_rects
        for bno, block in enumerate(tdict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            lines = []
            for ln in block.get("lines", []):
                lb = tuple(ln["bbox"])
                if any(_center_in(lb, r) for r in excluded):
                    continue
                if ln.get("dir", (1, 0))[0] < 0.9:
                    continue  # rotated / vertical text (usually margin decoration)
                spans = [rs for s in ln.get("spans", []) if (rs := _span_from_dict(s, links))]
                if not spans or not "".join(s.text for s in spans).strip():
                    continue
                _mark_superscripts(spans, lb)
                lines.append(Line(pnum, (pnum, bno), lb, spans))
            for gi, group in enumerate(_split_columns(lines)):
                for l in group:
                    l.block = (pnum, bno, gi)
                bb = (min(l.bbox[0] for l in group), min(l.bbox[1] for l in group),
                      max(l.bbox[2] for l in group), max(l.bbox[3] for l in group))
                items.append(PageItem("text", bb, lines=group))

        _attach_detached_superscripts(items)
        pages_items.append(items)

    if decorated_pages:
        pages = sorted(decorated_pages)
        issues.append(Issue("review", "1.1.1 Non-text Content",
                            "Lines, boxes or other graphics appear around text; the text was kept and the graphics "
                            "dropped. Check nothing meaningful was shown only by them (e.g. a highlighted box).",
                            page=pages[0], target="pages " + ", ".join(map(str, pages))))

    # ---- pass 2: repeated headers / footers and page numbers
    if options.strip_headers_footers:
        removed = _strip_headers_footers(pages_items, page_heights)
        if removed:
            sample = ", ".join(f'"{t}"' for t in list(removed)[:3])
            issues.append(Issue("info", "-", f"Removed repeated page headers/footers and page numbers (e.g. {sample})."))

    # ---- pass 3: reading order and flow
    flow: list = []
    for pno, (page, items) in enumerate(zip(doc, pages_items)):
        ordered, two_col = _order_items(items, page.rect.width)
        if two_col:
            multi_column_pages.append(pno + 1)
        flow.append(PageAnchor(pno + 1))
        for it in ordered:
            if it.kind == "text":
                flow.extend(it.lines)
            else:
                flow.append(it)
    if multi_column_pages:
        issues.append(Issue("review", "1.3.2 Meaningful Sequence",
                            "Multi-column layout detected; reading order was reconstructed column by column. "
                            "Check that the text reads in the right order.",
                            page=multi_column_pages[0],
                            target="pages " + ", ".join(map(str, multi_column_pages))))

    all_lines = [u for u in flow if isinstance(u, Line)]
    size_counter: Counter = Counter()
    for l in all_lines:
        size_counter[l.size] += len(l.text.strip())
    body_size = size_counter.most_common(1)[0][0] if size_counter else 11.0

    # ---- pass 4: group lines into raw paragraphs
    units = _group_paragraphs(flow)
    units = _merge_across_pages(units)

    # ---- pass 5: classify into blocks
    blocks = _classify(units, body_size, toc, doc, options, issues)

    # ---- title
    title = options.title
    h1 = next((b for b in blocks if isinstance(b, Heading) and b.level == 1), None)
    meta_title = (meta.get("title") or "").strip()
    if not title:
        if h1 and 3 <= len(h1.text) <= 150:
            title = h1.text
        elif meta_title and not JUNK_TITLE_RE.match(meta_title):
            title = meta_title
        else:
            title = re.sub(r"[_\-]+", " ", os.path.splitext(source_name)[0]).strip().title()
            issues.append(Issue("review", "2.4.2 Page Titled",
                                f"No usable title found; the file name was used (\"{title}\"). Set one with --title."))
    if not h1:
        blocks.insert(0, Heading(1, [Span(title)], 1))
        issues.append(Issue("review", "2.4.6 Headings and Labels",
                            "No visual title was detected, so the page title was added as the main heading. "
                            "Check it is correct."))

    _normalize_heading_levels(blocks, issues)
    _assign_ids(blocks)

    # ---- alt text: tagged PDF, then user-provided
    figures = [b for b in blocks if isinstance(b, Figure)]
    raster = [f for f in figures if f.kind == "image"]
    tagged = doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[0] != "null"
    tag_alts = _figure_alts_from_tags(path) if tagged and raster else None
    if tag_alts and len(tag_alts) == len(raster):
        for fig, alt in zip(raster, tag_alts):
            if alt:
                fig.alt, fig.alt_source = alt, "pdf-tags"
    for fig in figures:
        if fig.id in options.alt_text and options.alt_text[fig.id] is not None:
            alt = str(options.alt_text[fig.id]).strip()
            fig.alt = alt
            fig.alt_source = "decorative" if alt == "" else "user"

    for fig in figures:
        if fig.alt_source == "missing":
            what = {"graphic": "chart/diagram", "scan": "scanned page", "image": "image"}[fig.kind]
            issues.append(Issue("fail", "1.1.1 Non-text Content",
                                f"{fig.id} ({what}) has no alternative text. Add a description in an alt-text "
                                "JSON file (see the report) or mark it decorative with \"\".",
                                page=fig.page, target=fig.id))
        elif fig.alt_source == "pdf-tags":
            issues.append(Issue("review", "1.1.1 Non-text Content",
                                f"{fig.id} uses alt text from the PDF tags: \"{fig.alt}\". Confirm it is accurate.",
                                page=fig.page, target=fig.id))

    if tiny_images:
        issues.append(Issue("info", "1.1.1 Non-text Content",
                            f"Skipped {tiny_images} very small image(s) as decorative (bullets, rules, icons)."))

    if not any(isinstance(b, Heading) and b.level > 1 for b in blocks) and doc.page_count > 1:
        issues.append(Issue("review", "1.3.1 Info and Relationships",
                            "No section headings were detected. If the PDF has sections, mark them as headings."))

    for b in blocks:
        if isinstance(b, Table):
            issues.append(Issue("review", "1.3.1 Info and Relationships",
                                f"{b.id}: table structure was detected automatically. Check the header row, "
                                "merged cells, and whether the first column should also be headers.",
                                page=b.page, target=b.id))

    return Document(
        title=title,
        lang=lang,
        blocks=blocks,
        page_count=doc.page_count,
        source_name=source_name,
        source_size=os.path.getsize(path),
        metadata={k: v for k, v in meta.items() if v},
        issues=issues,
    )


# --------------------------------------------------------------------------- passes


def _strip_headers_footers(pages_items: list[list[PageItem]], heights: list[float]) -> set:
    n_pages = len(pages_items)
    margin_keys: Counter = Counter()

    def in_margin(line: Line, h: float) -> bool:
        return line.bbox[3] < h * 0.1 or line.bbox[1] > h * 0.9

    def key(line: Line) -> str:
        return re.sub(r"\d+", "#", line.text.strip().lower())

    for items, h in zip(pages_items, heights):
        keys = {key(l) for it in items if it.kind == "text" for l in it.lines if in_margin(l, h)}
        margin_keys.update(keys)

    threshold = max(2, int(n_pages * 0.5 + 0.5))
    repeated = {k for k, c in margin_keys.items() if c >= threshold} if n_pages >= 2 else set()
    removed: set = set()
    for items, h in zip(pages_items, heights):
        for it in list(items):
            if it.kind != "text":
                continue
            keep = []
            for l in it.lines:
                if in_margin(l, h) and (key(l) in repeated or PAGE_NUMBER_RE.match(l.text)):
                    removed.add(l.text.strip())
                else:
                    keep.append(l)
            it.lines = keep
            if not keep:
                items.remove(it)
    return removed


def _group_paragraphs(flow: list) -> list:
    """Merge consecutive lines into RawPara objects; tables/figures/anchors pass through."""
    out: list = []
    cur: Optional[RawPara] = None
    pending_marker: Optional[tuple[str, float]] = None

    def flush():
        nonlocal cur
        if cur and cur.text:
            out.append(cur)
        cur = None

    for idx, u in enumerate(flow):
        if not isinstance(u, Line):
            flush()
            if not isinstance(u, PageAnchor):
                pending_marker = None
            out.append(u)
            continue

        text = u.text
        if BULLET_ONLY_RE.match(text) or ORDERED_ONLY_RE.match(text) or (
            BARE_MARKER_RE.match(text) and _same_baseline_follows(flow, idx)
        ):
            flush()
            m = text.strip()
            pending_marker = ("•" if BULLET_ONLY_RE.match(text) else m, u.bbox[0])
            continue

        if pending_marker:
            marker, mx = pending_marker
            pending_marker = None
            flush()
            cur = RawPara([u], u.page, marker=marker, marker_x=mx)
            continue

        marker, rest = _detect_marker(text)
        if marker and _inline_enumeration(marker, rest):
            marker = None  # "(1) ... (2) ..." running inside a sentence, not a list
        if marker:
            flush()
            cur = RawPara([u], u.page, marker=marker, marker_x=u.bbox[0])
            continue

        if cur is None:
            cur = RawPara([u], u.page)
            continue

        prev = cur.lines[-1]
        gap = u.bbox[1] - prev.bbox[3]
        same_block = u.block == prev.block
        size_change = abs(u.size - prev.size) > 1.0
        bold_change = (cur.bold and not u.bold) or (not cur.bold and u.bold and len(cur.lines) >= 1)
        big_gap = gap > 0.6 * prev.height or gap < -prev.height  # new paragraph / jumped column
        continues = (
            same_block and not size_change and not bold_change and not big_gap
        ) or (
            not same_block and not size_change and not bold_change and -0.5 * prev.height <= gap < 0.5 * prev.height
            and not prev.text.rstrip().endswith(TERMINAL_PUNCT)
            and (cur.marker is None or u.bbox[0] >= cur.lines[0].bbox[0] - 3)
        )
        if continues:
            cur.lines.append(u)
        else:
            flush()
            cur = RawPara([u], u.page)
    flush()
    return out


def _inline_enumeration(marker: str, rest: str) -> bool:
    ordered, value, ltype = _marker_info(marker)
    if not ordered or value is None:
        return False
    nxt = str(value + 1) if ltype is None else chr(ord("a" if ltype in ("a", "i") else "A") + value)
    if ltype in ("i", "I"):
        return False
    return re.search(r"\(\s*" + re.escape(nxt) + r"\s*\)|\s" + re.escape(nxt) + r"\)\s", rest) is not None


def _same_baseline_follows(flow: list, idx: int) -> bool:
    """True if the next line sits on the same baseline to the right (a detached list number)."""
    if idx + 1 >= len(flow) or not isinstance(flow[idx + 1], Line):
        return False
    a, b = flow[idx].bbox, flow[idx + 1].bbox
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > 0.5 * min(a[3] - a[1], b[3] - b[1]) and b[0] > a[2]


def _merge_across_pages(units: list) -> list:
    """Join a paragraph split by a page break (no terminal punctuation, next starts lowercase).

    Footnotes printed at the bottom of the page sit between the two halves, so they are skipped.
    """
    out: list = []
    i = 0
    while i < len(units):
        u = units[i]
        if isinstance(u, PageAnchor) and i + 1 < len(units) and isinstance(units[i + 1], RawPara):
            nxt = units[i + 1]
            j = len(out) - 1
            while j >= 0 and isinstance(out[j], RawPara) and out[j].size < nxt.size - 0.5:
                j -= 1  # footnotes in smaller type
            prev = out[j] if j >= 0 and isinstance(out[j], RawPara) else None
            if (
                prev is not None and not prev.text.endswith(TERMINAL_PUNCT) and nxt.marker is None
                and abs(prev.size - nxt.size) <= 1 and not prev.bold and not nxt.bold
                and (nxt.text[:1].islower() or re.search(r"[\w,]$", prev.text))
                and len(prev.text) > 80
            ):
                out.append(u)
                prev.lines.extend(nxt.lines)
                i += 2
                continue
        out.append(u)
        i += 1
    return out


def _keeps_line_breaks(para: RawPara) -> bool:
    """Addresses and contact blocks: several short lines that each end without punctuation."""
    if len(para.lines) < 3 or para.marker:
        return False
    widths = [l.bbox[2] - l.bbox[0] for l in para.lines]
    if max(widths) > 300:
        return False
    return sum(1 for l in para.lines[:-1] if not l.text.rstrip().endswith((".", ",", ";", "-"))) >= len(para.lines) - 1


def _spans_for(para: RawPara, strip_marker: bool) -> list[Span]:
    spans: list[Span] = []
    breaks = _keeps_line_breaks(para)
    for idx, line in enumerate(para.lines):
        raw = line.spans
        if idx == 0 and strip_marker and para.marker:
            m = BULLET_RE.match(line.text) or ORDERED_RE.match(line.text)
            if m:
                raw = _strip_prefix_from_spans(raw, m.end())
        if spans and raw:
            last = spans[-1].text
            if last.endswith("-") and not last.endswith(" -"):
                pass  # hyphenated word split across lines; join without space
            elif breaks:
                spans.append(Span("\n"))
            elif not last.endswith(" ") and not raw[0].text.startswith(" "):
                spans[-1] = Span(last + " ", spans[-1].bold, spans[-1].italic, spans[-1].superscript, spans[-1].href)
        for s in raw:
            text = re.sub(r"[ \t]+", " ", s.text)
            if not text:
                continue
            if spans and (spans[-1].bold, spans[-1].italic, spans[-1].superscript, spans[-1].href) == (s.bold, s.italic, s.sup, s.href):
                spans[-1].text += text
            else:
                spans.append(Span(text, s.bold, s.italic, s.sup, s.href))
    if spans:
        spans[0].text = spans[0].text.lstrip()
        spans[-1].text = spans[-1].text.rstrip()
    # Bold/italic carried on whitespace-only spans is noise.
    out = [s if s.text.strip() else Span(s.text, href=s.href) for s in spans if s.text]
    if breaks:
        for s in out:
            if s.text != "\n":
                s.text = s.text.strip(" ")
    return out


def _marker_info(marker: str) -> tuple[bool, Optional[int], Optional[str]]:
    """Return (ordered, value, list_type) for a list marker."""
    if marker == "•":
        return False, None, None
    core = marker.strip("().")
    core = core.rstrip(")")
    if core.isdigit():
        return True, int(core), None
    if len(core) == 1 and core.isalpha() and core.lower() not in "ivx":
        return True, ord(core.lower()) - 96, "A" if core.isupper() else "a"
    r = roman_to_int(core)
    if r:
        return True, r, "I" if core.isupper() else "i"
    if len(core) == 1 and core.isalpha():
        return True, ord(core.lower()) - 96, "A" if core.isupper() else "a"
    return False, None, None


def _classify(units: list, body_size: float, toc: dict, doc, options: ExtractOptions, issues: list) -> list:
    # Heading candidates
    def is_heading_candidate(p: RawPara) -> bool:
        text = p.text
        if not re.search(r"[A-Za-zÀ-￿]", text) or len(text) > 200 or len(p.lines) > 4:
            return False
        if _is_footnote(p, body_size):
            return False
        if PAGE_NUMBER_RE.match(text):
            return False
        if p.size >= body_size * HEADING_RATIO:
            return True
        if p.marker == "•":
            return False
        return (
            p.bold and p.size >= body_size - 0.5 and len(text) <= 120
            and (len(p.lines) <= 2 or p.size > body_size + 0.5)
            and not text.endswith((".", ",", ";"))
        )

    paras = [u for u in units if isinstance(u, RawPara)]
    toc_hits = {}
    for p in paras:
        k = norm_key(p.text)
        if k in toc and abs(toc[k][1] - p.page) <= 1:
            toc_hits[id(p)] = toc[k][0]

    cands = [p for p in paras if is_heading_candidate(p) or id(p) in toc_hits]
    cand_ids = {id(p) for p in cands}
    # Title: biggest heading candidate on the first page.
    title_para = None
    first_page = [p for p in cands if p.page == min((c.page for c in cands), default=1) and p.page <= 2]
    if first_page:
        biggest = max(p.size for p in first_page)
        if biggest >= body_size * HEADING_RATIO:
            title_para = next(p for p in first_page if p.size == biggest)

    # Rank remaining heading styles
    style_sizes = sorted({p.size for p in cands if p is not title_para}, reverse=True)
    clusters: list[float] = []
    for s in style_sizes:
        if not clusters or clusters[-1] - s > 0.6:
            clusters.append(s)

    def style_rank(p: RawPara) -> tuple:
        ci = next(i for i, c in enumerate(clusters) if c - p.size <= 0.6 + 1e-6)
        return (ci, 0 if p.bold else 1, _numbering_style(p.marker))

    # Larger text ranks higher. Within one size, heading styles rank in the order they first appear,
    # so an outline numbered "I." then "A." then "1." gets three levels even in one font.
    first_seen: dict = {}
    for p in cands:
        if p is not title_para:
            first_seen.setdefault(style_rank(p), len(first_seen))
    ranks = sorted(first_seen, key=lambda r: (r[0], first_seen[r]))
    rank_level = {r: min(6, i + 2) for i, r in enumerate(ranks)}
    footnote_cont: Optional[Footnote] = None
    last_footnote: Optional[Footnote] = None
    small_seen_on_page = False

    blocks: list = []
    list_stack: list[tuple[float, ListBlock]] = []  # (marker x, list)

    def close_lists():
        list_stack.clear()

    i = 0
    while i < len(units):
        u = units[i]
        if isinstance(u, PageAnchor):
            blocks.append(u)
            footnote_cont = last_footnote if last_footnote and last_footnote.page == u.page - 1 else None
            small_seen_on_page = False
            i += 1
            continue
        if isinstance(u, PageItem):
            close_lists()
            if u.kind == "table":
                blocks.append(_build_table(u.payload, 0))
            else:
                blocks.append(u.payload)
            i += 1
            continue

        p: RawPara = u
        if _is_footnote(p, body_size):
            spans = _spans_for(p, strip_marker=False)
            number = spans[0].text.strip()
            rest = spans[1:]
            if rest:
                rest[0].text = rest[0].text.lstrip()
            last_footnote = Footnote(number, rest, p.page)
            blocks.append(last_footnote)
            footnote_cont = None
            small_seen_on_page = True
            i += 1
            continue
        if (
            p.size < body_size - 0.5 and not small_seen_on_page and footnote_cont is not None
            and p.lines[0].bbox[1] > doc[p.page - 1].rect.height * 0.5 and p.marker is None
        ):
            # footnote text carried over from the previous page
            footnote_cont.spans.append(Span(" "))
            footnote_cont.spans.extend(_spans_for(p, strip_marker=False))
            footnote_cont = None
            small_seen_on_page = True
            i += 1
            continue
        if p is title_para:
            close_lists()
            blocks.append(Heading(1, _spans_for(p, strip_marker=False), p.page))
            i += 1
            continue
        if id(p) in toc_hits:
            close_lists()
            blocks.append(Heading(min(6, toc_hits[id(p)] + 1), _spans_for(p, strip_marker=False), p.page))
            i += 1
            continue
        if id(p) in cand_ids and p.marker != "•":
            close_lists()
            spans = _spans_for(p, strip_marker=bool(p.marker))
            if p.marker:
                spans.insert(0, Span(p.marker + " "))
            blocks.append(Heading(rank_level[style_rank(p)], spans, p.page))
            i += 1
            continue

        if p.marker:
            ordered, value, ltype = _marker_info(p.marker)
            item = ListItem(_spans_for(p, strip_marker=True), value=value if ordered else None)
            x = p.marker_x
            while list_stack and x < list_stack[-1][0] - 8:
                list_stack.pop()
            if list_stack and x > list_stack[-1][0] + 8:
                nested = ListBlock(ordered, [item], p.page, ltype)
                list_stack[-1][1].items[-1].children = nested
                list_stack.append((x, nested))
            elif list_stack and (
                len(list_stack) > 1
                or (list_stack[-1][1].ordered == ordered and list_stack[-1][1].list_type == ltype)
            ):
                list_stack[-1][1].items.append(item)
            else:
                newl = ListBlock(ordered, [item], p.page, ltype)
                list_stack.clear()
                blocks.append(newl)
                list_stack.append((x, newl))
            i += 1
            continue

        # continuation paragraph inside a list item (indented past the bullet)
        if list_stack and p.x0 > list_stack[-1][0] + 5:
            item = list_stack[-1][1].items[-1]
            item.spans.append(Span(" "))
            item.spans.extend(_spans_for(p, strip_marker=False))
            i += 1
            continue

        close_lists()
        blocks.append(Paragraph(_spans_for(p, strip_marker=False), p.page))
        i += 1

    blocks = _unwrap_single_letter_lists(blocks)
    blocks = _merge_split_tables(blocks)
    _attach_captions(blocks)
    return blocks


def _numbering_style(marker: Optional[str]) -> Optional[str]:
    if not marker or marker == "•":
        return None
    ordered, _, ltype = _marker_info(marker)
    return (ltype or "1") if ordered else None


def _is_footnote(p: RawPara, body_size: float) -> bool:
    """A paragraph in smaller type that starts with a raised number, e.g. "¹ See SPG 201.35"."""
    first = next((s for s in p.lines[0].spans if s.text.strip()), None)
    return (
        first is not None and first.sup and first.text.strip().isdigit()
        and p.size < body_size - 0.5 and len(p.text) > len(first.text.strip()) + 2
    )


def _unwrap_single_letter_lists(blocks: list) -> list:
    """A lone "A. Something" paragraph is more likely prose than a one-item list."""
    out = []
    for b in blocks:
        if isinstance(b, ListBlock) and b.ordered and b.list_type in ("a", "A") and len(b.items) == 1 and not b.items[0].children:
            item = b.items[0]
            letter = chr(96 + (item.value or 1))
            if b.list_type == "A":
                letter = letter.upper()
            out.append(Paragraph([Span(f"{letter}. ")] + item.spans, b.page))
        else:
            out.append(b)
    return out


def _build_table(payload, _unused: int) -> Table:
    data, header_names, page = payload
    rows: list[list[TableCell]] = []
    if header_names:
        rows.append([TableCell(_cell_text(n), header=True, scope="col") for n in header_names])
    for r_idx, row in enumerate(data):
        cells: list[TableCell] = []
        for c in row:
            if c is None and cells:
                cells[-1].colspan += 1
                continue
            cells.append(TableCell(_cell_text(c)))
        if cells:
            rows.append(cells)
    if not header_names and rows:
        for c in rows[0]:
            c.header, c.scope = True, "col"
    return Table(rows=rows, page=page)


def _cell_text(c) -> str:
    if c is None:
        return ""
    return re.sub(r"\s+", " ", clean_text(str(c))).strip()


def _merge_split_tables(blocks: list) -> list:
    out: list = []
    for b in blocks:
        if isinstance(b, Table):
            prev_idx = len(out) - 1
            while prev_idx >= 0 and isinstance(out[prev_idx], PageAnchor):
                prev_idx -= 1
            if prev_idx >= 0 and isinstance(out[prev_idx], Table) and prev_idx != len(out) - 1:
                prev = out[prev_idx]
                width = lambda r: sum(c.colspan for c in r)
                if width(prev.rows[0]) == width(b.rows[0]):
                    rows = b.rows
                    if [c.text for c in rows[0]] == [c.text for c in prev.rows[0]]:
                        rows = rows[1:]
                    else:
                        for c in rows[0]:
                            c.header, c.scope = False, None
                    prev.rows.extend(rows)
                    continue
        out.append(b)
    return out


def _attach_captions(blocks: list) -> None:
    def neighbour(idx: int, step: int) -> Optional[int]:
        j = idx + step
        while 0 <= j < len(blocks) and isinstance(blocks[j], PageAnchor):
            j += step
        return j if 0 <= j < len(blocks) else None

    for idx, b in enumerate(blocks):
        if isinstance(b, Table):
            j = neighbour(idx, -1)
            if j is not None and isinstance(blocks[j], Paragraph) and CAPTION_TABLE_RE.match(blocks[j].text):
                b.caption = blocks[j].text
                blocks[j] = None
        elif isinstance(b, Figure):
            for step in (1, -1):
                j = neighbour(idx, step)
                if j is not None and isinstance(blocks[j], Paragraph) and CAPTION_FIGURE_RE.match(blocks[j].text):
                    b.caption = blocks[j].text
                    blocks[j] = None
                    break
    blocks[:] = [b for b in blocks if b is not None]


def _normalize_heading_levels(blocks: list, issues: list) -> None:
    prev = 1
    changed = False
    seen_h1 = False
    for b in blocks:
        if not isinstance(b, Heading):
            continue
        if b.level == 1:
            if seen_h1:
                b.level = 2
                changed = True
            seen_h1 = True
            prev = b.level
            continue
        if b.level > prev + 1:
            b.level = prev + 1
            changed = True
        prev = b.level
    if changed:
        issues.append(Issue("review", "1.3.1 Info and Relationships",
                            "Heading levels were adjusted so none are skipped. Check the outline makes sense."))


def _assign_ids(blocks: list) -> None:
    used: set = set()
    t = 0
    for b in blocks:
        if isinstance(b, Heading):
            base = re.sub(r"[^a-z0-9]+", "-", b.text.lower()).strip("-")[:60] or "section"
            slug, n = base, 2
            while slug in used or slug.startswith("page-") or slug.startswith("fig-"):
                slug = f"{base}-{n}"
                n += 1
            used.add(slug)
            b.id = slug
        elif isinstance(b, Table):
            t += 1
            b.id = f"table-{t}"
