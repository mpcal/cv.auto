"""JdService (F3): read a JD, parse its requirements, create the run folder."""

import json
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ulid import ULID

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.jd import JobDescription
from cvopt.domain.run import JDSource, RunInfo
from cvopt.errors import InputError
from cvopt.llm.runner import TaskRunner, TaskRunnerFactory
from cvopt.llm.tasks.parse_jd import PARSE_JD, JDInput, to_job_description
from cvopt.llm.tasks.transcribe_document import TRANSCRIBE_DOCUMENT, TranscribeInput
from cvopt.ports import Clock, FileStore, TextExtractor, WebFetcher

ASK = "Download it as a PDF or paste the text instead (--pdf or --text)."


@dataclass(frozen=True)
class RunResult:
    name: str
    path: Path
    jd: JobDescription


def slug(text: str, max_len: int = 40) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:max_len].strip("-") or "x"


def dump(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


class JdService:
    def __init__(
        self,
        runners: TaskRunnerFactory,
        files: FileStore,
        fetcher: WebFetcher,
        extractor: TextExtractor,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._runners = runners
        self._files = files
        self._fetcher = fetcher
        self._extractor = extractor
        self._clock = clock
        self._settings = settings

    async def _pdf_text(self, runner: TaskRunner, path: Path) -> str:
        extracted = await self._extractor.extract(path)
        if not extracted.needs_transcription:
            return extracted.text
        inp = TranscribeInput(media_type=extracted.media_type, data=path.read_bytes())
        return (await runner.run(TRANSCRIBE_DOCUMENT, inp)).text

    async def create_run(
        self,
        user: UserContext,
        *,
        url: str | None = None,
        pdf: Path | None = None,
        text: Path | None = None,
    ) -> RunResult:
        """Exactly one source. Nothing is written unless the JD was read and parsed."""
        if sum(x is not None for x in (url, pdf, text)) != 1:
            raise InputError("Give exactly one of --url, --pdf or --text.")
        runner = self._runners.for_user(user)
        title_hint = ""
        if url is not None:
            page = await self._fetcher.fetch_main_text(url)
            source = JDSource(type="url", url=url, final_url=page.final_url)
            original = (
                "jd-source.pdf" if page.media_type == "application/pdf" else "jd-source.html",
                page.raw,
            )
            if page.media_type == "application/pdf":
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_pdf = Path(tmp) / "jd.pdf"
                    tmp_pdf.write_bytes(page.raw)
                    jd_text = await self._pdf_text(runner, tmp_pdf)
            else:
                jd_text, title_hint = page.text, page.title
        elif pdf is not None:
            jd_text = await self._pdf_text(runner, pdf)
            source = JDSource(type="pdf", filename=pdf.name)
            original = (f"jd-source{pdf.suffix.lower()}", pdf.read_bytes())
        else:
            assert text is not None
            raw = (await self._extractor.extract(text)).text
            jd_text, source = raw, JDSource(type="text", filename=text.name)
            original = (f"jd-source{text.suffix.lower() or '.txt'}", text.read_bytes())
        jd_text = jd_text.strip()
        if not jd_text:
            raise InputError(f"The job description is empty. {ASK}")

        out = await runner.run(PARSE_JD, JDInput(text=jd_text, title_hint=title_hint))
        if not out.is_job_description:
            raise InputError(f"This does not read as a job description. {ASK}")
        jd = to_job_description(out)

        now = self._clock.now()
        base = f"{now:%Y-%m-%dT%H%M}-{slug(jd.company)}-{slug(jd.title)}"
        info = RunInfo(
            run_id=str(ULID()),
            name=base,
            created_at=now,
            as_of=self._clock.today(),
            jd_source=source,
            language=jd.language,
            models={
                "strong": self._settings.llm.strong_model,
                "fast": self._settings.llm.fast_model,
            },
            prompt_versions={PARSE_JD.name: PARSE_JD.version},
            config=self._settings.model_dump(mode="json"),
        )
        path = self._new_dir(user, base)
        info = info.model_copy(update={"name": path.name})
        (path / "jd.txt").write_text(jd_text + "\n", encoding="utf-8")
        (path / original[0]).write_bytes(original[1])
        (path / "requirements.json").write_text(dump(jd.model_dump(mode="json")), encoding="utf-8")
        (path / "run.json").write_text(dump(info.model_dump(mode="json")), encoding="utf-8")
        return RunResult(path.name, path, jd)

    def _new_dir(self, user: UserContext, base: str) -> Path:
        for n in range(1, 100):
            name = base if n == 1 else f"{base}-{n}"
            try:
                return self._files.new_run_dir(user, name)
            except FileExistsError:
                continue
        raise InputError(f"Too many runs named {base}.")
