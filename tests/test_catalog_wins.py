"""A VI the vi.lib catalog gives Python for is never generated from its diagram, and
the VIs only it calls are neither loaded for generation nor generated; its entry is
checked against the connector pane of its loaded file."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import TERMINAL_MAPPING, gap_of
from lvkit.codegen.vilib_pane import check_against_pane
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.load_mode import LoadMode
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.pipeline import generate_python
from lvkit.vilib_resolver import CatalogPaneMismatch, VIEntry, VITerminal
from tests.test_call_identity import _static, _Tree

SYSDIR = ["<vilib>", "Utility", "sysdir.llb", "Get System Directory.vi"]
LIB = "vilib/Utility/sysdir.llb"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    (built.root / "proj" / ".lvkit").mkdir(parents=True)
    built.vi(f"{LIB}/deep.vi", "deep.vi", [])
    built.vi(
        f"{LIB}/helper.vi", "helper.vi", [], [_static(["deep.vi"], ["", "deep.vi"])]
    )
    built.vi(
        f"{LIB}/Get System Directory.vi",
        "Get System Directory.vi",
        [],
        [_static(["helper.vi"], ["", "helper.vi"])],
    )
    built.vi(
        "proj/caller_a.vi",
        "caller_a.vi",
        [],
        [_static(["Get System Directory.vi"], SYSDIR)],
    )
    built.vi(
        "proj/caller_b.vi",
        "caller_b.vi",
        [],
        [
            _static(["Get System Directory.vi"], SYSDIR),
            _static(["helper.vi"], ["<vilib>", "Utility", "sysdir.llb", "helper.vi"]),
        ],
    )
    return built


def _generate(tree: Path | _Tree, tmp_path: Path, caller: str) -> list[str]:
    assert isinstance(tree, _Tree)
    out = tmp_path / "gen" / caller
    generate_python(
        tree.root / "proj" / f"{caller}.vi",
        out,
        vilib_root=tree.root / "vilib",
    )
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.py"))


def test_the_catalog_vi_and_what_only_it_calls_get_no_diagram_module(
    project: _Tree, tmp_path: Path
) -> None:
    modules = _generate(project, tmp_path, "caller_a")
    assert any(m.endswith("get_system_directory.py") for m in modules)
    assert not [m for m in modules if m.endswith(("helper.py", "deep.py"))]
    source = next((tmp_path / "gen").rglob("get_system_directory.py")).read_text()
    assert "def _get_system_directory_path" in source


def test_a_vi_called_from_outside_the_catalog_vis_tree_is_generated(
    project: _Tree, tmp_path: Path
) -> None:
    modules = _generate(project, tmp_path, "caller_b")
    assert any(m.endswith("helper.py") for m in modules)
    assert any(m.endswith("deep.py") for m in modules)


def test_loading_for_generation_stops_at_the_catalog_vi(project: _Tree) -> None:
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=project.root / "vilib")
    graph.set_catalog_stop(True)
    graph.load_vi(project.root / "proj" / "caller_a.vi", LoadMode.FULL)
    names = {Path(k).name for k in graph.list_vis()}
    assert "Get System Directory.vi" in names
    assert not names & {"helper.vi", "deep.vi"}


def test_other_views_load_the_catalog_vis_callees(project: _Tree) -> None:
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=project.root / "vilib")
    graph.load_vi(project.root / "proj" / "caller_a.vi", LoadMode.FULL)
    names = {Path(k).name for k in graph.list_vis()}
    assert {"Get System Directory.vi", "helper.vi", "deep.vi"} <= names


def _terminal(
    index: int, name: str, direction: str, type_name: str, default: object = None
) -> Terminal:
    return Terminal(
        id=f"t{index}{direction}",
        index=index,
        direction=direction,
        name=name,
        lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=type_name),
        default_value=default,  # type: ignore[arg-type]
    )


def _entry(*terminals: VITerminal) -> VIEntry:
    return VIEntry(name="X", terminals=list(terminals))


def test_a_catalog_terminal_without_an_index_takes_its_controls_slot() -> None:
    entry = _entry(VITerminal(name="path", direction="in"))
    pane = [
        _terminal(3, "path", "input", "Path"),
        _terminal(1, "other", "input", "Path"),
    ]
    checked = check_against_pane(entry, pane)
    assert checked.mismatches == []
    assert [t.index for t in checked.entry.terminals] == [3]


def test_an_input_the_catalog_gives_no_default_takes_the_saved_default() -> None:
    entry = _entry(
        VITerminal(name="count", direction="in", index=0),
        VITerminal(name="size", direction="in", index=1, default=7),
    )
    pane = [
        _terminal(0, "count", "input", "NumInt32", default=5),
        _terminal(1, "size", "input", "NumInt32", default=9),
    ]
    checked = check_against_pane(entry, pane)
    assert [t.default for t in checked.entry.terminals] == [5, 7]


def test_an_index_or_type_that_disagrees_with_the_pane_is_reported() -> None:
    entry = _entry(
        VITerminal(name="path", direction="in", index=2),
        VITerminal(name="flag", direction="in", index=1, type="Boolean"),
    )
    pane = [
        _terminal(3, "path", "input", "Path"),
        _terminal(1, "flag", "input", "String"),
    ]
    checked = check_against_pane(entry, pane)
    assert checked.mismatches == [
        "the catalog gives 'path' index 2, but the loaded VI's control 'path' is "
        "at index 3",
        "the catalog gives 'flag' type Boolean, but the loaded VI's control at "
        "index 1 is String",
    ]


def test_the_catalog_vocabulary_for_a_number_agrees_with_any_numeric_control() -> None:
    entry = _entry(VITerminal(name="n", direction="in", index=0, type="stdNum"))
    checked = check_against_pane(entry, [_terminal(0, "n", "input", "NumInt16")])
    assert checked.mismatches == []


def test_a_disagreement_is_a_terminal_mapping_gap_with_its_reason() -> None:
    exc = CatalogPaneMismatch("Get System Directory.vi", ["the catalog gives x"])
    gap = gap_of(exc, "/p/caller.vi", None)
    assert gap.kind == TERMINAL_MAPPING
    assert gap.reason == (
        "the catalog's terminals for the vi.lib VI 'Get System Directory.vi' "
        "disagree with its connector pane: the catalog gives x"
    )


@pytest.mark.parametrize(
    "pane_type",
    [
        "NumInt16",
        "NumUInt64",
        "NumFloat64",
        "NumComplex128",
        "UnitFloat64",
        "UnitComplex64",
        "UnitUInt16",
        "FixedPoint",
        "ComplexFixedPt",
    ],
)
def test_a_numeric_catalog_terminal_matches_every_number_type(pane_type: str) -> None:
    entry = _entry(VITerminal(name="x", direction="in", index=0, type="stdNum"))
    checked = check_against_pane(entry, [_terminal(0, "x", "input", pane_type)])
    assert checked.mismatches == []


@pytest.mark.parametrize("pane_type", ["String", "Boolean", "Path"])
def test_a_numeric_catalog_terminal_disagrees_with_a_non_number(
    pane_type: str,
) -> None:
    entry = _entry(VITerminal(name="x", direction="in", index=0, type="stdNum"))
    checked = check_against_pane(entry, [_terminal(0, "x", "input", pane_type)])
    assert len(checked.mismatches) == 1
