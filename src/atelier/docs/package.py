"""Office packages (DOCX, XLSX, PPTX): safe to open, deterministic to write.

An Office file is a zip of XML parts. Reading one is a security boundary (docs/10-security.md):
a small file can expand to gigabytes, carry a macro project, or point at a remote template or
image that a converter would fetch. `check` refuses those before any library parses the file, and
`write` rewrites a package with sorted entries, fixed timestamps and normalized properties so
that the same content always gives the same bytes.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable
from pathlib import Path

from lxml import etree

from atelier.ops.registry import OpError

MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 256 * 1024 * 1024  # bytes, all parts together
MAX_RATIO = 200  # uncompressed / compressed, per part
FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # the zip format's earliest date
FIXED_STAMP = "2000-01-01T00:00:00Z"  # what created/modified become in the core properties
COMPRESS_LEVEL = 6

_MACRO_PARTS = re.compile(r"(^|/)(vbaProject\.bin|vbaData\.xml|macrosheets/)", re.I)
# Relationship types a converter would go and fetch when their target is outside the package.
# Hyperlinks are left alone: they are only followed when a person clicks them.
_FETCHED = (
    "attachedTemplate|oleObject|externalLink|externalLinkPath|frame|image|subDocument|"
    "audio|video|media"
)
_EXTERNAL = re.compile(
    rf'Type="[^"]*/(?:{_FETCHED})"[^>]*TargetMode="External"'
    rf'|TargetMode="External"[^>]*Type="[^"]*/(?:{_FETCHED})"'
)
_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dcterms": "http://purl.org/dc/terms/",
}
Drop = Callable[[str], bool]


def check(path: Path) -> None:
    """Refuse a package that is not a plain Office file. Raises OpError with what to change."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                raise OpError(f"the file has {len(infos)} parts (limit {MAX_ENTRIES})")
            total = sum(i.file_size for i in infos)
            if total > MAX_UNCOMPRESSED:
                raise OpError(
                    f"the file expands to {total // 2**20} MB "
                    f"(limit {MAX_UNCOMPRESSED // 2**20} MB)"
                )
            for info in infos:
                if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                    raise OpError(f"part {info.filename!r} is compressed suspiciously well")
                if _MACRO_PARTS.search(info.filename):
                    raise OpError(
                        "the file contains a macro project; macros are never run or kept here. "
                        "Save it as a macro-free file (.docx, .xlsx, .pptx) and upload that"
                    )
                if info.filename.startswith(("/", "..")) or ".." in info.filename.split("/"):
                    raise OpError(f"part {info.filename!r} has an unsafe path")
                if info.filename.endswith(".rels"):
                    text = z.read(info).decode("utf-8", errors="replace")
                    if _EXTERNAL.search(text):
                        raise OpError(
                            f"{info.filename} links to content outside the file (a remote "
                            "template, linked object or workbook); remove the link and upload again"
                        )
    except zipfile.BadZipFile:
        raise OpError("not a valid Office file (the zip container is damaged)") from None


def _normalize_core(data: bytes, title: str | None = None) -> bytes:
    """Fixed created/modified, no last-modified-by, revision 1; optionally a new title."""
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return data
    for tag in ("created", "modified"):
        for el in root.iterfind(f"dcterms:{tag}", _NS):
            el.text = FIXED_STAMP
    for el in root.iterfind("cp:lastModifiedBy", _NS):
        el.text = ""
    for el in root.iterfind("cp:revision", _NS):
        el.text = "1"
    if title is not None:
        dc = "http://purl.org/dc/elements/1.1/"
        node = root.find(f"{{{dc}}}title")
        if node is None:
            node = etree.SubElement(root, f"{{{dc}}}title")
        node.text = title
    out: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return out


def write(src: Path, dst: Path, *, drop: Drop | None = None, title: str | None = None) -> None:
    """Rewrite the package at `src` to `dst` deterministically.

    Entries are sorted (`[Content_Types].xml` first), get a fixed timestamp, one compression
    level and no extra fields; the core properties get fixed dates. `drop` removes parts by name.
    """
    with zipfile.ZipFile(src) as zin:
        names = sorted(
            (n for n in zin.namelist() if not n.endswith("/") and not (drop and drop(n))),
            key=lambda n: (n != "[Content_Types].xml", n),
        )
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL) as out:
            for name in names:
                data = zin.read(name)
                if name == "docProps/core.xml":
                    data = _normalize_core(data, title)
                info = zipfile.ZipInfo(name, FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                out.writestr(info, data, compresslevel=COMPRESS_LEVEL)


def parts(path: Path) -> dict[str, bytes]:
    """Every part of a checked package (for probes that read the raw XML)."""
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if not n.endswith("/")}
