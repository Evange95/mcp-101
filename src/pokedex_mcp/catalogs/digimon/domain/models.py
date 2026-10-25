"""Digimon domain entities: immutable, framework-free value objects."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    translation: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class Digivolution:
    """One edge of the digivolution graph: a Digimon and what it takes to get there."""

    id: int
    name: str
    condition: str | None


@dataclass(frozen=True, slots=True)
class Digimon:
    id: int
    name: str
    levels: tuple[str, ...]
    types: tuple[str, ...]
    attributes: tuple[str, ...]
    fields: tuple[str, ...]
    x_antibody: bool
    release_year: str | None
    description: str | None
    skills: tuple[Skill, ...]
    image_url: str | None
    # A graph, not a tree: a Digimon digivolves from and into many others.
    prior_evolutions: tuple[Digivolution, ...]
    next_evolutions: tuple[Digivolution, ...]


@dataclass(frozen=True, slots=True)
class DigimonFilter:
    """Optional list filters. Values are already normalized; `None` means "any"."""

    name: str | None = None
    level: str | None = None
    attribute: str | None = None


@dataclass(frozen=True, slots=True)
class DigimonSummary:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class DigimonPage:
    total: int
    page: int
    page_size: int
    items: tuple[DigimonSummary, ...]
