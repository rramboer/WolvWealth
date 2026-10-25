"""Errors the domain raises. They carry no HTTP or UI knowledge.

The web layer maps them to JSON status codes or flashed messages; the CLI
prints them. ``details`` is optional structured context (e.g. the list of
unknown tickers) for callers that want more than a sentence.
"""


class DomainError(Exception):
    """Base class: something about the request or the data was not acceptable."""

    def __init__(self, message: str, details: dict = None) -> None:
        """Store a human-readable message and optional structured details."""
        super().__init__(message)
        self.message = str(message)
        self.details = details or {}


class ValidationError(DomainError):
    """The input was malformed or violated a rule."""


class NotFoundError(DomainError):
    """A referenced workspace or portfolio does not exist."""
