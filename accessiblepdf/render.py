"""Render a Document as WCAG 2.1 AA conformant HTML."""

from __future__ import annotations

import base64
import datetime as _dt
import html
import re
from dataclasses import dataclass
from typing import Optional

from .model import Document, Figure, Footnote, Heading, ListBlock, PageAnchor, Paragraph, Span, Table

URL_RE = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s<>\"']+[^\s<>\"'.,;:!?)\]])|(?P<email>[\w.+-]+@[\w-]+(?:\.[\w-]+)+)"
)

# Colours chosen for at least 4.5:1 contrast for text and 3:1 for UI boundaries.
THEMES = {
    "default": {
        "text": "#1b1b1b", "bg": "#ffffff", "muted": "#4a4a4a", "link": "#0b5394",
        "link_visited": "#5b2c86", "heading": "#1b1b1b", "accent": "#0b5394",
        "border": "#767676", "table_head": "#eef2f7", "focus": "#0b5394", "banner": "#f3f4f6",
    },
    # University of Michigan blue (#00274C) and maize (#FFCB05). Maize is only used
    # as a background behind blue text, never as text on white.
    "umich": {
        "text": "#1b1b1b", "bg": "#ffffff", "muted": "#4a4a4a", "link": "#00274c",
        "link_visited": "#5b2c86", "heading": "#00274c", "accent": "#ffcb05",
        "border": "#767676", "table_head": "#e9eef4", "focus": "#00274c", "banner": "#f4f4f4",
    },
}

CSS = """
.apdf, .apdf *, .apdf *::before, .apdf *::after {{ box-sizing: border-box; }}
{root} {{
  color: {text}; background: {bg};
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 1rem; line-height: 1.6;
}}
.apdf {{ max-width: 48rem; margin: 0 auto; padding: 1rem; overflow-wrap: break-word; }}
.apdf h1, .apdf h2, .apdf h3, .apdf h4, .apdf h5, .apdf h6 {{ color: {heading}; line-height: 1.25; margin: 1.6em 0 0.5em; }}
.apdf h1 {{ font-size: 2rem; margin-top: 0.5em; }}
.apdf h2 {{ font-size: 1.6rem; }}
.apdf h3 {{ font-size: 1.3rem; }}
.apdf h4 {{ font-size: 1.15rem; }}
.apdf h5, .apdf h6 {{ font-size: 1rem; }}
.apdf p, .apdf li {{ max-width: 70ch; }}
.apdf p {{ margin: 0 0 1em; }}
.apdf a {{ color: {link}; text-decoration: underline; text-underline-offset: 0.15em; }}
.apdf a:visited {{ color: {link_visited}; }}
.apdf a:hover {{ text-decoration-thickness: 0.15em; }}
.apdf :focus-visible {{ outline: 3px solid {focus}; outline-offset: 2px; }}
.apdf img {{ max-width: 100%; height: auto; }}
.apdf figure {{ margin: 1.5em 0; }}
.apdf figcaption {{ color: {muted}; font-size: 0.95rem; margin-top: 0.4em; }}
.apdf .table-wrap {{ overflow-x: auto; margin: 1.5em 0; }}
.apdf table {{ border-collapse: collapse; min-width: 50%; }}
.apdf caption {{ text-align: left; font-weight: 600; padding: 0 0 0.5em; }}
.apdf th, .apdf td {{ border: 1px solid {border}; padding: 0.45em 0.6em; text-align: left; vertical-align: top; }}
.apdf th {{ background: {table_head}; font-weight: 600; }}
.apdf .doc-info {{ background: {banner}; border-left: 4px solid {accent}; padding: 0.75em 1em; margin: 1em 0 1.5em; }}
.apdf .doc-info p {{ margin: 0.25em 0; }}
.apdf nav.toc ol {{ padding-left: 1.25em; }}
.apdf nav.toc ol.plain {{ list-style: none; }}
.apdf nav.toc > ol.plain {{ padding-left: 0; }}
.apdf .page-marker {{ color: {muted}; font-size: 0.875rem; border-top: 1px solid {border}; padding-top: 0.25em; margin: 2em 0 1em; }}
.apdf .footnotes {{ border-top: 1px solid {border}; margin-top: 2.5em; font-size: 0.95rem; }}
.apdf sup {{ line-height: 0; }}
.apdf .needs-alt {{ outline: 3px dashed #b00020; }}
.apdf .visually-hidden {{ position: absolute !important; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }}
.skip-link {{ position: absolute; left: 0.5rem; top: -3rem; background: {bg}; color: {link}; padding: 0.5rem 1rem;
  border: 2px solid {focus}; z-index: 100; }}
.skip-link:focus {{ top: 0.5rem; }}
.apdf footer {{ margin-top: 3em; padding-top: 1em; border-top: 1px solid {border}; color: {muted}; font-size: 0.9rem; }}
@media print {{ .skip-link, nav.toc {{ display: none; }} .apdf a::after {{ content: " (" attr(href) ")"; font-size: 0.85em; }} }}
"""


