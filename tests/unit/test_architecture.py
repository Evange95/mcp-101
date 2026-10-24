"""Layering rules from CLAUDE.md, enforced on the import graph of src/pokedex_mcp."""

import ast
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[2] / "src" / "pokedex_mcp"


def imported_modules(layer: str) -> dict[str, set[str]]:
    """Map each module of `layer` to the absolute module names it imports."""
    imports: dict[str, set[str]] = {}
    for path in (PACKAGE / layer).rglob("*.py"):
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
        imports[str(path.relative_to(PACKAGE))] = names
    return imports


def violations(layer: str, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{module} imports {name}"
        for module, names in imported_modules(layer).items()
        for name in names
        if name.startswith(forbidden)
    )


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        ("platform", ("pokedex_mcp.domain", "pokedex_mcp.application", "pokedex_mcp.adapters")),
        ("application", ("pokedex_mcp.adapters", "pokedex_mcp.platform")),
        ("domain", ("pokedex_mcp.application", "pokedex_mcp.adapters", "pokedex_mcp.platform")),
    ],
)
def test_inner_layers_do_not_import_outer_ones(layer, forbidden):
    assert violations(layer, forbidden) == []


def test_domain_imports_only_the_standard_library_and_itself():
    third_party = sorted(
        f"{module} imports {name}"
        for module, names in imported_modules("domain").items()
        for name in names
        if name.split(".")[0] not in sys.stdlib_module_names
        and not name.startswith("pokedex_mcp.domain")
    )
    assert third_party == []
