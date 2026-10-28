"""TypstRenderer: compiles the render model with the bundled template and fonts."""

import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path

import typst
from pypdf import PdfReader

from cvopt.domain.render import RenderModel
from cvopt.ports import RenderResult

RESOURCES = files("cvopt.resources")


class TypstRenderer:
    def render(self, model: RenderModel, out_path: Path) -> RenderResult:
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            as_file(
                RESOURCES.joinpath(
                    "templates", "classic.typ" if model.template == "classic" else "cv.typ"
                )
            ) as template,
            as_file(RESOURCES.joinpath("fonts")) as fonts,
        ):
            tmp = Path(tmp_dir)
            shutil.copy(template, tmp / "cv.typ")
            with as_file(RESOURCES.joinpath("templates", "atsthetic.typ")) as theme:
                shutil.copy(theme, tmp / "atsthetic.typ")
            (tmp / "model.json").write_text(model.model_dump_json(), encoding="utf-8")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            typst.compile(
                str(tmp / "cv.typ"),
                output=str(out_path),
                root=str(tmp),
                font_paths=[str(fonts)],
                ignore_system_fonts=True,
                sys_inputs={"model": "model.json"},
            )
        return RenderResult(path=out_path, pages=len(PdfReader(out_path).pages))
