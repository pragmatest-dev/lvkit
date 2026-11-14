"""Every ``{placeholder}`` an inline catalog template writes names a terminal of
its own entry, the way codegen names it (``terminal_placeholder``)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit._data import data_dir
from lvkit.catalog_stamp import iter_category_entries
from lvkit.codegen.nodes.subvi import terminal_placeholder
from lvkit.vilib_resolver import VIEntry

CATALOGS = ("vilib", "openg", "drivers")


def _inline_entries() -> list[tuple[str, VIEntry]]:
    found: list[tuple[str, VIEntry]] = []
    for catalog in CATALOGS:
        folder: Path = data_dir() / catalog
        for _, data in iter_category_entries(folder):
            entry = VIEntry.model_validate(data)
            if entry.python_code is not None and entry.inline:
                found.append((f"{catalog}/{entry.name}", entry))
    return found


@pytest.mark.parametrize(("label", "entry"), _inline_entries())
def test_placeholders_name_terminals_of_the_entry(label: str, entry: VIEntry) -> None:
    assert entry.python_code is not None
    placeholders = set(re.findall(r"\{(\w+)\}", entry.python_code))
    names = {terminal_placeholder(t) for t in entry.terminals}
    assert placeholders <= names, (
        f"{label}: {sorted(placeholders - names)} name no terminal "
        f"(terminals: {sorted(names)})"
    )
