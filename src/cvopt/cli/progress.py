"""Optional terminal-only progress, written to the supplied stderr console."""

from rich.console import Console

from cvopt.observability import ProgressEvent


class TerminalProgress:
    def __init__(self, console: Console) -> None:
        self.console = console

    def __call__(self, event: ProgressEvent) -> None:
        if not self.console.is_terminal or event.status == "started":
            return
        label = event.stage or event.task or "cache"
        duration = f" {event.duration_s:.2f}s" if event.duration_s is not None else ""
        self.console.print(
            f"{event.operation}: {label} {event.status}{duration} "
            f"({event.llm_calls} calls, {event.application_cache_hits} cache hits)",
            highlight=False,
            markup=False,
        )
