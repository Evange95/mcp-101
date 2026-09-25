from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexError, PokedexUnavailable


def test_not_found_names_resource_and_key():
    error = NotFound("Pokémon", "missingno")

    assert str(error) == "Pokémon 'missingno' not found"
    assert (error.resource, error.key) == ("Pokémon", "missingno")
    assert isinstance(error, PokedexError)


def test_pokedex_unavailable_is_retryable_by_default():
    error = PokedexUnavailable("timeout")

    assert error.retryable is True
    assert error.reason == "timeout"
    assert str(error) == "Pokédex service temporarily unavailable: timeout"
    assert isinstance(error, PokedexError)


def test_pokedex_unavailable_can_be_marked_non_retryable():
    assert PokedexUnavailable("bad payload", retryable=False).retryable is False


def test_invalid_query_is_a_pokedex_error():
    error = InvalidQuery("limit must be between 1 and 100")

    assert str(error) == "limit must be between 1 and 100"
    assert isinstance(error, PokedexError)
