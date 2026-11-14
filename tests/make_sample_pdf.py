"""Build a realistic sample PDF for tests (requires reportlab).

    python tests/make_sample_pdf.py tests/fixtures/sample.pdf
"""

import io
import sys

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _png() -> io.BytesIO:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.clear_with(200)
    for x in range(40, 160):
        for y in range(30, 90):
            pix.set_pixel(x, y, (0, 39, 76))
    return io.BytesIO(pix.tobytes("png"))


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawString(inch, letter[1] - 0.5 * inch, "Office of Example Services | Student Guide")
    canvas.drawRightString(letter[0] - inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build(path: str) -> None:
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=11, leading=15)
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=24, leading=28)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=16, leading=20)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=13, leading=16)

    story = [
        Paragraph("Requesting Academic Accommodations", h1),
        Paragraph(
            "This guide explains how students request accommodations. For details visit "
            '<link href="https://example.edu/accommodations" color="blue">the accommodations website</link> '
            "or email help@example.edu.", body),
        Paragraph("Who Is Eligible", h2),
        Paragraph("Any enrolled student with a documented condition may apply. " * 6, body),
        Paragraph("Steps to Apply", h2),
        ListFlowable(
            [ListItem(Paragraph(t, body)) for t in (
                "Complete the online application form.",
                "Upload supporting documentation.",
                "Meet with an access consultant.",
            )],
            bulletType="1",
        ),
        Paragraph("What to Bring", h3),
        ListFlowable(
            [ListItem(Paragraph(t, body), bulletColor=colors.black) for t in (
                "Photo identification",
                "Documentation from a qualified provider",
            )],
            bulletType="bullet", start="•",
        ),
        Paragraph("Processing Times", h2),
        Table(
            [["Request type", "Typical time", "Notes"],
             ["Exam accommodations", "5 business days", "Submit early"],
             ["Housing", "4 weeks", "Apply by May 1"],
             ["Captioning", "10 business days", "Per course"]],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]),
        ),
        Spacer(1, 12),
        Paragraph("Office Location", h2),
        Image(_png(), width=150, height=90),
        Paragraph("Figure 1: Map of the office location.", body),
    ]

    chart = Drawing(300, 180)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 40, 30, 240, 130
    bc.data = [(120, 150, 180, 210)]
    bc.categoryAxis.categoryNames = ["2021", "2022", "2023", "2024"]
    chart.add(bc)
    story += [
        Paragraph("Requests by Year", h2),
        Paragraph("The number of requests has grown each year, as the chart shows.", body),
        chart,
        Paragraph("Appeals", h2),
        Paragraph("If you disagree with a decision you may appeal within thirty days. " * 8, body),
    ]

    doc = SimpleDocTemplate(path, pagesize=letter, title="Microsoft Word - guide_v3.docx")
    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "sample.pdf")
