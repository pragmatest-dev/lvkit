"""Every primitive node in the sample corpus must fit its catalog entry.

A catalog entry numbers its terminals by the connector-pane slot the VI file
stores as ``parmIndex``. This streams the extracted block-diagram XML of the
sample corpus one file at a time (no graph build) and checks that each terminal
of each ``class="prim"`` node has its ``parmIndex`` in the catalog entry, in the
same direction. The test skips when the corpus or its extracted XML is absent.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit.extractor import global_cache_root
from lvkit.primitive_resolver import get_resolver

pytestmark = pytest.mark.needs_samples

VARIANT_TO_DATA = "8003"

_OUTPUT_FLAG = 1


def _sample_block_diagram_xml() -> Iterator[tuple[str, Path]]:
    """Yield ``(source, BD xml path)`` once per distinct sample-corpus VI."""
    seen: set[str] = set()
    for meta_path in sorted(global_cache_root().rglob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source = str(meta.get("source", "")).replace("\\", "/")
        digest = meta.get("sha256")
        if "/samples/" not in source or digest in seen:
            continue
        seen.add(digest)
        xml_paths = sorted(meta_path.parent.glob("*_BDHb.xml"))
        if xml_paths:
            yield source, xml_paths[0]


def _prim_nodes(xml_path: Path) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    """Yield ``(primResID, [(parmIndex, direction), ...])`` per prim node.

    A missing ``parmIndex`` is 0 (the XML omits the default). Direction is the
    terminal DCO's ``objFlags`` bit 0.
    """
    for _, element in ET.iterparse(xml_path, events=("end",)):
        if element.tag != "SL__arrayElement" or element.get("class") != "prim":
            continue
        prim_id = element.findtext("primResID")
        term_list = element.find("termList")
        if prim_id is None or term_list is None:
            continue
        slots: list[tuple[int, str]] = []
        for term in term_list.findall("SL__arrayElement[@class='term']"):
            dco = term.find("dco")
            if dco is None:
                continue
            flags = int(dco.findtext("objFlags") or 0)
            direction = "out" if flags & _OUTPUT_FLAG else "in"
            slots.append((int(dco.findtext("parmIndex") or 0), direction))
        yield prim_id, slots
        element.clear()


@pytest.fixture(scope="module")
def conflicts() -> dict[str, int]:
    """primResID -> number of corpus nodes with a terminal the catalog lacks."""
    # Read the user's real extraction cache (read-only); the per-test cache the
    # suite installs is empty.
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("LVKIT_CACHE_DIR", raising=False)
        xml_files = list(_sample_block_diagram_xml())
    if not xml_files:
        pytest.skip("no extracted sample-corpus XML in the extraction cache")

    resolver = get_resolver()
    catalog: dict[str, set[tuple[int, str]] | None] = {}
    found: dict[str, int] = {}
    for _source, xml_path in xml_files:
        for prim_id, slots in _prim_nodes(xml_path):
            if prim_id not in catalog:
                resolved = resolver.resolve(prim_id=int(prim_id))
                catalog[prim_id] = (
                    {(t.index, t.direction) for t in resolved.terminals}
                    if resolved is not None and resolved.terminals
                    else None
                )
            entry = catalog[prim_id]
            if entry is not None and any(slot not in entry for slot in slots):
                found[prim_id] = found.get(prim_id, 0) + 1
    return found


def test_every_prim_terminal_is_in_its_catalog_entry(
    conflicts: dict[str, int],
) -> None:
    others = {k: v for k, v in conflicts.items() if k != VARIANT_TO_DATA}
    assert not others, (
        "primitive nodes whose parmIndex/direction is missing from the catalog "
        "entry (primResID: node count): "
        + ", ".join(f"{k}: {v}" for k, v in sorted(others.items(), key=_by_id))
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "open item: Variant To Data (8003) is numbered 0, 3, 8, 9, 11 in VIs "
        "saved by LabVIEW 11 and later but 0-4 in VIs saved by 8.2 and 9.0 "
        "(48 corpus nodes). The catalog holds one numbering, so on the older "
        "nodes names are withheld and generated Python takes the wrong "
        "terminal. Needs a catalog format that holds both layouts."
    ),
)
def test_variant_to_data_numbering_does_not_depend_on_saved_version(
    conflicts: dict[str, int],
) -> None:
    assert VARIANT_TO_DATA not in conflicts


def _by_id(item: tuple[str, int]) -> int:
    return int(item[0])
