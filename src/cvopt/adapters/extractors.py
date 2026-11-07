"""Local text extraction: PDF (pypdf), DOCX (python-docx), images, plain text."""

import asyncio
import io
from pathlib import Path

import docx
import pypdf
from pypdf.errors import PdfReadError

from cvopt.config import DocumentSettings
from cvopt.errors import InputError
from cvopt.ports import ExtractedText

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class LocalTextExtractor:
    def __init__(self, s: DocumentSettings) -> None:
        self._s = s

    async def extract(self, path: Path) -> ExtractedText:
        return await asyncio.to_thread(self._extract, path)

    def _extract(self, path: Path) -> ExtractedText:
        try:
            data = path.read_bytes()
        except OSError as err:
            raise InputError(f"Cannot read {path}: {err.strerror}") from err
        if len(data) > self._s.max_mb * 1024 * 1024:
            raise InputError(f"{path.name} is larger than {self._s.max_mb} MB.")
        ext = path.suffix.lower()
        if ext == ".pdf" and data.startswith(b"%PDF"):
            return self._pdf(path, data)
        if ext == ".docx" and data.startswith(b"PK"):
            return self._docx(path, data)
        if ext in IMAGE_TYPES:
            return ExtractedText("", IMAGE_TYPES[ext], 1, needs_transcription=True)
        if ext in (".txt", ".md"):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("cp1252", errors="replace")
            return ExtractedText(text, "text/plain", 1, needs_transcription=False)
        raise InputError(f"Unsupported file type: {path.name}")

    def _pdf(self, path: Path, data: bytes) -> ExtractedText:
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages = len(reader.pages)
            if pages > self._s.max_pdf_pages:
                raise InputError(
                    f"{path.name} has {pages} pages; the limit is {self._s.max_pdf_pages}."
                )
            parts = []
            scanned = False
            all_urls = []
            for page in reader.pages:
                body = page.extract_text() or ""
                visible = len(body.strip())
                # A blank page needs no OCR. An image-only or short-text page does,
                # even when another page has an excellent text layer.
                if (visible or len(page.images)) and visible < self._s.min_text_chars:
                    scanned = True
                # Text extraction omits link targets. Keep them available to the
                # structured extractor without treating them as visible page text.
                urls = []
                for ref in page.get("/Annots", []):
                    action = ref.get_object().get("/A")
                    if action and (url := action.get_object().get("/URI")) and str(url) not in urls:
                        urls.append(str(url))
                if urls:
                    body += "\nLink targets on this page:\n" + "\n".join(urls)
                    all_urls.extend(urls)
                parts.append(body)
            text = "\f".join(parts)
        except PdfReadError as err:
            raise InputError(f"Cannot read PDF {path.name}: {err}") from err
        if scanned:
            text = "\nLink targets:\n" + "\n".join(all_urls) if all_urls else ""
        return ExtractedText(text, "application/pdf", pages, scanned)

    def _docx(self, path: Path, data: bytes) -> ExtractedText:
        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as err:  # python-docx raises several unrelated types
            raise InputError(f"Cannot read DOCX {path.name}: {err}") from err
        parts: list[str] = []
        for block in document.iter_inner_content():  # paragraphs and tables in order
            if isinstance(block, docx.table.Table):
                parts += [" | ".join(c.text for c in row.cells) for row in block.rows]
            else:
                parts.append(block.text)
        urls = [
            rel.target_ref
            for rel in document.part.rels.values()
            if rel.is_external and rel.reltype.endswith("/hyperlink")
        ]
        if urls:
            parts.append("Link targets:\n" + "\n".join(dict.fromkeys(urls)))
        return ExtractedText("\n".join(parts), DOCX_TYPE, 1, needs_transcription=False)
