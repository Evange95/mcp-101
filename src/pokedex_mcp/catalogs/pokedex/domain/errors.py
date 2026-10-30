"""Domain errors raised by the Pokédex core and its adapters."""


class PokedexError(Exception):
    """Base class for every Pokédex domain error."""


class NotFound(PokedexError):
    """The requested resource does not exist in the Pokédex.

    `suggestions` are existing names close to `key`, so the caller can correct itself.
    """

    def __init__(self, resource: str, key: str, suggestions: tuple[str, ...] = ()) -> None:
        self.resource = resource
        self.key = key
        self.suggestions = suggestions
        message = f"{resource} '{key}' not found"
        if suggestions:
            message += ". Did you mean: " + ", ".join(f"'{name}'" for name in suggestions) + "?"
        super().__init__(message)


class PokedexUnavailable(PokedexError):
    """The Pokédex data source could not answer; `retryable` tells if trying again may help."""

    def __init__(
        self, reason: str, *, retryable: bool = True, retry_after: float | None = None
    ) -> None:
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after  # seconds the source asked us to wait, if it said
        super().__init__(f"Pokédex data source unavailable: {reason}")


class AccessDenied(PokedexError):
    """The caller may not see this resource (e.g. a legendary Pokémon without the
    `pokedex:legendary` permission). Not a fault: an expected, final answer."""

    def __init__(self, resource: str, key: str, reason: str) -> None:
        self.resource = resource
        self.key = key
        self.reason = reason
        super().__init__(f"{resource} '{key}' is restricted: {reason}")


class InvalidQuery(PokedexError):
    """The caller supplied an invalid query (blank key, out-of-range pagination...)."""
