"""transcribe_document: OCR for scanned PDFs and images."""

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.ports import FilePart, TextPart


class TranscribeInput(Model):
    media_type: str
    data: bytes


class TranscribeOutput(Model):
    text: str


def render(inp: TranscribeInput) -> list[TextPart | FilePart]:
    return [
        FilePart(inp.media_type, inp.data),
        TextPart("Transcribe the attached document verbatim, in reading order."),
    ]


TRANSCRIBE_DOCUMENT = Task(
    name="transcribe_document",
    version="1",
    tier="fast",
    input_model=TranscribeInput,
    output_model=TranscribeOutput,
    max_tokens=8000,
    render=render,
)
