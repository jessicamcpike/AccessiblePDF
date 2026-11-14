"""End-to-end tests. Run with:  python -m unittest discover tests"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import reportlab  # noqa: F401
    HAVE_REPORTLAB = True
except ImportError:
    HAVE_REPORTLAB = False


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "accessiblepdf", *args], cwd=ROOT,
                          capture_output=True, text=True)


@unittest.skipUnless(HAVE_REPORTLAB, "reportlab is needed to build the sample PDF")
class ConvertSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, HERE)
        from make_sample_pdf import build

        cls.tmp = tempfile.mkdtemp()
        cls.pdf = os.path.join(cls.tmp, "sample.pdf")
        build(cls.pdf)
        cls.out = os.path.join(cls.tmp, "out")
        cls.result = run_cli("convert", cls.pdf, "-o", cls.out, "--contact", "access@example.edu")
        with open(os.path.join(cls.out, "sample.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_exit_ok(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_document_basics(self):
        self.assertIn('<html lang="en">', self.html)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', self.html)
        self.assertIn("<title>Requesting Academic Accommodations</title>", self.html)
        self.assertIn('href="#main"', self.html)
        self.assertEqual(self.html.count("<h1"), 1)

    def test_heading_outline(self):
        levels = [int(l) for l in re.findall(r"<h([1-6]) id=", self.html)]
        self.assertEqual(levels[0], 1)
        for a, b in zip(levels, levels[1:]):
            self.assertLessEqual(b, a + 1, "heading levels must not skip")
        self.assertIn(">Who Is Eligible</h2>", self.html)
        self.assertRegex(self.html, r"<h3 id=\"what-to-bring\">")

    def test_lists(self):
        self.assertRegex(self.html, r"<ol>\s*<li>Complete the online application form.</li>")
        self.assertRegex(self.html, r"<ul>\s*<li>Photo identification</li>")

    def test_table(self):
        self.assertIn('<th scope="col">Request type</th>', self.html)
        self.assertIn("<td>Exam accommodations</td>", self.html)
        self.assertIn('role="region"', self.html)

    def test_links(self):
        self.assertIn('<a href="https://example.edu/accommodations">the accommodations website</a>', self.html)
        self.assertIn('href="mailto:help@example.edu"', self.html)

    def test_headers_footers_removed(self):
        self.assertNotIn("Office of Example Services | Student Guide", self.html)
        self.assertNotRegex(self.html, r"<p>Page \d</p>")

    def test_images_need_alt(self):
        self.assertIn('class="needs-alt"', self.html)
        with open(os.path.join(self.out, "sample.report.md"), encoding="utf-8") as fh:
            self.assertIn("NOT READY", fh.read())
        self.assertIn("<figcaption>Figure 1: Map of the office location.</figcaption>", self.html)
        # chart axis labels are folded into the chart image, not left as stray paragraphs
        self.assertNotIn("<p>200</p>", self.html)

    def test_alt_text_round_trip(self):
        out = os.path.join(self.tmp, "out-alt")
        os.makedirs(out)
        with open(os.path.join(out, "sample.alt-text.json"), "w") as fh:
            json.dump({"fig-1": "Map of the office", "fig-2": "Requests rose from 120 in 2021 to 210 in 2024."}, fh)
        r = run_cli("convert", self.pdf, "-o", out, "--strict")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(out, "sample.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('alt="Map of the office"', html)
        self.assertNotIn('class="needs-alt"', html)

    def test_fragment(self):
        out = os.path.join(self.tmp, "out-frag")
        r = run_cli("convert", self.pdf, "-o", out, "--fragment", "--embed-images", "--theme", "umich")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(out, "sample.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertTrue(html.startswith('<div class="apdf"'))
        self.assertNotIn("<h1", html)
        self.assertIn("<h2 id=\"requesting-academic-accommodations\">", html)
        self.assertIn("data:image/png;base64,", html)


if __name__ == "__main__":
    unittest.main()


def build_policy_pdf(path):
    """A policy-style PDF: numbered bold headings in body-size type, footnotes, phone, address."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = [72]

    def text(s, x=72, size=12, font="tiro", step=16):
        page.insert_text((x, y[0]), s, fontsize=size, fontname=font)
        y[0] += step

    text("THE EXAMPLE POLICY ON", 150, 14, "tibo", 17)
    text("CAMPUS CONDUCT", 190, 14, "tibo", 26)
    text("I.", 72, 12, "tibo", 0)
    text("DEFINITIONS", 108, 12, "tibo", 20)
    text("A.  Harassment", 117, 12, "tibo", 20)
    text("Conduct that is unwelcome and offensive is prohibited by this Policy and by", step=14)
    page.insert_text((72 + 0, y[0]), "related rules", fontsize=12, fontname="tiro")
    end = 72 + pymupdf.get_text_length("related rules", fontname="tiro", fontsize=12)
    page.insert_text((end + 0.5, y[0] - 4), "1", fontsize=7, fontname="tiro")
    y[0] += 14
    text("Resources may not share information unless: (1) given permission; (2) required", step=14)
    text("by law.", step=24)
    text("Equity Office", step=14)
    text("2030 Administrative Building", step=14)
    text("Ann Arbor, Michigan 48109", step=14)
    text("(734) 763-0235", step=24)
    text("B.  Retaliation", 117, 12, "tibo", 20)
    text("Retaliation means conduct that intimidates any individual.", step=20)
    text("II.", 72, 12, "tibo", 0)
    text("REPORTING", 108, 12, "tibo", 20)
    text("Reports may be made at any time.", step=20)
    page.insert_text((72, 700), "1", fontsize=6, fontname="tiro")
    page.insert_text((76, 704), "See the related rules document for details.", fontsize=10, fontname="tiro")
    doc.save(path)


class ConvertPolicyStyle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        pdf = os.path.join(cls.tmp, "policy.pdf")
        build_policy_pdf(pdf)
        out = os.path.join(cls.tmp, "out")
        r = run_cli("convert", pdf, "-o", out)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(out, "policy.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_title(self):
        self.assertIn("<h1 id=\"the-example-policy-on-campus-conduct\">THE EXAMPLE POLICY ON CAMPUS CONDUCT</h1>",
                      self.html)

    def test_numbered_bold_headings_get_outline_levels(self):
        self.assertRegex(self.html, r"<h2 id=\"[^\"]+\">I\. DEFINITIONS</h2>")
        self.assertRegex(self.html, r"<h3 id=\"[^\"]+\">A\. Harassment</h3>")
        self.assertRegex(self.html, r"<h2 id=\"[^\"]+\">II\. REPORTING</h2>")
        self.assertIn('<ol role="list" class="plain">', self.html)

    def test_footnote(self):
        self.assertIn('href="#fn-1" id="fnref-1"', self.html)
        self.assertRegex(self.html, r'<li id="fn-1" value="1">See the related rules document for details\.')

    def test_inline_enumeration_and_phone_are_not_lists(self):
        self.assertIn("unless: (1) given permission; (2) required by law.", self.html)
        self.assertNotIn('start="734"', self.html)

    def test_address_keeps_line_breaks(self):
        self.assertIn("Equity Office<br>\n2030 Administrative Building<br>", self.html)
