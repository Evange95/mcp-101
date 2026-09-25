"""Domain errors raised by the Pokédex core and its adapters."""


class PokedexError(Exception):
    """Base class for every Pokédex domain error."""


class NotFound(PokedexError):
    """The requested resource does not exist in the Pokédex."""

    def __init__(self, resource: str, key: str) -> None:
        self.resource = resource
        self.key = key
        super().__init__(f"{resource} '{key}' not found")


class PokedexUnavailable(PokedexError):
    """The Pokédex data source could not answer; `retryable` tells if trying again may help."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Pokédex service temporarily unavailable: {reason}")


class InvalidQuery(PokedexError):
    """The caller supplied an invalid query (blank key, out-of-range pagination...)."""
