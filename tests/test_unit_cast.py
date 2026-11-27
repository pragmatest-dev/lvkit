"""Convert Unit (class ``uCast``): a pure number gets the unit and is held in the
base unit (10 min is 600 s), a physical number is read back in the unit as a pure
number. NI, "Convert Unit": "if x is 10 and you enter min (minutes) ..., y is 600
seconds"; "if x is 37 meters and you enter ft (feet) ..., y is 121.36"."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.gaps import Gap
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE
from lvkit.codegen.nodes.unit_cast import unit_factor
from lvkit.codegen.panels_module import PANELS_MODULE_NAME, PANELS_MODULE_SOURCE
from lvkit.codegen.variants_module import VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind
from tests import structure_diagrams as sd
from tests import vi_reference_diagrams as d

_DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
_UNIT_DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="UnitFloat64")
_TYPES = [
    '<TypeDesc Type="NumFloat64" />',
    '<TypeDesc Type="UnitFloat64" />',
    '<TypeDesc Type="String" />',
]
_PURE, _PHYSICAL = 0, 1


def _node(uid: int, unit: str | None, in_type: int, out_type: int) -> str:
    """A uCast node: the number in is terminal ``uid + 1``, the number out
    ``uid + 3``."""
    label = (
        '<textNodeLabel class="label" uid="900"><textRec class="textHair">'
        f'<text>"{unit}"</text></textRec></textNodeLabel>'
        if unit is not None
        else ""
    )
    return (
        f'<SL__arrayElement class="uCast" uid="{uid}"><termList elements="2">'
        + sd.term(uid + 1, sd.dco("bDDCO", uid + 2, type_id=in_type))
        + sd.term(uid + 3, sd.dco("bDDCO", uid + 4, type_id=out_type, flags=1))
        + f"</termList>{label}<bounds>(0, 0, 32, 17)</bounds></SL__arrayElement>"
    )


def _loaded(tmp_path: Path, unit: str | None, in_type: int, out_type: int) -> d.Loaded:
    """A VI whose control ``x`` goes through a Convert Unit to the indicator
    ``y``."""
    (tmp_path / "T.xml").write_text(sd.main_xml(_TYPES), encoding="utf-8")
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(
        sd.root_diagram([], [_node(10, unit, in_type, out_type)], []),
        encoding="utf-8",
    )
    graph = InMemoryVIGraph()
    graph.load_vi(bd)
    vi = graph.list_vis()[0]
    loaded = d.Loaded(graph, vi, "")
    d.add_control(loaded, "x", _UNIT_DBL if in_type == _PHYSICAL else _DBL, 11)
    d.add_indicator(loaded, "y", _UNIT_DBL if out_type == _PHYSICAL else _DBL, 13, 0)
    return loaded


def _run(loaded: d.Loaded, tmp_path: Path, monkeypatch, x: float) -> float:
    gaps: list[Gap] = []
    source = build_module(
        loaded.graph.get_vi_context(loaded.caller),
        loaded.caller,
        graph=loaded.graph,
        gaps=gaps,
    )
    assert gaps == []
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "caller.py").write_text(source, encoding="utf-8")
    for module, text in (
        (ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE),
        (PANELS_MODULE_NAME, PANELS_MODULE_SOURCE),
        (VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE),
        (LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE),
    ):
        (package / f"{module}.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        module = importlib.import_module("pkg.caller")
        function = next(
            v for k, v in vars(module).items() if k.startswith("t") and callable(v)
        )
        return function(x=x).y
    finally:
        for name in ("pkg.caller", "pkg.errors", "pkg"):
            sys.modules.pop(name, None)


def test_the_parser_reads_the_unit_string_of_the_node(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path, " h ", _PURE, _PHYSICAL)
    node = next(
        n
        for n in loaded.graph.iter_nodes(loaded.caller)
        if isinstance(n, PrimitiveNode) and n.node_type == "uCast"
    )
    assert node.unit == "h"
    assert [t.lv_type.underlying_type for t in node.terminals] == [
        "NumFloat64",
        "UnitFloat64",
    ]
    assert "Convert Unit" in describe_vi(loaded.graph, loaded.caller)


@pytest.mark.parametrize(
    ("unit", "x", "expected"),
    [("h", -4.0, -14400.0), ("min", 10.0, 600.0), ("ms", 2500.0, 2.5)],
)
def test_a_pure_number_becomes_the_number_in_base_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unit: str,
    x: float,
    expected: float,
) -> None:
    loaded = _loaded(tmp_path, f" {unit} ", _PURE, _PHYSICAL)
    assert _run(loaded, tmp_path, monkeypatch, x) == pytest.approx(expected)


def test_a_physical_number_is_read_back_in_the_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = _loaded(tmp_path, " ft ", _PHYSICAL, _PURE)
    assert _run(loaded, tmp_path, monkeypatch, 37.0) == pytest.approx(37.0 / 0.3048)


def test_a_base_unit_leaves_the_value_as_it_is() -> None:
    assert unit_factor("s") == 1.0
    assert unit_factor("m/s^2") == 1.0
    assert unit_factor("km/h") == pytest.approx(1000 / 3600)


@pytest.mark.parametrize("unit", ["furlong", "", "m//s", "^2"])
def test_a_unit_the_table_does_not_know_is_none(unit: str) -> None:
    assert unit_factor(unit) is None


def test_an_unknown_unit_is_a_gap_naming_it(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path, " furlong ", _PURE, _PHYSICAL)
    gaps: list[Gap] = []
    source = build_module(
        loaded.graph.get_vi_context(loaded.caller),
        loaded.caller,
        graph=loaded.graph,
        gaps=gaps,
    )
    assert "raise NotImplementedError(" in source
    assert len(gaps) == 1 and "'furlong'" in gaps[0].reason
