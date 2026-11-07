from pathlib import Path

import docx
import pytest

from cvopt.adapters.extractors import LocalTextExtractor
from cvopt.adapters.language import LinguaDetector
from cvopt.config import DocumentSettings
from cvopt.errors import InputError
from tests.scenarios import CV, LETTER

EXTRACT = LocalTextExtractor(DocumentSettings())


async def test_pdf_text_layer() -> None:
    out = await EXTRACT.extract(CV)
    assert out.media_type == "application/pdf" and not out.needs_transcription
    assert "Nordlicht Analytics AG" in out.text and out.pages == 1


async def test_short_pdf_needs_transcription() -> None:
    out = await EXTRACT.extract(Path(__file__).parents[1] / "golden" / "note.pdf")
    assert out.needs_transcription and out.text == ""


async def test_docx_paragraphs_and_tables(tmp_path: Path) -> None:
    d = docx.Document()
    d.add_paragraph("First paragraph")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Python", "5 years"
    d.add_paragraph("After the table")
    path = tmp_path / "cv.docx"
    d.save(str(path))
    out = await EXTRACT.extract(path)
    assert out.text.splitlines() == ["First paragraph", "Python | 5 years", "After the table"]


async def test_text_images_and_refusals(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes("Zürich".encode("cp1252"))
    assert (await EXTRACT.extract(tmp_path / "a.txt")).text == "Zürich"
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    assert (await EXTRACT.extract(tmp_path / "a.png")).needs_transcription
    (tmp_path / "a.xls").write_bytes(b"x")
    with pytest.raises(InputError):
        await EXTRACT.extract(tmp_path / "a.xls")
    (tmp_path / "fake.pdf").write_bytes(b"not a pdf")
    with pytest.raises(InputError):
        await EXTRACT.extract(tmp_path / "fake.pdf")
    with pytest.raises(InputError):
        await LocalTextExtractor(DocumentSettings(max_mb=0.00001)).extract(CV)
    with pytest.raises(InputError):
        await EXTRACT.extract(tmp_path / "missing.pdf")


async def test_language_detection() -> None:
    detector = LinguaDetector()
    assert detector.detect((await EXTRACT.extract(CV)).text) == "en"
    assert detector.detect((await EXTRACT.extract(LETTER)).text) == "de"
    assert detector.detect("") == "und"


@pytest.mark.parametrize("blank", [False, True])
async def test_mixed_pdf_checks_each_page_and_ignores_truly_blank_pages(
    tmp_path: Path, blank: bool
) -> None:
    import io

    import typst
    from pypdf import PdfReader, PdfWriter

    text = typst.compile(b"#set page(width: 10cm, height: 10cm)\n" + b"Extracted text " * 30)
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(text)).pages[0])
    if blank:
        writer.add_blank_page(width=100, height=100)
    else:
        import struct
        import zlib

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data))
            )

        image = tmp_path / "scan.png"
        image.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
            + chunk(b"IEND", b"")
        )
        scanned = typst.compile(b'#image("scan.png")', root=str(tmp_path))
        writer.add_page(PdfReader(io.BytesIO(scanned)).pages[0])
    path = tmp_path / "mixed.pdf"
    writer.write(path)
    out = await EXTRACT.extract(path)
    assert out.needs_transcription is (not blank)
    if not blank:
        assert "Extracted text" not in out.text
