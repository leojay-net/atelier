# ADR-0016: The document engine: safe packages, deterministic writers, honest redaction

- **Status:** Accepted
- **Date:** 2026-09-26 (decided under delegated authority)
- **Builds on:** [ADR-0006](0006-pdf-rendering-library.md) (pypdfium2 + pikepdf), [ADR-0013](0013-video-engine-graph-ops-and-fusion.md)

## Context
Phase 4 adds Word, Excel, PowerPoint and PDF. All four are untrusted, complex containers (zip
packages of XML, or object graphs) that programs open and execute things from: macros, remote
templates, JavaScript, links to linked images. The rest of the engine's promises still have to hold:
equal input and equal args give equal bytes, errors say how to fix them, and the agent sees short
descriptions instead of files.

## Decisions
1. **Libraries.** python-docx, python-pptx and openpyxl (MIT) for Office files, pikepdf (qpdf) and
   pypdfium2 for PDF (ADR-0006), lxml directly for XML. LibreOffice, in the reference image only, prints
   Office files to PDF and recalculates workbooks. Nothing here is AGPL.
2. **Every package is checked before any library parses it** (`docs/package.py`): at most 5,000 parts,
   256 MB expanded, a compression ratio of 200 per part, no macro project, no unsafe part path, and no
   relationship that a converter would fetch (a linked image, remote template, linked object, external
   workbook). Hyperlinks are allowed: they are followed only when a person clicks. PDFs must be
   unencrypted and lose JavaScript, launch/submit actions and embedded files on every save.
3. **Every write is deterministic.** Office packages are rewritten with sorted entries (`[Content_Types].xml`
   first), a fixed 1980 timestamp, one compression level, fixed core properties (created/modified
   2000-01-01, no last-modified-by, revision 1). PDFs are saved with no dates, no XMP, a fixed producer, and
   an ID derived from the content: the file's own `/ID` is dropped first, because a save keeps its first
   half and converters make that half random. Two fresh engines give the same bytes (tested per op).
4. **Text editing is run-aware** (`docs/text.py`). Word and PowerPoint split a paragraph into runs wherever
   formatting or an edit history changes, so `{{name}}` is often stored as `{{na` + `me}}`. Matches are found
   in the paragraph's whole text, the replacement goes into the run where the match starts (keeping its
   formatting) and the rest of the match is cut from the runs after it. `doc.fill` is strict by default:
   a placeholder without a value, or a value without a placeholder, fails and lists what the file has.
5. **Stamps are form XObjects with counted names.** qpdf's overlay helper invents a random XObject name on
   every call, which made the output differ run to run. A stamp is drawn by our own form XObject named
   `/AtelierStamp0`, `1`… with the page's rotation and crop origin handled in one matrix. Text is set in the
   bundled Inter, embedded as a TrueType subset of just the characters used (`docs/embed.py`: Type0 /
   CIDFontType2, no timestamps or name records, a subset tag derived from the characters, a ToUnicode map so
   the text stays selectable). A stamp is therefore print-safe: `pdf_print` accepts a converted, stamped file.
6. **Redaction removes, it does not cover.** A page with a match is rendered (pypdfium2), the match boxes
   are painted black, and the page is rebuilt as a picture; the original page and its text no longer exist in
   the file. Other pages are untouched. Titles, authors and bookmarks that repeat a redacted text are
   dropped. The op records what it found and the finished file's own text is searched afterwards; the
   `redaction_complete` check judges that record. The cost is that a redacted page is no longer text
   (a later `pdf.ocr` could restore a text layer; it is not built).
7. **LibreOffice is contained as far as a subprocess allows.** A fresh profile per run (macros disabled,
   links never refreshed), a private home, a font set that is only the bundled pack (system fonts differ
   between machines and so would the layout), a hard timeout that kills the process group, and `package.check`
   already refusing anything that points outside the file. Its output is normalized afterwards (dates, IDs),
   so a conversion is byte-identical across fresh runs in the reference image. It is a `PERCEPTUAL` op.
8. **`export` covers documents.** Presets are `export.pdf` (`pdf_screen`: pictures recompressed to 1600 px
   and fast web view; `pdf_print`: pictures untouched and every font must be embedded) and
   `export.docx|xlsx|pptx` (`*_clean`: normalized package, optional title). The result of an export is what the
   checks judge.
9. **Checks are pure functions of recorded metadata**: `no_placeholders`, `formula_errors`, `text_overflow` (an
   estimate from the bundled font's widths, a warning), `redaction_complete`, `pdf_valid` by default;
   `fonts_embedded` on request and in `pdf_print`.
10. **Names.** `pdf.pages` (keep, reorder or split out pages) is used instead of the roadmap's `pdf.split`,
    because one op makes one output: a split is one step per part. Op names for Office files are `doc.*`
    (fill, replace, table, image), `xlsx.*` (write, recalc) and `pptx.slide`.
11. **Previews** (`preview.pages`) render PDF pages with pypdfium2 into a labelled contact sheet; an Office
    file is printed to PDF first, so the preview shows it the way it prints. The agent's `preview` tool gains
    `page`.

## Consequences
- Form fields are not carried over by `pdf.merge`: fill and flatten first.
- Form fields (`pdf.form`) still use the standard Helvetica for their appearance, which is not embedded, so a
  filled, flattened form fails `fonts_embedded`. Stamps are fine. Characters Inter cannot draw (CJK) are refused
  with the characters named.
- There is no OCR (Tesseract is not in the image), no PDF/A conversion or validation (veraPDF), and Office
  files that are only edited (not printed) are not laid out, so `text_overflow` and page counts of Word
  files are estimates or unknown until converted.
- A workbook written by `xlsx.write` has no stored results until `xlsx.recalc` runs; the recalculated file
  is LibreOffice's rewrite of it, so run it last.
