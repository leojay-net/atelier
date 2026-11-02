"""Regenerate the eval suite's document inputs (Word, PowerPoint, Excel, PDF), all synthetic.

    uv run python tests/evals/make_doc_inputs.py

The committed files are what the document tasks use; rerun only when adding an input. Files are
written through the engine's own deterministic writers, so regenerating gives the same bytes.

    invoice_template.docx   {{company}} {{invoice_no}} {{customer}} {{date}} {{total}}
    offer_letter.docx       {{name}} {{role}} {{salary}} {{start_date}}
    report.docx             a report with headings, "Old Corp" in the body and header
    report_layout.docx      like report.docx plus the marker {{items_table}} and {{logo}}
    deck_template.pptx      two slides with {{company}} and {{quarter}}; layouts of the default deck
    deck_plain.pptx         the same two slides without placeholders
    budget.xlsx             planned/actual by line, totals in row 7 and ratios =B/C (all fine)
    budget_errors.xlsx      the same, but Rent's actual is 0: its ratio is #DIV/0! once calculated
    sales.xlsx              a clean quarterly sheet with an empty Total row to fill
    contract.pdf            3 pages: two names, two social security numbers, an account number
    handbook.pdf            6 pages, "Chapter n" on each
    agreement_a.pdf         3 pages       agreement_b.pdf   2 pages       terms.pdf   1 page
    photo_report.pdf        2 pages, one holding a large photograph-like picture (~1 MB)
    application.pdf         a form with text fields full_name, city and a checkbox agree
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

import docx
import numpy as np
import openpyxl
import pikepdf
from docx.shared import Pt
from pptx import Presentation

sys.path.insert(0, str(Path(__file__).parents[2] / "src"))

from atelier.docs import package, pdfio

HERE = Path(__file__).parent / "inputs"


def save_docx(d: docx.document.Document, name: str) -> None:
    raw = HERE / "_raw.docx"
    d.save(str(raw))
    package.write(raw, HERE / name)
    raw.unlink()


def invoice() -> None:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "{{company}}"
    d.add_heading("Invoice {{invoice_no}}", level=1)
    d.add_paragraph("Bill to: {{customer}}")
    d.add_paragraph("Date: {{date}}")
    d.add_paragraph("Services rendered as agreed.")
    p = d.add_paragraph()
    p.add_run("Total due: ").bold = True
    p.add_run("{{total}}")
    save_docx(d, "invoice_template.docx")


def offer() -> None:
    d = docx.Document()
    d.add_heading("Offer of employment", level=1)
    d.add_paragraph("Dear {{name}},")
    d.add_paragraph(
        "We are pleased to offer you the position of {{role}} at a salary of {{salary}} per year, "
        "starting on {{start_date}}."
    )
    d.add_paragraph("Please sign and return a copy of this letter.")
    save_docx(d, "offer_letter.docx")


def report(marked: bool) -> None:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "Old Corp - internal"
    d.add_heading("Quarterly report", level=0)
    for n, topic in enumerate(("Results", "Customers", "Outlook"), start=1):
        d.add_heading(f"{n}. {topic}", level=1)
        for _ in range(3):
            d.add_paragraph(
                f"Old Corp finished the quarter with steady growth in {topic.lower()}. "
                "Teams reported progress on every planned milestone, and the leadership group "
                "agreed to keep the current priorities for the next period. " * 3
            )
        if marked and n == 1:
            d.add_paragraph("{{items_table}}")
        if marked and n == 2:
            d.add_paragraph("{{logo}}")
    d.paragraphs[-1].runs[0].font.size = Pt(11)
    save_docx(d, "report_layout.docx" if marked else "report.docx")


def deck_plain() -> None:
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Business review"
    s.placeholders[1].text = "Acme Studio"
    a = prs.slides.add_slide(prs.slide_layouts[1])
    a.shapes.title.text = "Agenda"
    a.placeholders[1].text = "Results"
    raw = HERE / "_raw.pptx"
    prs.save(str(raw))
    package.write(raw, HERE / "deck_plain.pptx")
    raw.unlink()


def deck() -> None:
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "{{company}} - {{quarter}}"
    s.placeholders[1].text = "Business review"
    a = prs.slides.add_slide(prs.slide_layouts[1])
    a.shapes.title.text = "Agenda"
    a.placeholders[1].text = "Results"
    raw = HERE / "_raw.pptx"
    prs.save(str(raw))
    package.write(raw, HERE / "deck_template.pptx")
    raw.unlink()


def budget(name: str, rent_actual: int) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Budget"
    ws.append(["Item", "Planned", "Actual", "Ratio"])
    lines = (
        ("Rent", 1200, rent_actual),
        ("Salaries", 5400, 5350),
        ("Software", 800, 910),
        ("Travel", 600, 450),
    )
    for i, (item, planned, actual) in enumerate(lines, start=2):
        ws.append([item, planned, actual, f"=B{i}/C{i}"])
    ws["A7"] = "Total"
    ws["B7"] = "=SUM(B2:B5)"
    ws["C7"] = "=SUM(C2:C5)"
    raw = HERE / "_raw.xlsx"
    wb.save(str(raw))
    package.write(raw, HERE / name)
    raw.unlink()


def workbooks() -> None:
    budget("budget.xlsx", 1150)
    budget("budget_errors.xlsx", 0)
    raw = HERE / "_raw.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Q1", "Q2", "Q3", "Q4"])
    for row in (
        ("North", 120, 135, 150, 170),
        ("South", 90, 95, 110, 120),
        ("West", 70, 88, 91, 99),
    ):
        ws.append(list(row))
    ws["A5"] = "Total"
    wb.save(str(raw))
    package.write(raw, HERE / "sales.xlsx")
    raw.unlink()


def pdf(name: str, pages: list[list[str]]) -> None:
    pdfio.save(pdfio.build_text_pdf(pages), HERE / name)


def pdfs() -> None:
    pdf("contract.pdf", [
        ["SERVICE CONTRACT", "Between Acme Studio and John Smith.",
         "John Smith, SSN 123-45-6789, agrees to the terms below."],
        ["2. Payment", "Payments are made to account 998877 held by Mary Jones.",
         "Mary Jones, SSN 987-65-4321, is the authorised signatory."],
        ["3. Term", "This contract runs for twelve months from signature.",
         "Signed for Acme Studio."],
    ])  # fmt: skip
    pdf(
        "handbook.pdf",
        [[f"Chapter {n}", f"This is chapter {n} of the employee handbook."] for n in range(1, 7)],
    )
    pdf(
        "agreement_a.pdf",
        [[f"Agreement A, page {n}", "Standard terms apply."] for n in range(1, 4)],
    )
    pdf(
        "agreement_b.pdf",
        [[f"Agreement B, page {n}", "Special conditions apply."] for n in range(1, 3)],
    )
    pdf("terms.pdf", [["Terms and conditions", "By signing you accept these terms."]])

    report = pdfio.build_text_pdf(
        [["Photo report", "Site visit, page 1."], ["Site visit, page 2."]]
    )
    rng = np.random.default_rng(11)
    grad = np.linspace(20, 235, 900)[None, :, None] * np.ones((600, 1, 3))
    pixels = np.clip(grad + rng.normal(0, 14, (600, 900, 3)), 0, 255).astype("uint8")
    xo = report.make_stream(
        zlib.compress(pixels.tobytes()), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
        Width=900, Height=600, ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
        Filter=pikepdf.Name.FlateDecode,
    )  # fmt: skip
    page = report.pages[1]
    page.obj.Resources.XObject = pikepdf.Dictionary(Im0=xo)
    page.contents_add(report.make_stream(b"q 400 0 0 267 100 300 cm /Im0 Do Q\n"))
    pdfio.save(report, HERE / "photo_report.pdf")

    form = pdfio.build_text_pdf([["Application form", "Name:", "City:", "I agree:"]])
    page = form.pages[0]
    helv = pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
    )
    refs = []
    for name, kind, rect, extra in (
        ("full_name", "/Tx", [140, 690, 400, 710], {"DA": pikepdf.String("/Helv 12 Tf 0 g")}),
        ("city", "/Tx", [140, 665, 400, 685], {"DA": pikepdf.String("/Helv 12 Tf 0 g")}),
        ("agree", "/Btn", [140, 640, 156, 656], {"V": pikepdf.Name.Off, "AS": pikepdf.Name.Off}),
    ):
        refs.append(form.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name(kind),
            T=pikepdf.String(name), Rect=rect, F=4, P=page.obj, **extra,
        )))  # fmt: skip
    page.obj.Annots = form.make_indirect(pikepdf.Array(refs))
    form.Root.AcroForm = form.make_indirect(pikepdf.Dictionary(
        Fields=pikepdf.Array(refs), DA=pikepdf.String("/Helv 12 Tf 0 g"),
        DR=pikepdf.Dictionary(Font=pikepdf.Dictionary(Helv=helv)), NeedAppearances=True,
    ))  # fmt: skip
    pdfio.save(form, HERE / "application.pdf")


def main() -> None:
    HERE.mkdir(exist_ok=True)
    invoice()
    offer()
    report(marked=False)
    report(marked=True)
    deck()
    deck_plain()
    workbooks()
    pdfs()


if __name__ == "__main__":
    main()
