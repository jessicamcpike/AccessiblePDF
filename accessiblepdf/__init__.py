"""Convert PDFs into WCAG 2.1 AA accessible HTML pages."""

__version__ = "1.0.0"

from .extract import ExtractOptions, extract_document  # noqa: E402
from .render import RenderOptions, render_html  # noqa: E402

__all__ = ["ExtractOptions", "extract_document", "RenderOptions", "render_html", "__version__"]
