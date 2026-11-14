"""Intermediate document model produced by the extractor and consumed by the renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class Span:
    """A run of inline text with uniform formatting."""

    text: str
    bold: bool = False
    italic: bool = False
    superscript: bool = False
    href: Optional[str] = None


@dataclass
class Heading:
    level: int
    spans: list[Span]
    page: int
    id: str = ""

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans).strip()


@dataclass
class Paragraph:
    spans: list[Span]
    page: int

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans).strip()


@dataclass
class ListItem:
    spans: list[Span]
    value: Optional[int] = None
    children: Optional["ListBlock"] = None


@dataclass
class ListBlock:
    ordered: bool
    items: list[ListItem]
    page: int
    list_type: Optional[str] = None  # "a", "A", "i", "I" for ordered lists


@dataclass
class TableCell:
    text: str
    colspan: int = 1
    header: bool = False
    scope: Optional[str] = None  # "col" or "row"


@dataclass
class Table:
    rows: list[list[TableCell]]
    page: int
    id: str = ""
    caption: Optional[str] = None


@dataclass
class Figure:
    id: str
    page: int
    data: bytes
    ext: str
    width: int
    height: int
    alt: Optional[str] = None
    alt_source: str = "missing"  # "missing", "pdf-tags", "user", "decorative"
    caption: Optional[str] = None
    kind: str = "image"  # "image", "graphic" (rendered vector drawing), "scan"
    context_text: str = ""  # nearby / embedded text to help authors write alt text
    filename: str = ""


@dataclass
class Footnote:
    number: str
    spans: list[Span]
    page: int


@dataclass
class PageAnchor:
    page: int


Block = Union[Heading, Paragraph, ListBlock, Table, Figure, PageAnchor, Footnote]


@dataclass
class Issue:
    severity: str  # "fail" (must fix before publishing), "review" (human check), "info"
    wcag: str  # e.g. "1.1.1 Non-text Content"
    message: str
    page: Optional[int] = None
    target: Optional[str] = None


@dataclass
class Document:
    title: str
    lang: str
    blocks: list[Block]
    page_count: int
    source_name: str
    source_size: int = 0
    metadata: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def figures(self) -> list[Figure]:
        return [b for b in self.blocks if isinstance(b, Figure)]

    @property
    def footnotes(self) -> list[Footnote]:
        return [b for b in self.blocks if isinstance(b, Footnote)]

    @property
    def headings(self) -> list[Heading]:
        return [b for b in self.blocks if isinstance(b, Heading)]

    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "fail"]