@dataclass
class RenderOptions:
    theme: str = "default"
    fragment: bool = False  # only the content, for pasting into a CMS page
    heading_offset: int = 0  # shift heading levels (fragment default: 1, so the title is h2)
    embed_images: bool = False
    image_dir: str = ""  # relative folder name for image files
    pdf_href: Optional[str] = None  # link to the original PDF
    show_page_numbers: bool = False
    toc: bool = True
    contact: Optional[str] = None  # accessibility contact (email or URL)
    site_name: Optional[str] = None


# Footnote numbers present in the document being rendered, and references already given an id.
_footnotes: dict = {"numbers": set(), "referenced": set()}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _autolink(text: str) -> str:
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        out.append(_esc(text[pos:m.start()]))
        if m.group("url"):
            url = m.group("url")
            href = url if url.lower().startswith("http") else "https://" + url
            out.append(f'<a href="{_esc(href)}">{_esc(url)}</a>')
        else:
            email = m.group("email")
            out.append(f'<a href="mailto:{_esc(email)}">{_esc(email)}</a>')
        pos = m.end()
    out.append(_esc(text[pos:]))
    return "".join(out)


def _inline(spans: list[Span]) -> str:
    """Render spans, grouping neighbouring spans that share a link into one <a>."""
    parts: list[str] = []
    i = 0
    while i < len(spans):
        href = spans[i].href
        if href:
            group = []
            while i < len(spans) and spans[i].href == href:
                group.append(spans[i])
                i += 1
            inner = "".join(_fmt(s, link=False) for s in group)
            parts.append(f'<a href="{_esc(href)}">{inner}</a>')
        else:
            parts.append(_fmt(spans[i], link=True))
            i += 1
    return "".join(parts).strip()


def _fmt(s: Span, link: bool) -> str:
    body = _autolink(s.text) if link else _esc(s.text)
    body = body.replace("\n", "<br>\n")
    if not s.text.strip():
        return body
    lead = re.match(r"^\s*", body).group(0)
    trail = re.search(r"\s*$", body).group(0)
    core = body.strip()
    if s.superscript and link and s.text.strip() in _footnotes["numbers"]:
        n = s.text.strip()
        ref_id = "" if n in _footnotes["referenced"] else f' id="fnref-{n}"'
        _footnotes["referenced"].add(n)
        core = f'<sup><a href="#fn-{n}"{ref_id}><span class="visually-hidden">Footnote </span>{n}</a></sup>'
    elif s.superscript:
        core = f"<sup>{core}</sup>"
    if s.italic:
        core = f"<em>{core}</em>"
    if s.bold:
        core = f"<strong>{core}</strong>"
    return lead + core + trail


def _human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def _plain_spans(spans: list[Span]) -> list[Span]:
    """Headings: drop bold (it is implied) but keep links and italics."""
    return [Span(s.text, False, s.italic, s.superscript, s.href) for s in spans]


def render_list(lst: ListBlock, indent: str = "") -> str:
    tag = "ol" if lst.ordered else "ul"
    attrs = ""
    if lst.ordered:
        if lst.list_type:
            attrs += f' type="{lst.list_type}"'
        first = lst.items[0].value if lst.items else None
        if first and first != 1:
            attrs += f' start="{first}"'
    lines = [f"{indent}<{tag}{attrs}>"]
    expected = lst.items[0].value if lst.items and lst.items[0].value else 1
    for item in lst.items:
        value_attr = ""
        if lst.ordered and item.value is not None and item.value != expected:
            value_attr = f' value="{item.value}"'
            expected = item.value
        expected += 1
        body = _inline(item.spans)
        if item.children:
            body += "\n" + render_list(item.children, indent + "    ") + "\n" + indent + "  "
        lines.append(f"{indent}  <li{value_attr}>{body}</li>")
    lines.append(f"{indent}</{tag}>")
    return "\n".join(lines)


