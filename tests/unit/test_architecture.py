"""Layering rules from CLAUDE.md, enforced on the import graph of src/pokedex_mcp."""

import ast
import sys
from pathlib import Path

import pytest

ROOT = "pokedex_mcp"
PACKAGE = Path(__file__).parents[2] / "src" / ROOT
CATALOGS = sorted(p.name for p in (PACKAGE / "catalogs").iterdir() if (p / "__init__.py").exists())


def imported_modules(directory: Path) -> dict[str, set[str]]:
    """Map each module under `directory` to the absolute module names it imports."""
    imports: dict[str, set[str]] = {}
    for path in directory.rglob("*.py"):
        names: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
        imports[str(path.relative_to(PACKAGE))] = names
    return imports


def violations(directory: Path, forbidden: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{module} imports {name}"
        for module, names in imported_modules(directory).items()
        for name in names
        if name.startswith(forbidden)
    )


def test_platform_imports_no_catalog():
    assert violations(PACKAGE / "platform", (f"{ROOT}.catalogs",)) == []


@pytest.mark.parametrize("catalog", CATALOGS)
def test_catalogs_do_not_import_each_other(catalog):
    others = tuple(f"{ROOT}.catalogs.{other}" for other in CATALOGS if other != catalog)
    assert violations(PACKAGE / "catalogs" / catalog, others) == []


@pytest.mark.parametrize("catalog", CATALOGS)
@pytest.mark.parametrize(
    ("layer", "outer_layers"),
    [("application", ("adapters",)), ("domain", ("application", "adapters"))],
)
def test_inner_layers_do_not_import_outer_ones(catalog, layer, outer_layers):
    base = f"{ROOT}.catalogs.{catalog}"
    forbidden = (*(f"{base}.{outer}" for outer in outer_layers), f"{ROOT}.platform")
    assert violations(PACKAGE / "catalogs" / catalog / layer, forbidden) == []


@pytest.mark.parametrize("catalog", CATALOGS)
def test_domain_imports_only_the_standard_library_and_itself(catalog):
    own_domain = f"{ROOT}.catalogs.{catalog}.domain"
    third_party = sorted(
        f"{module} imports {name}"
        for module, names in imported_modules(PACKAGE / "catalogs" / catalog / "domain").items()
        for name in names
        if name.split(".")[0] not in sys.stdlib_module_names and not name.startswith(own_domain)
    )
    assert third_party == []


@pytest.mark.parametrize("catalog", CATALOGS)
def test_catalogs_get_their_settings_not_the_apps_configuration(catalog):
    """A catalog owns `settings.py` and receives shared settings through `Platform`, so
    adding a catalog never means editing a file every catalog depends on."""
    forbidden = (f"{ROOT}.config", f"{ROOT}.main")
    assert violations(PACKAGE / "catalogs" / catalog, forbidden) == []
