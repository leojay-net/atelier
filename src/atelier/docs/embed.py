"""Embedding a bundled font in a PDF, cut down to the characters actually used.

Text drawn with a standard font (Helvetica) is not part of the file, so a viewer or printer
substitutes whatever it has, and print and archive workflows refuse the file. Stamps therefore use
the bundled Inter, embedded as a TrueType subset in a Type0/CIDFontType2 font with a ToUnicode map
(so the text stays selectable and searchable). Subsetting and saving are deterministic: no
timestamps, no name records, glyphs renumbered in a fixed order, a subset tag derived from the
characters.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import pikepdf
from fontTools import subset
from fontTools.ttLib import TTFont

from atelier import fonts
from atelier.ops.registry import OpError


@dataclass(frozen=True)
class Embedded:
    font: pikepdf.Object  # the Type0 font dictionary (an indirect object of the target PDF)
    gids: dict[str, int]  # character → glyph id in the subset
    advances: dict[str, float]  # character → advance width in 1/1000 em

    def encode(self, text: str) -> bytes:
        """The text as the hex string a `Tj` operator takes (two bytes per glyph)."""
        return b"<" + "".join(f"{self.gids[c]:04X}" for c in text).encode() + b">"

    def width(self, text: str, size: float) -> float:
        return sum(self.advances[c] for c in text) * size / 1000


def _cmap_stream(gids: dict[str, int]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def /CMapType 2 def",
        "1 begincodespacerange <0000> <FFFF> endcodespacerange",
        f"{len(gids)} beginbfchar",
    ]
    for char, gid in sorted(gids.items(), key=lambda kv: kv[1]):
        lines.append(f"<{gid:04X}> <{char.encode('utf-16-be').hex().upper()}>")
    lines += ["endbfchar", "endcmap CMapName currentdict /CMap defineresource pop end end"]
    return ("\n".join(lines) + "\n").encode("ascii")


def embed(
    pdf: pikepdf.Pdf, text: str, family: str = fonts.DEFAULT_FAMILY, weight: str = "regular"
) -> Embedded:
    """Add a subset of the bundled font that can draw every character of `text` to `pdf`."""
    chars = sorted(set(text) | {" "})
    source = TTFont(str(fonts.font_path(family, weight)), recalcTimestamp=False)
    cmap = source.getBestCmap()
    missing = [c for c in chars if ord(c) not in cmap]
    if missing:
        raise OpError(
            f"the font {family} cannot draw {''.join(missing)!r}: use letters, digits and "
            "ordinary punctuation"
        )
    options = subset.Options()
    options.layout_features = []
    options.name_IDs = []
    options.notdef_outline = True
    options.hinting = False
    options.glyph_names = False
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "kern", "STAT", "meta"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=[ord(c) for c in chars])
    subsetter.subset(source)
    upem = float(source["head"].unitsPerEm)
    small = source.getBestCmap()
    gids = {c: source.getGlyphID(small[ord(c)]) for c in chars}
    advances = {c: source["hmtx"][small[ord(c)]][0] * 1000 / upem for c in chars}
    buf = io.BytesIO()
    source.save(buf)
    data = buf.getvalue()

    digest = hashlib.sha256("".join(chars).encode()).digest()
    tag = "".join(chr(65 + b % 26) for b in digest[:6])
    name = pikepdf.Name(f"/{tag}+{family}-{weight.capitalize()}")
    head, os2, hhea = source["head"], source["OS/2"], source["hhea"]
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=name,
            Flags=4,
            FontBBox=[round(v * 1000 / upem) for v in (head.xMin, head.yMin, head.xMax, head.yMax)],
            ItalicAngle=0,
            Ascent=round(hhea.ascent * 1000 / upem),
            Descent=round(hhea.descent * 1000 / upem),
            CapHeight=round(getattr(os2, "sCapHeight", 0) * 1000 / upem) or 700,
            StemV=80,
            FontFile2=pdf.make_stream(data, Length1=len(data)),
        )
    )
    widths = pikepdf.Array()
    for c in sorted(chars, key=lambda x: gids[x]):
        widths.append(gids[c])
        widths.append(pikepdf.Array([round(advances[c])]))
    cid = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            BaseFont=name,
            CIDSystemInfo=pikepdf.Dictionary(
                Registry=pikepdf.String("Adobe"),
                Ordering=pikepdf.String("Identity"),
                Supplement=0,
            ),
            FontDescriptor=descriptor,
            DW=1000,
            W=widths,
            CIDToGIDMap=pikepdf.Name.Identity,
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=name,
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([cid]),
            ToUnicode=pdf.make_stream(_cmap_stream(gids)),
        )
    )
    return Embedded(font, gids, advances)
