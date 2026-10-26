"""Domain errors raised by the Digimon core and its adapters."""


class DigimonError(Exception):
    """Base class for every Digimon domain error."""


class NotFound(DigimonError):
    """The requested resource does not exist in the Digimon catalog."""

    def __init__(self, resource: str, key: str, suggestions: tuple[str, ...] = ()) -> None:
        self.resource = resource
        self.key = key
        self.suggestions = suggestions
        message = f"{resource} '{key}' not found"
        if suggestions:
            message += ". Did you mean: " + ", ".join(f"'{name}'" for name in suggestions) + "?"
        super().__init__(message)


class DigimonUnavailable(DigimonError):
    """The Digimon data source could not answer; `retryable` tells if trying again may help."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Digimon data source unavailable: {reason}")


class InvalidQuery(DigimonError):
    """The caller supplied an invalid query (blank name, out-of-range pagination...)."""
