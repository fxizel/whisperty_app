#!/usr/bin/env python3
"""Génère build/version_info.txt pour PyInstaller depuis whisperty.version."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from whisperty.version import __version__, version_tuple  # noqa: E402

vt = version_tuple()
out = ROOT / "build" / "version_info.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    f"""# UTF-8 — généré par scripts/gen_version_info.py (ne pas éditer à la main)
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vt},
    prodvers={vt},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040C04B0',
        [StringStruct('CompanyName', 'Softcom'),
        StringStruct('FileDescription', 'Whisperty — dictée vocale locale'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', 'whisperty'),
        StringStruct('LegalCopyright', 'Copyright © Softcom'),
        StringStruct('OriginalFilename', 'whisperty.exe'),
        StringStruct('ProductName', 'Whisperty'),
        StringStruct('ProductVersion', '{__version__}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
""",
    encoding="utf-8",
)
print(out)
