"""Accessibility review report for a converted document."""

from __future__ import annotations

import json

from .model import Document

SEVERITY_LABEL = {
    "fail": "Must fix before publishing",
    "review": "Check by hand",
    "info": "For information",
}


def status(doc: Document) -> str:
    if doc.blocking_issues():
        return "NOT READY"
    if any(i.severity == "review" for i in doc.issues):
        return "READY AFTER REVIEW"
    return "READY"


def markdown_report(doc: Document, html_name: str, alt_template_name: str | None) -> str:
    lines = [
        f"# Accessibility report: {doc.title}",
        "",
        f"- Source: `{doc.source_name}` ({doc.page_count} pages)",
        f"- Output: `{html_name}`",
        f"- Language: `{doc.lang}`",
        f"- Status: **{status(doc)}**",
        "",
        "Automated conversion handles structure (headings, lists, tables, links, reading order, "
        "language, title, reflow and contrast) but cannot judge meaning. A person must check the items below "
        "and should read the page once with a screen reader or at least at 200% zoom before publishing.",
        "",
    ]
    for sev in ("fail", "review", "info"):
        items = [i for i in doc.issues if i.severity == sev]
        if not items:
            continue
        lines += [f"## {SEVERITY_LABEL[sev]} ({len(items)})", ""]
        for i in items:
            where = f" (page {i.page})" if i.page else ""
            wcag = f"[{i.wcag}] " if i.wcag and i.wcag != "-" else ""
            lines.append(f"- {wcag}{i.message}{where}")
        lines.append("")

    figs = doc.figures
    if figs:
        lines += ["## Images", "", "| ID | Page | Type | Alt text source | Alt text |", "|---|---|---|---|---|"]
        for f in figs:
            alt = (f.alt or "").replace("|", "\\|") if f.alt_source != "missing" else "_missing_"
            if f.alt_source == "decorative":
                alt = "_decorative_"
            lines.append(f"| {f.id} | {f.page} | {f.kind} | {f.alt_source} | {alt} |")
        lines.append("")
        if alt_template_name:
            lines += [
                "### Adding alt text",
                "",
                f"Edit `{alt_template_name}`: replace each `null` with a short description of what the image "
                "conveys, or `\"\"` if the image is purely decorative. Then run the conversion again with "
                f"`--alt-text {alt_template_name}`.",
                "",
            ]
        ctx = [f for f in figs if f.context_text]
        if ctx:
            lines += ["Text found inside charts/diagrams (use it when writing descriptions):", ""]
            for f in ctx:
                lines.append(f"- **{f.id}**: {f.context_text[:500]}")
            lines.append("")

    lines += [
        "## Manual checklist",
        "",
        "- [ ] Headings reflect the document's real outline",
        "- [ ] Reading order matches the original",
        "- [ ] Every informative image has an accurate description; decorative images are marked decorative",
        "- [ ] Tables have correct header cells; complex tables are simplified or explained",
        "- [ ] Link text makes sense out of context (avoid bare \"click here\")",
        "- [ ] Information conveyed only by colour, position, or shape in the PDF is also stated in text",
        "- [ ] Abbreviations, math, and non-English passages read correctly",
        "",
    ]
    return "\n".join(lines)


def alt_template(doc: Document) -> dict:
    out = {}
    for f in doc.figures:
        if f.alt_source in ("user", "decorative", "pdf-tags"):
            out[f.id] = f.alt
        else:
            out[f.id] = None
    return out


def json_report(doc: Document, html_name: str) -> str:
    return json.dumps(
        {
            "title": doc.title,
            "source": doc.source_name,
            "output": html_name,
            "pages": doc.page_count,
            "lang": doc.lang,
            "status": status(doc),
            "issues": [i.__dict__ for i in doc.issues],
            "figures": [
                {"id": f.id, "page": f.page, "kind": f.kind, "alt": f.alt, "alt_source": f.alt_source,
                 "file": f.filename}
                for f in doc.figures
            ],
        },
        indent=2,
    )
