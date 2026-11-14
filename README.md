# AccessiblePDF

Convert PDFs into web pages that meet **WCAG 2.1 AA**, so a link to a PDF can point to an accessible HTML page instead.

It was written for sites such as ecrt.umich.edu and disability.umich.edu, but it works for any site.

For each PDF the tool produces:

| File | What it is |
|---|---|
| `name.html` | The accessible web page (or an HTML fragment for a CMS) |
| `name_files/` | Images taken from the PDF |
| `name.report.md` | What a person must fix or check before publishing |
| `name.alt-text.json` | A place to write image descriptions, which are used on the next run |
| `index.html` | A summary of every document converted in the run, with its status |
| `link-map.csv` | (crawl mode) Which page linked to which PDF, so you know which links to update |

## What it does automatically

| WCAG 2.1 success criterion | How the converter handles it |
|---|---|
| 1.1.1 Non-text Content | Pulls out images and charts. It uses alt text from tagged PDFs when it exists; otherwise the document is marked **NOT READY** until a description is added. Tiny decorative images and full-page backgrounds are left out. |
| 1.3.1 Info and Relationships | Real `<h1>`–`<h6>` headings (from font size and weight, from outline numbering such as I. / A. / 1., and from PDF bookmarks when present), `<ul>`/`<ol>` lists including nested lists, `<table>` with `<th scope>` headers, captions, `<figure>`/`<figcaption>`, footnotes as linked notes with "Back to text" links, and line breaks kept in addresses |
| 1.3.2 Meaningful Sequence | Rebuilds reading order, including two-column layouts. Removes repeated headers, footers and page numbers. Joins paragraphs that were split across pages. |
| 1.4.3 Contrast (Minimum) / 1.4.11 Non-text Contrast | Built-in colour themes all meet at least 4.5:1 for text and 3:1 for borders and focus outlines |
| 1.4.4 Resize Text / 1.4.10 Reflow / 1.4.12 Text Spacing | Responsive layout in `rem` units with no fixed heights. Works at 320px wide and at 400% zoom. Wide tables scroll inside their own keyboard-focusable region. |
| 2.4.1 Bypass Blocks | "Skip to main content" link and a `<main>` landmark |
| 2.4.2 Page Titled | `<title>` comes from the document's visible title. Junk metadata such as "Microsoft Word - doc1.docx" is ignored. |
| 2.4.4 Link Purpose | Keeps the PDF's links with their own link text. Internal "go to page" links still work. Plain URLs and email addresses become links. |
| 2.4.6 / 2.4.10 Headings | Heading levels are fixed so none are skipped. A table of contents is added when the document has 3 or more sections. |
| 2.4.7 Focus Visible | A 3px focus outline on every link and focusable element |
| 3.1.1 Language of Page | `lang` comes from the PDF, or from `--lang` |
| 4.1.1 / 4.1.2 | Plain semantic HTML with no scripts |

Scanned PDFs can be OCR'd with `--ocr`. This needs [Tesseract](https://github.com/tesseract-ocr/tesseract) installed.

## Example

`examples/` holds the output for ECRT's [student discrimination and harassment policy PDF](https://ecrt.umich.edu/wp-content/uploads/discrimination-harassment-student-policy.pdf). The result has a full section outline, 6 linked footnotes, links kept, and no blocking issues. It was made with:

```bash
accessiblepdf convert discrimination-harassment-student-policy.pdf -o examples --theme umich \
    --pdf-base-url https://ecrt.umich.edu/wp-content/uploads
```

## What still needs a person

No converter can do these reliably. The report lists each one that applies to a document:

- **Writing image descriptions.** The tool can't know what a photo or chart means.
- Checking that the heading outline and reading order match the original.
- Checking header cells in complex tables.
- **Fillable PDF forms.** These are flagged and have to be rebuilt as web forms.
- Meaning carried only by colour or position, math, and passages in another language.

## Install

You need Python 3.9 or newer.

```bash
git clone <this repository>
cd AccessiblePDF
python3 -m pip install .
```

That gives you the `accessiblepdf` command. You can also run it without installing: `pip install -r requirements.txt`, then `python3 -m accessiblepdf ...`.

Optional: Tesseract, for scanned PDFs (`brew install tesseract`, `apt install tesseract-ocr`, or the Windows installer from the UB Mannheim build).

## Usage

### Convert PDFs you have

```bash
accessiblepdf convert handbook.pdf -o out/
accessiblepdf convert ~/Downloads/forms/ -o out/                 # every PDF in a folder
accessiblepdf convert https://example.edu/files/guide.pdf -o out/
```

