"""Regenerate the document fixtures (Word, PowerPoint, Excel, PDF), all synthetic.

    uv run python tests/fixtures/make_docs.py

The committed files are what tests use. They are written through the same deterministic writers
the engine uses, so regenerating them gives the same bytes.

    letter.docx   a letter whose placeholders are split across runs (`{{na` + `me}}`), with a
                  bold run, a table, a header, a footer, and the markers {{items_table}}, {{logo}}
    deck.pptx     three slides: a title with a placeholder, bullets with a split placeholder, and
                  boxes holding the markers {{figures}} and {{chart}}
    book.xlsx     a sheet with a placeholder title, numbers, a SUM formula and a broken formula
    report.pdf    three text pages (Helvetica, not embedded): "Page 1 of 3 ..."
    secret.pdf    one page that mentions a name, an account number and a phone number
    form.pdf      a form with text fields `full_name` and `city` and a checkbox `agree`
"""

from __future__ import annotations

from pathlib import Path

import docx
import openpyxl
import pikepdf
from docx.shared import Pt
from pptx import Presentation

from atelier.docs import package, pdfio

HERE = Path(__file__).parent / "docs"


def letter() -> None:
    d = docx.Document()
    d.sections[0].header.paragraphs[0].text = "{{company}} confidential"
    d.sections[0].footer.paragraphs[0].text = "Thank you for your business"
    p = d.add_paragraph()
    r = p.add_run("Dear ")
    r.bold = True
    p.add_run("{{na")
    p.add_run("me}},").italic = True
    q = d.add_paragraph()
    q.add_run("Your order {{order.id}} ships on ")
    tail = q.add_run("{{date}}")
    tail.font.size = Pt(14)
    q.add_run(".")
    table = d.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "Item"
    table.cell(0, 1).text = "Price"
    table.cell(1, 0).text = "Total"
    table.cell(1, 1).text = "{{total}}"
    d.add_paragraph("{{items_table}}")
    d.add_paragraph("{{logo}}")
    d.add_paragraph("Regards, the team")
    raw = HERE / "_raw.docx"
    d.save(str(raw))
    package.write(raw, HERE / "letter.docx")
    raw.unlink()


def deck() -> None:
    prs = Presentation()
    title = prs.slides.add_slide(prs.slide_layouts[0])
    title.shapes.title.text = "Welcome {{name}}"
    title.placeholders[1].text = "Quarterly review"
    body = prs.slides.add_slide(prs.slide_layouts[1])
    body.shapes.title.text = "Agenda"
    tf = body.placeholders[1].text_frame
    tf.text = "Revenue: "
    run = tf.paragraphs[0].add_run()
    run.text = "{{reve"
    run2 = tf.paragraphs[0].add_run()
    run2.text = "nue}}"
    from pptx.util import Inches

    third = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    third.shapes.title.text = "Numbers"
    box = third.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(3))
    box.text_frame.text = "{{figures}}"
    pic = third.shapes.add_textbox(Inches(1), Inches(5.5), Inches(3), Inches(1.5))
    pic.text_frame.text = "{{chart}}"
    raw = HERE / "_raw.pptx"
    prs.save(str(raw))
    package.write(raw, HERE / "deck.pptx")
    raw.unlink()


def book() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"
    ws["A1"] = "Report for {{client}}"
    for i, v in enumerate((10, 20, 30), start=3):
        ws[f"A{i}"] = f"Item {i - 2}"
        ws[f"B{i}"] = v
    ws["A6"] = "Total"
    ws["B6"] = "=SUM(B3:B5)"
    ws["B7"] = "=B3/0"
    raw = HERE / "_raw.xlsx"
    wb.save(str(raw))
    package.write(raw, HERE / "book.xlsx")
    raw.unlink()


def pdfs() -> None:
    pages = [[f"Page {n} of 3", "The quick brown fox jumps over the lazy dog."] for n in (1, 2, 3)]
    pdfio.save(pdfio.build_text_pdf(pages), HERE / "report.pdf")
    secret = [
        [
            "Client file",
            "Name: Jane Roe",
            "Account: 4111-2222-3333-4444",
            "Phone: +1 555 010 9999",
            "This line is public.",
        ]
    ]
    pdfio.save(pdfio.build_text_pdf(secret), HERE / "secret.pdf")


def form() -> None:
    """A one-page form: a text field `full_name`, a text field `city`, a checkbox `agree`."""
    pdf = pdfio.build_text_pdf([["Application form", "Name:", "City:", "I agree:"]])
    page = pdf.pages[0]
    helv = pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
    )
    fields = []

    def widget(name: str, kind: str, rect: list[float], **extra: object) -> None:
        d = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name(kind),
            T=pikepdf.String(name), Rect=rect, F=4, P=page.obj, **extra,
        )  # fmt: skip
        ref = pdf.make_indirect(d)
        fields.append(ref)

    widget("full_name", "/Tx", [140, 690, 400, 710], DA=pikepdf.String("/Helv 12 Tf 0 g"))
    widget("city", "/Tx", [140, 665, 400, 685], DA=pikepdf.String("/Helv 12 Tf 0 g"))
    widget("agree", "/Btn", [140, 640, 156, 656], V=pikepdf.Name.Off, AS=pikepdf.Name.Off)
    page.obj.Annots = pdf.make_indirect(pikepdf.Array(fields))
    pdf.Root.AcroForm = pdf.make_indirect(
        pikepdf.Dictionary(
            Fields=pikepdf.Array(fields),
            DA=pikepdf.String("/Helv 12 Tf 0 g"),
            DR=pikepdf.Dictionary(Font=pikepdf.Dictionary(Helv=helv)),
            NeedAppearances=True,
        )
    )
    pdfio.save(pdf, HERE / "form.pdf")


def main() -> None:
    HERE.mkdir(exist_ok=True)
    letter()
    deck()
    book()
    pdfs()
    form()


if __name__ == "__main__":
    main()
