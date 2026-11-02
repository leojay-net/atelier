"""LibreOffice, run headless to turn one document into another (docs/05 §5.5, docs/10).

LibreOffice is a large program reading untrusted files, so it is contained as far as a plain
subprocess allows: a fresh profile per run (no macros, no link updates), a private home directory,
a font set that is only the bundled pack (system fonts differ between machines, and so would the
layout), a hard timeout with the whole process group killed, and no input that points outside its
own package (`package.check` refuses those before we get here). The result is normalized
afterwards, so what LibreOffice stamps into a file (dates, ids) never reaches the output.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from atelier.ops.registry import OpError
from atelier.paths import FONTS

TIMEOUT = 150.0
_SETTINGS = (  # (configuration path, property, value): macros off, links never refreshed
    ("Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("Writer/Content/Update", "Link", "2"),
    ("Calc/Content/Update", "Link", "2"),
)


def _registry() -> str:
    items = "".join(
        f'<item oor:path="/org.openoffice.Office.{path}"><prop oor:name="{name}" oor:op="fuse">'
        f"<value>{value}</value></prop></item>\n"
        for path, name, value in _SETTINGS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n' + items + "</oor:items>\n"
    )


def soffice() -> str:
    path = os.environ.get("ATELIER_SOFFICE") or shutil.which("soffice")
    if not path or not Path(path).is_file():
        raise OpError(
            "LibreOffice is not installed here, so this document cannot be converted or "
            "recalculated (it is part of the reference image; or set ATELIER_SOFFICE)"
        )
    return path


def convert(src: Path, ext: str, target: str, work: Path) -> Path:
    """Convert `src` (a file whose type is `ext`) to `target` (pdf, xlsx…); returns the result."""
    binary = soffice()
    box = work / "lo"
    (box / "in").mkdir(parents=True)
    (box / "out").mkdir()
    (box / "profile" / "user").mkdir(parents=True)
    (box / "profile" / "user" / "registrymodifications.xcu").write_text(
        _registry(), encoding="utf-8"
    )
    (box / "fonts.conf").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f"<dir>{FONTS}</dir><cachedir>{box / 'fontcache'}</cachedir></fontconfig>",
        encoding="utf-8",
    )
    source = box / "in" / f"document{ext}"
    shutil.copyfile(src, source)
    env = {
        "HOME": str(box),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "FONTCONFIG_FILE": str(box / "fonts.conf"),
        "SAL_USE_VCLPLUGIN": "svp",
    }
    argv = [
        binary, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
        "--nofirststartwizard", f"-env:UserInstallation=file://{box / 'profile'}",
        "--convert-to", target, "--outdir", str(box / "out"), str(source),
    ]  # fmt: skip
    proc = subprocess.Popen(
        argv, env=env, cwd=box, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
    )  # fmt: skip
    try:
        output, _ = proc.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise OpError(f"LibreOffice took longer than {TIMEOUT:.0f}s on this file") from None
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    made = next((box / "out").glob("document.*"), None)
    if proc.returncode != 0 or made is None:
        tail = output.decode(errors="replace").strip().splitlines()[-1:] or ["no message"]
        raise OpError(f"LibreOffice could not convert the file: {tail[0][:200]}")
    return made
