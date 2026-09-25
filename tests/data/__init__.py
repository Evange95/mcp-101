"""Recorded PokeAPI payloads (trimmed) used by adapter tests."""

import json
from pathlib import Path
from typing import Any

_POKEAPI_DIR = Path(__file__).parent / "pokeapi"


def load_json(name: str) -> Any:
    return json.loads((_POKEAPI_DIR / name).read_text(encoding="utf-8"))