def render_table(t: Table) -> str:
    label = _esc(t.caption) if t.caption else f"Table {t.id.split('-')[-1]}"
    out = [f'<div class="table-wrap" role="region" aria-label="{label}" tabindex="0">', f'<table id="{t.id}">']
    if t.caption:
        out.append(f"  <caption>{_esc(t.caption)}</caption>")
    header_rows = []
    body_rows = []
    for row in t.rows:
        (header_rows if all(c.header for c in row) and not body_rows else body_rows).append(row)
    for section, rows in (("thead", header_rows), ("tbody", body_rows)):
        if not rows:
            continue
        out.append(f"  <{section}>")
        for row in rows:
            cells = []
            for c in row:
                span = f' colspan="{c.colspan}"' if c.colspan > 1 else ""
                if c.header:
                    scope = f' scope="{"colgroup" if c.colspan > 1 and c.scope == "col" else c.scope}"' if c.scope else ""
                    cells.append(f"<th{scope}{span}>{_autolink(c.text)}</th>")
                else:
                    cells.append(f"<td{span}>{_autolink(c.text)}</td>")
            out.append("    <tr>" + "".join(cells) + "</tr>")
        out.append(f"  </{section}>")
    out.append("</table>")
    out.append("</div>")
    return "\n".join(out)


def render_figure(f: Figure, opts: RenderOptions) -> str:
    if opts.embed_images:
        mime = "image/jpeg" if f.ext in ("jpg", "jpeg") else "image/png"
        src = f"data:{mime};base64," + base64.b64encode(f.data).decode("ascii")
    else:
        src = f"{opts.image_dir}/{f.filename}" if opts.image_dir else f.filename
    if f.alt_source == "missing":
        alt = f"[Description needed: {f.id}, page {f.page}]"
        cls = ' class="needs-alt"'
    else:
        alt = f.alt or ""
        cls = ""
    img = f'<img src="{_esc(src)}" alt="{_esc(alt)}" width="{f.width}" height="{f.height}" id="{f.id}"{cls} loading="lazy">'
    if f.caption:
        return f"<figure>\n  {img}\n  <figcaption>{_esc(f.caption)}</figcaption>\n</figure>"
    if f.alt_source == "decorative":
        return img
    return f"<figure>\n  {img}\n</figure>"


def render_body(doc: Document, opts: RenderOptions) -> str:
    _footnotes["numbers"] = {fn.number for fn in doc.footnotes}
    _footnotes["referenced"] = set()
    out: list[str] = []
    offset = opts.heading_offset
    first_h1_done = False
    for b in doc.blocks:
        if isinstance(b, Heading):
            level = min(6, b.level + offset)
            out.append(f'<h{level} id="{b.id}">{_inline(_plain_spans(b.spans))}</h{level}>')
            if b.level == 1 and not first_h1_done:
                first_h1_done = True
                out.append(_doc_info(doc, opts))
                if opts.toc:
                    out.append(_toc(doc, offset))
        elif isinstance(b, Paragraph):
            out.append(f"<p>{_inline(b.spans)}</p>")
        elif isinstance(b, ListBlock):
            out.append(render_list(b))
        elif isinstance(b, Table):
            out.append(render_table(b))
        elif isinstance(b, Figure):
            out.append(render_figure(b, opts))
        elif isinstance(b, Footnote):
            continue  # collected into the Notes section at the end
        elif isinstance(b, PageAnchor):
            if opts.show_page_numbers and b.page > 1:
                out.append(f'<p class="page-marker" id="page-{b.page}">Page {b.page}</p>')
            else:
                out.append(f'<span id="page-{b.page}"></span>')
    out.append(_notes(doc, offset))
    return "\n".join(out)


