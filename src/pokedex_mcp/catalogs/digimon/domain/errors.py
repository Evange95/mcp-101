"""Domain errors raised by the Digimon core and its adapters."""


class DigimonError(Exception):
    """Base class for every Digimon domain error."""


class NotFound(DigimonError):
    """The requested resource does not exist in the Digimon catalog."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource = resource
        self.key = key
        super().__init__(f"{resource} '{key}' not found")


class DigimonUnavailable(DigimonError):
    """The Digimon data source could not answer; `retryable` tells if trying again may help."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Digimon service temporarily unavailable: {reason}")


class InvalidQuery(DigimonError):
    """The caller supplied an invalid query (blank name, out-of-range pagination...)."""
