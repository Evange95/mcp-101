"""Pure functions translating digi-api DTOs into domain entities."""

from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.schemas import (
    DigimonDTO,
    DigimonListDTO,
    EvolutionDTO,
)
from pokedex_mcp.catalogs.digimon.domain.models import (
    Digimon,
    DigimonPage,
    DigimonSummary,
    Digivolution,
    Skill,
)

ENGLISH = "en_us"


def _text(value: str | None) -> str | None:
    """digi-api uses "" for "unknown": the domain says `None`."""
    text = (value or "").strip()
    return text or None


def _digivolution(dto: EvolutionDTO) -> Digivolution:
    return Digivolution(id=dto.id, name=dto.digimon, condition=_text(dto.condition))


def to_digimon(dto: DigimonDTO) -> Digimon:
    return Digimon(
        id=dto.id,
        name=dto.name,
        levels=tuple(entry.level for entry in dto.levels),
        types=tuple(entry.type for entry in dto.types),
        attributes=tuple(entry.attribute for entry in dto.attributes),
        fields=tuple(entry.field for entry in dto.fields),
        x_antibody=dto.x_antibody,
        release_year=_text(dto.release_date),
        description=next(
            (_text(d.description) for d in dto.descriptions if d.language == ENGLISH), None
        ),
        skills=tuple(
            Skill(s.skill, translation=_text(s.translation), description=_text(s.description))
            for s in dto.skills
        ),
        image_url=dto.images[0].href if dto.images else None,
        prior_evolutions=tuple(_digivolution(e) for e in dto.prior_evolutions),
        next_evolutions=tuple(_digivolution(e) for e in dto.next_evolutions),
    )


def to_summaries(dto: DigimonListDTO) -> tuple[DigimonSummary, ...]:
    return tuple(DigimonSummary(id=ref.id, name=ref.name) for ref in dto.content)


def to_page(dto: DigimonListDTO, *, page: int, page_size: int) -> DigimonPage:
    return DigimonPage(
        total=dto.pageable.total_elements,
        page=page,
        page_size=page_size,
        items=to_summaries(dto),
    )
