"""Recorded upstream payloads (trimmed) used by adapter tests: PokeAPI and digi-api."""

import json
from pathlib import Path
from typing import Any, Literal

_DATA_DIR = Path(__file__).parent


def load_json(name: str, source: Literal["pokeapi", "digiapi"] = "pokeapi") -> Any:
    return json.loads((_DATA_DIR / source / name).read_text(encoding="utf-8"))
