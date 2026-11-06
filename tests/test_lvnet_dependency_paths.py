"""A ``uses :`` entry writes the path the VI stores for the dependency, as
LabVIEW stores it (``stored_path``): terse and verbose, with ``(missing)`` and
``(found <path>)`` in verbose only; every form reads back from the text."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, NodeType
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import _reconstruct_dependency
from lvkit.graph.netlist_build import _build_dependency_manifest
from lvkit.graph.netlist_models import NetlistDependency
from lvkit.graph.render_lvnet import render_lvnet_uses
from lvkit.stored_path import from_stored, to_stored

_FORMS = [
    # (tokens, path type, stored text)
    (["", "Build Path__ogtk.vi"], 1, "./Build Path__ogtk.vi"),
    (["", "sub", "X.vi"], 1, "./sub/X.vi"),
    (["", "", "shared", "X.vi"], 1, "../shared/X.vi"),
    (["", "", "", "a", "X.vi"], 1, "../../a/X.vi"),
    (["", "..", "X.vi"], 1, "./../X.vi"),
    (
        [
            "<userlib>",
            "_OpenG.lib",
            "error",
            "error.llb",
            "Build Error Cluster__ogtk.vi",
        ],
        None,
        "<userlib>/_OpenG.lib/error/error.llb/Build Error Cluster__ogtk.vi",
    ),
    (["<vilib>", "Utility", "X.vi"], 1, "<vilib>/Utility/X.vi"),
    (["", "opt", "X.vi"], 0, "/opt/X.vi"),
    (["C", "Program Files", "X.vi"], 0, "C:/Program Files/X.vi"),
    (["server", "share", "X.vi"], 0, "//server/share/X.vi"),
]


@pytest.mark.parametrize(("tokens", "path_type", "text"), _FORMS)
def test_a_stored_path_is_written_and_read_back(
    tokens: list[str], path_type: int | None, text: str
) -> None:
    assert to_stored(tokens, path_type) == text
    read_tokens, read_type = from_stored(text)
    assert read_tokens == tokens
    assert read_type == path_type or (path_type == 1 and read_type is None)
    assert to_stored(read_tokens, read_type) == text


def _graph(tmp_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    caller = tmp_path / "Caller.vi"
    graph._source_paths[str(caller)] = caller  # noqa: SLF001
    graph._vi_nodes[str(caller)] = set()  # noqa: SLF001
    graph._dep_graph.add_node(str(caller))  # noqa: SLF001
    return graph, str(caller)


def _loaded(
    graph: InMemoryVIGraph, caller: str, file: Path, tokens: list[str], kind: int | None
) -> str:
    key = str(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("")
    graph._source_paths[key] = file  # noqa: SLF001
    graph._dep_graph.add_node(key, qname=file.name, node_type=NodeType.VI)  # noqa: SLF001
    graph._dep_graph.add_edge(caller, key)  # noqa: SLF001
    graph._recorded.record(key, caller, tokens, kind)  # noqa: SLF001
    graph._recorded.note_stored(key, caller, tokens, kind)  # noqa: SLF001
    return key


def _absent(
    graph: InMemoryVIGraph, caller: str, qname: str, tokens: list[str]
) -> None:
    graph._add_stub(qname, NodeType.VI, qname, qname, caller=caller)  # noqa: SLF001
    graph._recorded.record(qname, caller, tokens, None)  # noqa: SLF001
    graph._recorded.note_stored(qname, caller, tokens, None)  # noqa: SLF001


def _manifest(tmp_path: Path) -> list[NetlistDependency]:
    graph, caller = _graph(tmp_path)
    _loaded(graph, caller, tmp_path / "B.vi", ["", "B.vi"], 1)
    _loaded(
        graph,
        caller,
        tmp_path / "Convert File Extension (String)__ogtk.vi",
        ["", "Convert File Extension (String)__ogtk.vi"],
        1,
    )
    _loaded(graph, caller, tmp_path / "sub" / "D.vi", ["", "D.vi"], 1)  # elsewhere
    _loaded(
        graph,
        caller,
        tmp_path / "lib" / "L.vi",
        ["<userlib>", "_OpenG.lib", "L.vi"],
        None,
    )
    _absent(graph, caller, "G.vi", ["<vilib>", "Utility", "G.vi"])
    return _build_dependency_manifest(graph, caller)


def _text(deps: list[NetlistDependency], *, verbose: bool) -> list[str]:
    lines: list[str] = []
    render_lvnet_uses(deps, lines, verbose=verbose)
    return [ln.strip() for ln in lines[1:]]


def _entry(lines: list[str], qualified: str) -> str:
    (line,) = [ln for ln in lines if ln.split(maxsplit=1)[1].startswith(qualified)]
    return " ".join(line.split())


def test_terse_writes_the_stored_path_and_no_marks(tmp_path: Path) -> None:
    lines = _text(_manifest(tmp_path), verbose=False)
    assert _entry(lines, "B.vi") == "subVI B.vi ; ./B.vi"
    assert _entry(lines, "L.vi") == "subVI L.vi ; <userlib>/_OpenG.lib/L.vi"
    assert _entry(lines, "G.vi") == "subVI G.vi ; <vilib>/Utility/G.vi"
    assert _entry(lines, "D.vi") == "subVI D.vi ; ./D.vi"
    assert not any("(missing)" in ln or "(found" in ln for ln in lines)


def test_verbose_marks_a_missing_file_and_one_found_elsewhere(tmp_path: Path) -> None:
    lines = _text(_manifest(tmp_path), verbose=True)
    assert _entry(lines, "G.vi") == "subVI G.vi ; <vilib>/Utility/G.vi (missing)"
    assert _entry(lines, "D.vi") == "subVI D.vi ; ./D.vi (found ./sub/D.vi)"
    assert _entry(lines, "B.vi") == "subVI B.vi ; ./B.vi"


def test_every_entry_reads_back_to_the_built_model(tmp_path: Path) -> None:
    deps = [replace(d, interface=[]) for d in _manifest(tmp_path)]
    lines = _text(deps, verbose=True)
    body = "\n".join(f"    {ln}" for ln in lines)
    parsed = parse_lvnet(
        f"vi X :\n  uses :\n{body}\n  front-panel :\n    pattern : 4815\n"
        "  block-diagram :\n"
    )
    rebuilt = [_reconstruct_dependency(d, {}, {}) for d in parsed.uses]
    assert rebuilt == deps


def test_a_file_name_holding_parentheses_keeps_its_path(tmp_path: Path) -> None:
    deps = [
        replace(d, interface=[])
        for d in _manifest(tmp_path)
        if "(String)" in d.qualified
    ]
    (dep,) = deps
    assert dep.path == "./Convert File Extension (String)__ogtk.vi"
    for verbose in (False, True):
        body = "\n".join(f"    {ln}" for ln in _text(deps, verbose=verbose))
        parsed = parse_lvnet(
            f"vi X :\n  uses :\n{body}\n  front-panel :\n    pattern : 4815\n"
            "  block-diagram :\n"
        )
        assert [_reconstruct_dependency(d, {}, {}) for d in parsed.uses] == deps
