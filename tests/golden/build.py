"""Rebuild the golden PDFs from tests/golden/src/*.typ."""

from pathlib import Path

import typst

HERE = Path(__file__).parent

for src in sorted((HERE / "src").glob("*.typ")):
    out = HERE / f"{src.stem}.pdf"
    typst.compile(str(src), output=str(out))
    print(f"built {out.name}")
