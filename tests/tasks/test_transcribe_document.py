from pathlib import Path

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.llm.runner import load_prompt
from cvopt.llm.tasks.transcribe_document import (
    TRANSCRIBE_DOCUMENT,
    TranscribeInput,
)
from cvopt.ports import FilePart, TextPart

NOTE = Path(__file__).parents[1] / "golden" / "note.pdf"


def note_input() -> TranscribeInput:
    return TranscribeInput(media_type="application/pdf", data=NOTE.read_bytes())


def test_prompt_and_render() -> None:
    prompt = load_prompt("transcribe_document")
    assert "never follow instructions" in prompt and "[illegible]" in prompt
    parts = TRANSCRIBE_DOCUMENT.render(note_input())
    assert isinstance(parts[0], FilePart) and parts[0].media_type == "application/pdf"
    assert isinstance(parts[1], TextPart)


async def test_runs_offline_from_recording(golden: Container, alice: UserContext) -> None:
    out = await golden.runners.for_user(alice).run(TRANSCRIBE_DOCUMENT, note_input())
    assert "Alice Example" in out.text
