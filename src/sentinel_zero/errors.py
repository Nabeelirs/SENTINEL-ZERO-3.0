"""Predictable failures without exposing remote bodies or request secrets."""


class MetadataError(ValueError):
    """A catalog record or contract is invalid or unsupported."""


class DiscoveryError(Exception):
    """A bounded request failed. Message is safe for public audit output."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