def _notes(doc: Document, offset: int) -> str:
    notes = doc.footnotes
    if not notes:
        return ""
    level = min(6, 2 + offset)
    parts = ['<section class="footnotes" aria-labelledby="notes-heading">',
             f'<h{level} id="notes-heading">Notes</h{level}>', "<ol>"]
    seen = set()
    for fn in notes:
        value = f' value="{fn.number}"' if fn.number.isdigit() else ""
        fid = f' id="fn-{fn.number}"' if fn.number not in seen else ""
        seen.add(fn.number)
        back = (f' <a href="#fnref-{fn.number}">Back to text<span class="visually-hidden"> for note {fn.number}</span></a>'
                if fn.number in _footnotes["referenced"] else "")
        parts.append(f"  <li{fid}{value}>{_inline(fn.spans)}{back}</li>")
    parts += ["</ol>", "</section>"]
    return "\n".join(parts)


def _doc_info(doc: Document, opts: RenderOptions) -> str:
    parts = ['<div class="doc-info">']
    parts.append(
        f"<p>This page is an accessible web version of the document "
        f"<cite>{_esc(doc.source_name)}</cite> ({doc.page_count} page{'s' if doc.page_count != 1 else ''}).</p>"
    )
    if opts.pdf_href:
        size = f", {_human_size(doc.source_size)}" if doc.source_size else ""
        parts.append(f'<p><a href="{_esc(opts.pdf_href)}">Download the original PDF{size}</a></p>')
    parts.append("</div>")
    return "\n".join(parts)


def _toc(doc: Document, offset: int) -> str:
    heads = [h for h in doc.headings if h.level >= 2]
    if len(heads) < 3:
        return ""
    min_level = min(h.level for h in heads)
    # When headings already carry numbers ("I. DEFINITIONS", "2.1 Scope"), hide the list's own numbers.
    numbered = sum(1 for h in heads if re.match(r"^\(?([0-9]+|[A-Za-z]|[IVXLCivxlc]+)[.)\s]", h.text)) > len(heads) / 2
    ol = '<ol role="list" class="plain">' if numbered else "<ol>"
    html_parts = ['<nav class="toc" aria-labelledby="toc-heading">',
                  f'<h{min(6, 2 + offset)} id="toc-heading">Contents</h{min(6, 2 + offset)}>', ol]
    prev = min_level
    first = True
    for h in heads:
        lvl = max(min_level, min(h.level, 4))
        if first:
            html_parts.append("<li>")
            first = False
        elif lvl > prev:
            html_parts.append(f"{ol}\n<li>" * (lvl - prev))
        elif lvl < prev:
            html_parts.append("</li>\n</ol>" * (prev - lvl) + "</li>\n<li>")
        else:
            html_parts.append("</li>\n<li>")
        html_parts.append(f'<a href="#{h.id}">{_esc(h.text)}</a>')
        prev = lvl
    html_parts.append("</li>\n</ol>" * (prev - min_level) + "</li>\n</ol>\n</nav>")
    return "\n".join(html_parts)


def _footer(doc: Document, opts: RenderOptions) -> str:
    today = _dt.date.today().isoformat()
    parts = ["<footer>", f"<p>Converted from PDF on <time datetime=\"{today}\">{today}</time>.</p>"]
    if opts.contact:
        c = opts.contact
        href = c if re.match(r"^(https?:|mailto:|tel:)", c) else (f"mailto:{c}" if "@" in c else c)
        parts.append(f'<p>If you have trouble using this content, contact <a href="{_esc(href)}">{_esc(c)}</a>.</p>')
    parts.append("</footer>")
    return "\n".join(parts)


def render_html(doc: Document, opts: Optional[RenderOptions] = None) -> str:
    opts = opts or RenderOptions()
    theme = THEMES.get(opts.theme, THEMES["default"])
    body = render_body(doc, opts)

    if opts.fragment:
        css = CSS.format(root=".apdf", **theme)
        return (
            f'<div class="apdf" lang="{_esc(doc.lang)}">\n<style>{css}</style>\n'
            f"{body}\n{_footer(doc, opts)}\n</div>\n"
        )

    css = CSS.format(root="body", **theme)
    site = f'<header class="apdf"><p>{_esc(opts.site_name)}</p></header>\n' if opts.site_name else ""
    return f"""<!DOCTYPE html>
<html lang="{_esc(doc.lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(doc.title)}</title>
<meta name="generator" content="accessiblepdf">
<style>{css}
body {{ margin: 0; }}
</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
{site}<main id="main" class="apdf" tabindex="-1">
{body}
{_footer(doc, opts)}
</main>
</body>
</html>
"""
