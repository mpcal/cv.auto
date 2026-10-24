"""Errors raised by services; each interface maps them (the CLI to exit codes)."""


class CvoptError(Exception):
    exit_code = 4


class InputError(CvoptError):
    """Unreadable file, failed URL fetch, malformed review file, unknown run."""

    exit_code = 1


class NotFoundError(CvoptError):
    exit_code = 1


class ConflictError(CvoptError):
    """A refused change, e.g. deleting a position that has claims."""

    exit_code = 1


class LLMTaskError(CvoptError):
    """Schema validation or provider failure after retries."""

    exit_code = 2


class ValidationFailed(CvoptError):
    """Generation cannot satisfy the rules or the page budget."""

    exit_code = 3
