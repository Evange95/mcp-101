"""Structured output models returned by the Digimon MCP tools (presentation of domain entities)."""

from typing import Self

from pydantic import BaseModel

from pokedex_mcp.catalogs.digimon.domain.models import (
    Digimon,
    DigimonPage,
    Digivolution,
    Skill,
)


class SkillOut(BaseModel):
    name: str
    translation: str | None
    description: str | None

    @classmethod
    def from_domain(cls, skill: Skill) -> Self:
        return cls(name=skill.name, translation=skill.translation, description=skill.description)


class DigimonOut(BaseModel):
    id: int
    name: str
    levels: list[str]
    types: list[str]
    attributes: list[str]
    fields: list[str]
    x_antibody: bool
    release_year: str | None
    description: str | None
    skills: list[SkillOut]
    image_url: str | None
    # Counts only: the lists can be long (Agumon has 80+), `digimon_get_digivolutions` has them.
    digivolves_from_count: int
    digivolves_to_count: int

    @classmethod
    def from_domain(cls, digimon: Digimon) -> Self:
        return cls(
            id=digimon.id,
            name=digimon.name,
            levels=list(digimon.levels),
            types=list(digimon.types),
            attributes=list(digimon.attributes),
            fields=list(digimon.fields),
            x_antibody=digimon.x_antibody,
            release_year=digimon.release_year,
            description=digimon.description,
            skills=[SkillOut.from_domain(skill) for skill in digimon.skills],
            image_url=digimon.image_url,
            digivolves_from_count=len(digimon.prior_evolutions),
            digivolves_to_count=len(digimon.next_evolutions),
        )


class DigivolutionOut(BaseModel):
    id: int
    name: str
    condition: str | None

    @classmethod
    def from_domain(cls, digivolution: Digivolution) -> Self:
        return cls(id=digivolution.id, name=digivolution.name, condition=digivolution.condition)


class DigivolutionsOut(BaseModel):
    id: int
    name: str
    levels: list[str]
    digivolves_from: list[DigivolutionOut]
    digivolves_to: list[DigivolutionOut]
    # Each list holds at most `limit` entries; the totals say how many there are.
    digivolves_from_total: int
    digivolves_to_total: int
    truncated: bool

    @classmethod
    def from_domain(cls, digimon: Digimon, *, limit: int) -> Self:
        prior, after = digimon.prior_evolutions, digimon.next_evolutions
        return cls(
            id=digimon.id,
            name=digimon.name,
            levels=list(digimon.levels),
            digivolves_from=[DigivolutionOut.from_domain(d) for d in prior[:limit]],
            digivolves_to=[DigivolutionOut.from_domain(d) for d in after[:limit]],
            digivolves_from_total=len(prior),
            digivolves_to_total=len(after),
            truncated=len(prior) > limit or len(after) > limit,
        )


class DigimonSummaryOut(BaseModel):
    id: int
    name: str


class DigimonPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    has_more: bool  # ask for page + 1 to continue
    items: list[DigimonSummaryOut]

    @classmethod
    def from_domain(cls, page: DigimonPage) -> Self:
        return cls(
            total=page.total,
            page=page.page,
            page_size=page.page_size,
            has_more=page.has_more,
            items=[DigimonSummaryOut(id=item.id, name=item.name) for item in page.items],
        )
