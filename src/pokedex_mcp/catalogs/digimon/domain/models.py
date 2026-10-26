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
    """Optional upstream list filters. Values are already normalized; `None` means "any".

    No name: digi-api's name filter only matches the start of a name, so name search is
    done by the service on the name index instead.
    """

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

    @property
    def has_more(self) -> bool:
        return (self.page + 1) * self.page_size < self.total


# digi-api's vocabularies (Japanese level names), lowercase like normalized keys.
LEVELS = (
    "baby i",
    "baby ii",
    "child",
    "adult",
    "perfect",
    "ultimate",
    "armor",
    "hybrid",
    "unknown",
)
ATTRIBUTES = ("vaccine", "data", "virus", "free", "variable", "no data", "unknown")
# English dub level names that cannot be mistaken for a Japanese one. "Ultimate" is
# missing on purpose: in the dub it means Perfect, in Japanese it is the level above.
DUB_LEVELS = {
    "fresh": "baby i",
    "in-training": "baby ii",
    "in training": "baby ii",
    "rookie": "child",
    "champion": "adult",
    "mega": "ultimate",
}
