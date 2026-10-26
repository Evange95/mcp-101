"""Pydantic DTOs mirroring the subset of digi-api.com payloads this adapter reads."""

from pydantic import BaseModel, ConfigDict, Field


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class ImageDTO(_DTO):
    href: str


class LevelDTO(_DTO):
    level: str


class TypeDTO(_DTO):
    type: str


class AttributeDTO(_DTO):
    attribute: str


class FieldDTO(_DTO):
    field: str


class DescriptionDTO(_DTO):
    language: str
    description: str


class SkillDTO(_DTO):
    skill: str
    translation: str | None = None
    description: str | None = None


class EvolutionDTO(_DTO):
    id: int
    digimon: str
    condition: str | None = None


class DigimonDTO(_DTO):
    id: int
    name: str
    x_antibody: bool = Field(default=False, alias="xAntibody")
    images: list[ImageDTO] = []
    levels: list[LevelDTO] = []
    types: list[TypeDTO] = []
    attributes: list[AttributeDTO] = []
    fields: list[FieldDTO] = []
    release_date: str | None = Field(default=None, alias="releaseDate")
    descriptions: list[DescriptionDTO] = []
    skills: list[SkillDTO] = []
    prior_evolutions: list[EvolutionDTO] = Field(default=[], alias="priorEvolutions")
    next_evolutions: list[EvolutionDTO] = Field(default=[], alias="nextEvolutions")


class DigimonRefDTO(_DTO):
    id: int
    name: str


class PageableDTO(_DTO):
    current_page: int = Field(alias="currentPage")
    total_elements: int = Field(alias="totalElements")
    total_pages: int = Field(alias="totalPages")


class DigimonListDTO(_DTO):
    # digi-api leaves `content` out entirely when a page is empty.
    content: list[DigimonRefDTO] = []
    pageable: PageableDTO


class ErrorDTO(_DTO):
    """digi-api's error body: `{"error": 1, "message": "Digimon not found"}`."""

    message: str
