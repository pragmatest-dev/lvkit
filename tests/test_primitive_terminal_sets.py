"""A primitive's terminals can be numbered differently by LabVIEW version.

Variant To Data (8003) is numbered 0-4 in VIs saved by LabVIEW 8.2/9.0 and
0, 3, 8, 9, 11 in VIs saved by 11.0 and later. The catalog holds the second
numbering and lists the first as an alternative that maps onto it; a node is
read in the one numbering that holds all of its terminals, and a node that fits
none or several is a gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit._data import load_primitives
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.primitive_resolver import get_resolver
from lvkit.primitive_resolver.models import PrimitiveEntry
from lvkit.render import render_vi
from tests.native_vi import generate, node, scalar

VARIANT_TO_DATA = 8003
OLD = [(0, "in"), (1, "out"), (2, "in"), (3, "in"), (4, "out")]
NEW = [(0, "in"), (3, "out"), (8, "in"), (9, "in"), (11, "out")]
# (variant in, data out) of each numbering
VARIANT_AND_DATA = {"old": (2, 4), "new": (8, 11)}
VARIANT = scalar("Variant")


def _bd_xml(slots: list[tuple[int, str]]) -> str:
    terms = "".join(
        f'<SL__arrayElement class="term" uid="{11 + 2 * i}">'
        f'<dco class="parm" uid="{12 + 2 * i}">'
        + ("<objFlags>1</objFlags>" if direction == "out" else "")
        + f"<termBounds>(0, {8 * i}, 8, {8 * i + 8})</termBounds>"
        + (f"<parmIndex>{index}</parmIndex>" if index else "")
        + "</dco></SL__arrayElement>"
        for i, (index, direction) in enumerate(slots)
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        '<zPlaneList elements="0" /><nodeList elements="1">'
        '<SL__arrayElement class="prim" uid="10">'
        "<bounds>(100, 100, 132, 132)</bounds>"
        f"<primResID>{VARIANT_TO_DATA}</primResID>"
        f'<termList elements="{len(slots)}">{terms}</termList>'
        "</SL__arrayElement></nodeList>"
        '<signalList elements="0" /></root></SL__rootObject>'
    )


def _graph(tmp_path: Path, slots: list[tuple[int, str]]) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "VD_BDHb.xml"
    bd_xml.write_text(_bd_xml(slots))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml, layout=True)
    return graph, graph.list_vis()[0]


LVNET_NODE = (
    "    function Variant_To_Data_10 : Variant To Data\n"
    "      in   error in  : ? default\n"
    "      in   variant   : ? default\n"
    "      in   type      : ? default\n"
    "      out  error out : ?\n"
    "      out  data      : ?"
)
TOOLTIP = (
    "<title>Variant To Data\nInputs:\n  error in: ?\n  variant: ?\n  type: ?\n"
    "Outputs:\n  error out: ?\n  data: ?\n"
)


@pytest.mark.parametrize("slots", [OLD, NEW], ids=["old", "new"])
def test_both_numberings_show_named_terminals_in_every_view(
    tmp_path: Path, slots: list[tuple[int, str]]
) -> None:
    graph, vi_name = _graph(tmp_path, slots)
    assert describe_vi(graph, vi_name).endswith(
        "## Operations\nVariant To Data [prim 8003]"
    )
    assert render_lvnet(build_netlist_from_graph(graph, vi_name)).endswith(LVNET_NODE)
    svg = render_vi(graph, vi_name, interactive=False)
    assert svg is not None
    assert TOOLTIP in svg


def _variant_to_data(numbering: str, gaps: list[Gap] | None = None):
    variant, data = VARIANT_AND_DATA[numbering]
    slots = OLD if numbering == "old" else NEW
    terminals = [
        (index, "input" if direction == "in" else "output", VARIANT)
        for index, direction in slots
    ]
    return generate(
        {"v": VARIANT},
        {"d": VARIANT},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:v", f"vd:{variant}"), (f"vd:{data}", "fp:d")],
        gaps=gaps,
    )


def test_the_old_numbering_generates_the_same_code_as_the_new() -> None:
    old, new = _variant_to_data("old"), _variant_to_data("new")
    assert old.source == new.source
    assert old.function("x").d == "x"


def _resolve(slots: list[tuple[int, str]]):
    resolved = get_resolver().resolve(prim_id=VARIANT_TO_DATA, node_slots=slots)
    assert resolved is not None
    return resolved


def test_a_chosen_numbering_renumbers_terminals_and_code_placeholders() -> None:
    new, old = _resolve(NEW), _resolve(OLD)
    assert [(t.index, t.direction, t.name) for t in new.terminals] == [
        (0, "in", "error in"),
        (3, "out", "error out"),
        (8, "in", "variant"),
        (9, "in", "type"),
        (11, "out", "data"),
    ]
    assert new.python_code == {"error out": "in_0", "data": "in_8"}
    assert [(t.index, t.direction, t.name, t.type) for t in old.terminals] == [
        (0, "in", "error in", "cluster"),
        (1, "out", "error out", "cluster"),
        (2, "in", "variant", "polymorphic"),
        (3, "in", "type", "polymorphic"),
        (4, "out", "data", "polymorphic"),
    ]
    assert old.python_code == {"error out": "in_0", "data": "in_2"}
    assert new.terminal_set_gap is None and old.terminal_set_gap is None


@pytest.mark.parametrize(
    ("slots", "reason"),
    [
        ([(0, "in"), (5, "out")], "match no known numbering"),
        ([(0, "in")], "fit several numberings"),
        ([(3, "in"), (3, "out")], "match no known numbering"),
    ],
    ids=["no-set", "ambiguous", "same-index-both-directions"],
)
def test_a_node_fitting_no_single_numbering_is_a_gap(
    slots: list[tuple[int, str]], reason: str
) -> None:
    resolved = _resolve(slots)
    assert resolved.terminals == []
    assert resolved.terminal_set_gap is not None
    assert reason in resolved.terminal_set_gap
    assert "Variant To Data" in resolved.terminal_set_gap
    terminals = [
        (index, "input" if direction == "in" else "output", VARIANT)
        for index, direction in slots
    ]
    gaps: list[Gap] = []
    run = generate(
        {"v": VARIANT},
        {"d": VARIANT},
        [node("vd", VARIANT_TO_DATA, terminals)],
        [("fp:v", f"vd:{slots[0][0]}")],
        gaps=gaps,
    )
    assert [g.reason for g in gaps] == [resolved.terminal_set_gap]
    with pytest.raises(NotImplementedError, match="Variant To Data"):
        run.function("x")


def test_a_single_numbering_primitive_is_untouched() -> None:
    resolver = get_resolver()
    plain = resolver.resolve(prim_id=1535)
    with_slots = resolver.resolve(prim_id=1535, node_slots=[(99, "in")])
    assert plain is not None and with_slots is not None
    assert with_slots.terminal_set_gap is None
    assert with_slots.terminals == plain.terminals != []


def _entries() -> dict[str, PrimitiveEntry]:
    return {
        prim_id: PrimitiveEntry.model_validate(raw)
        for prim_id, raw in load_primitives()["primitives"].items()
    }


def test_the_catalog_has_alternatives_only_where_numberings_differ() -> None:
    with_sets = {
        prim_id: entry
        for prim_id, entry in _entries().items()
        if entry.alternative_terminal_sets
    }
    assert list(with_sets) == [str(VARIANT_TO_DATA)]
    for prim_id, entry in with_sets.items():
        primary = {(t.index, t.direction) for t in entry.terminals}
        numberings = [primary]
        for alias_set in entry.alternative_terminal_sets:
            slots = [(a.index, a.direction) for a in alias_set]
            assert len(set(slots)) == len(slots), prim_id
            targets = [(a.terminal, a.direction) for a in alias_set]
            assert len(set(targets)) == len(targets), prim_id
            assert set(targets) <= primary, prim_id
            numberings.append(set(slots))
        for i, first in enumerate(numberings):
            for second in numberings[i + 1 :]:
                assert first != second, f"{prim_id}: two identical numberings"
        assert not any(t.expandable or t.per_dimension_of for t in entry.terminals)
