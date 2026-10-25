"""Builders for Digimon domain objects shared across the test suite."""

from pokedex_mcp.catalogs.digimon.domain.models import Digimon, Digivolution, Skill


def make_digimon(name: str = "Agumon", id: int = 1) -> Digimon:
    return Digimon(
        id=id,
        name=name,
        levels=("Child",),
        types=("Reptile",),
        attributes=("Vaccine",),
        fields=("Metal Empire",),
        x_antibody=False,
        release_year="1997",
        description="A Reptile Digimon with an appearance resembling a small dinosaur.",
        skills=(Skill("Baby Flame", translation=None, description="Spits a fiery breath."),),
        image_url=f"https://images.test/{id}.png",
        prior_evolutions=(Digivolution(36, "Koromon", condition=None),),
        next_evolutions=(
            Digivolution(34, "Greymon", condition="with or without Gabumon"),
            Digivolution(493, "Agnimon", condition=None),
        ),
    )