### Convert every PDF linked from a page

```bash
accessiblepdf crawl https://ecrt.umich.edu/ --list-only           # see what it would convert
accessiblepdf crawl https://ecrt.umich.edu/ https://disability.umich.edu/ -o out/ --theme umich \
    --contact "ecrt@umich.edu"
```

Crawl mode reads only the pages you name; it doesn't follow links further into the site. It downloads only PDFs on the same site (for example `*.umich.edu`) unless you add `--any-domain`. It respects `robots.txt` and waits one second between downloads. `out/link-map.csv` lists every link you will need to update.

### Typical workflow

1. **Convert:** `accessiblepdf convert guide.pdf -o out/`
2. **Read** `out/guide.report.md`. **NOT READY** usually means some images have no description yet.
3. **Describe images.** Open `out/guide.alt-text.json` and replace each `null` with a description, or with `""` for a purely decorative image:
   ```json
   {
     "fig-1": "Map showing the office on the second floor of the Student Services Building",
     "fig-2": "Bar chart: requests grew from 120 in 2021 to 210 in 2024"
   }
   ```
   For charts, the report lists the text found inside the chart to help you write the description.
4. **Run the same command again.** The descriptions are picked up automatically.
5. **Check** the page in a browser at 200% zoom and with a screen reader, using the checklist at the end of the report.
6. **Publish** and point the old PDF links at the new page.

### Options

| Option | Purpose |
|---|---|
| `-o DIR` | Output folder (default `accessible-html`) |
| `--fragment` | Write only the content (no `<html>`/`<head>`), with headings starting at `<h2>`, for pasting into WordPress or another CMS that already has a page title |
| `--embed-images` | Put images inside the HTML, so it is one self-contained file |
| `--theme umich` | Michigan Blue headings and links. Maize appears only as a decorative accent bar, never as text. |
| `--lang es` | Language, if the PDF doesn't declare one |
| `--title "..."` | Override the page title |
| `--alt-text file.json` | Use image descriptions from another file |
| `--ocr` / `--ocr-lang spa` | OCR scanned pages |
| `--contact EMAIL_OR_URL` | Footer line: "If you have trouble using this content, contact ..." |
| `--pdf-base-url URL` | Link "Download the original PDF" to `URL/<file name>` instead of copying the PDF next to the page |
| `--no-pdf-link` | Don't offer the original PDF |
| `--page-numbers` | Show "Page N" markers, which helps when people refer to page numbers |
| `--no-graphics` | Don't turn vector charts and diagrams into images |
| `--json` | Also write a machine-readable report |
| `--strict` | Exit with status 2 if any document is NOT READY (useful in scripts) |

### Putting the output in WordPress

- **Full page:** upload `name.html` and its `name_files/` folder as-is, and link to it.
- **Inside an existing page:** convert with `--fragment --embed-images`. Then paste the file's contents into a **Custom HTML** block. WordPress may strip the `<style>` block for users who are not administrators. If that happens, copy the CSS into *Appearance → Customize → Additional CSS* once and it applies to every converted document.

## Automated checking (optional)

`tools/check_accessibility.mjs` runs the [axe-core](https://github.com/dequelabs/axe-core) WCAG 2.0/2.1 A and AA rules in a real browser. It also checks reflow at 320px and the WCAG text-spacing override.

```bash
cd tools
npm install
npx playwright install chromium
node check_accessibility.mjs ../out/*.html
```

Automated tools catch only some accessibility problems. Passing them is necessary but not sufficient.

## Tests

```bash
pip install reportlab
python3 -m unittest discover tests
```

The tests build a realistic sample PDF, then check the output: headings, lists, table, links, header/footer removal, charts, alt-text round trip, and fragment mode.

## Limitations

- Heading detection relies on visual cues (larger or bold text). Documents that use the same font for everything will have few or no headings, and the report says so.
- Very complex layouts, such as posters, brochures with text boxes everywhere, or three or more columns, may need hand editing of the HTML.
- Tables without ruling lines may not be detected and can come out as paragraphs.
- Alt text from tagged PDFs is used only when the number of tagged figures matches the number of images found, to avoid attaching a description to the wrong image.

## License note

This tool depends on [PyMuPDF](https://pymupdf.readthedocs.io/), which is licensed under the GNU AGPL v3 (or a commercial license from Artifex). Using the tool internally is fine. If you redistribute it, or offer it as a network service to others, the AGPL terms apply.
