"""Measure chars_per_line and lines_per_page for the [layout] settings.

Renders clean CVs filled with highlights of known length using the real template and
fonts, then reads the line breaks back with pypdf.

    uv run python scripts/calibrate_layout.py
"""

import statistics
import tempfile
from pathlib import Path

from pypdf import PdfReader

from cvopt.adapters.typst_renderer import TypstRenderer
from cvopt.config import load_settings
from cvopt.domain.cv import JSONResume
from cvopt.services.render import build_render_model

SAMPLE = (
    "Led the migration of the analytics platform to a managed cloud warehouse, rebuilt the "
    "nightly pipelines in Python and SQL, introduced automated data quality checks, and "
    "trained six analysts in modern forecasting methods for retail demand planning. "
)


def cv_with(highlights: list[str]) -> JSONResume:
    return JSONResume.model_validate(
        {
            "basics": {"name": "Calibration"},
            "work": [
                {
                    "name": "Employer",
                    "position": "Title",
                    "startDate": "2020-01",
                    "highlights": highlights,
                }
            ],
        }
    )


def render_lines(highlights: list[str], out: Path) -> tuple[list[str], int]:
    settings = load_settings()
    res = TypstRenderer().render(build_render_model(cv_with(highlights), settings), out)
    reader = PdfReader(res.path)
    return (reader.pages[0].extract_text() or "").splitlines(), res.pages


def chars_per_line(tmp: Path) -> int:
    lines, _ = render_lines([SAMPLE * 12], tmp / "wide.pdf")
    body = [ln.strip("• ").strip() for ln in lines if ln.strip()]
    start = next(i for i, ln in enumerate(body) if ln.startswith("Led the migration"))
    full = body[start:-1]  # wrapped lines of the long highlight, without the last partial one
    return int(statistics.median(len(ln) for ln in full[:-1]))


def lines_per_page(tmp: Path) -> int:
    n = 120
    lines, _ = render_lines([f"Item {i:03d}" for i in range(n)], tmp / "tall.pdf")
    on_first = [ln for ln in lines if ln.strip("• ").startswith("Item ")]
    return len(on_first)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        cpl, lpp = chars_per_line(tmp), lines_per_page(tmp)
    print("[layout]")
    print(f"chars_per_line = {cpl}")
    print(f"lines_per_page = {lpp}")


if __name__ == "__main__":
    main()
