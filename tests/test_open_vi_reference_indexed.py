"""Open VI Reference inside a For Loop that indexes an array of names: the
computed VI name is one element of the array, so it is a name (a string), not
the array's type."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.models import LVType, LVTypeKind
from tests import structure_diagrams as sd
from tests import vi_reference_diagrams as d

_STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")


def _loaded(tmp_path: Path, element_type: int, array_of: LVType) -> d.Loaded:
    """A For Loop whose array input (outer tunnel 420, the control ``names``)
    reaches the VI path of the Open VI Reference (terminal 23) inside it."""
    border = [
        sd.term(
            420,
            sd.dco("lpTun", 421, type_id=element_type, faces=[422, 420]),
        )
    ]
    inner = sd.diag(
        450,
        nodes=[sd.srn(460, [sd.face(422, 421)]), d.open_vi_reference(20)],
    )
    xml = sd.root_diagram(
        [sd.loop("forLoop", 400, border, inner)],
        [],
        [sd.wire(901, 422, 23)],
    )
    (tmp_path / "T.xml").write_text(
        sd.main_xml([*sd.TYPES, *sd.PATH_TYPES]), encoding="utf-8"
    )
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(xml, encoding="utf-8")
    graph = InMemoryVIGraph()
    graph.load_vi(bd)
    vi = graph.list_vis()[0]
    loaded = d.Loaded(graph, vi, "")
    array = LVType(kind=LVTypeKind.ARRAY, element_type=array_of, dimensions=1)
    d.add_control(loaded, "names", array, 420, uid="names")
    return loaded


def _source_and_gaps(loaded: d.Loaded) -> tuple[str, list[Gap]]:
    gaps: list[Gap] = []
    source = build_module(
        loaded.graph.get_vi_context(loaded.caller),
        loaded.caller,
        graph=loaded.graph,
        gaps=gaps,
    )
    return source, gaps


def test_an_array_of_names_indexed_into_open_vi_reference_is_a_name(
    tmp_path: Path,
) -> None:
    loaded = _loaded(tmp_path, sd.STR, _STRING)
    source, gaps = _source_and_gaps(loaded)
    assert [g.reason for g in gaps] == []
    assert "for " in source and "importlib.import_module(" in source
    assert "_owned_vis[" in source


def test_an_array_of_paths_indexed_into_open_vi_reference_is_a_path(
    tmp_path: Path,
) -> None:
    loaded = _loaded(tmp_path, sd.PATH, _PATH)
    source, gaps = _source_and_gaps(loaded)
    assert [g.reason for g in gaps] == []
    assert "os.path.relpath" in source


def test_an_indexed_value_of_another_type_is_still_a_gap(tmp_path: Path) -> None:
    loaded = _loaded(tmp_path, sd.I32, _STRING)
    _, gaps = _source_and_gaps(loaded)
    assert len(gaps) == 1
    assert "neither a path nor a string" in gaps[0].reason
