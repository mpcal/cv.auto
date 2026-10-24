"""Live smoke test: one real call returns a parsed structured output."""

from pathlib import Path

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.llm.tasks.transcribe_document import (
    TRANSCRIBE_DOCUMENT,
    TranscribeInput,
    TranscribeOutput,
)

pytestmark = pytest.mark.live

NOTE = Path(__file__).parents[1] / "golden" / "note.pdf"


async def test_transcribe_returns_parsed_output(golden_live: Container, alice: UserContext) -> None:
    inp = TranscribeInput(media_type="application/pdf", data=NOTE.read_bytes())
    out = await golden_live.runners.for_user(alice).run(TRANSCRIBE_DOCUMENT, inp)
    assert isinstance(out, TranscribeOutput)
    assert "Alice Example" in out.text and "2019" in out.text
