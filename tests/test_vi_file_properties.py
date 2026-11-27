"""The VI class properties that the VI's file records (Exec.IsReentrant,
Exec.Priority, VI Type, Help.URL, IsCloneVI, Clone Name): each generated function
carries them as attributes, a Property Node on the default VI object reads its own
function's and one on a VI reference reads the referenced function's. Each case is
a handmade diagram of just the construct, built into a VI and run."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes.vi_file_properties import (
    _VI_TYPE_NUMBER,
    CLONE_NAME_ERROR_CODE,
)
from lvkit.graph.models import (
    ExecutionProps,
    GraphNode,
    PrimitiveNode,
    Priority,
    Reentrancy,
    VIContext,
    VIProperties,
    Wire,
)
from lvkit.models import (
    FPTerminal,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
    VIKind,
)
from lvkit.parser.metadata import parse_vi_metadata
from tests import vi_reference_diagrams as d
from tests.class_graphs import Project, Recipe
from tests.helpers import (
    build_graph,
    error_cluster_type,
    exec_generated,
    without_panel_attributes,
)
from tests.test_front_panel_title import other_title
from tests.test_run_vi import target
from tests.test_vi_reference import _CALL, _CALLEE, _STATIC, _package

_TEXT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")

# (property name as the diagram shows it, code, the type of its value)
_ROWS = {
    "Exec.IsReentrant": (525, "Boolean"),
    "Exec.Priority": (526, "NumInt32"),
    "Exec.ReentrancyType": (648, "NumInt32"),
    "VIType": (562, "NumInt32"),
    "Clone Name": (645, "String"),
    "IsCloneVI": (646, "Boolean"),
    "Help.URL": (698, "String"),
}


def _properties(
    reentrancy: Reentrancy = Reentrancy.NON_REENTRANT,
    priority: Priority = Priority.NORMAL,
    vi_type: str | None = "VI",
    help_url: str = "",
) -> VIProperties:
    return VIProperties(
        vi_type=vi_type,
        help_url=help_url,
        execution=ExecutionProps(priority=priority, reentrancy=reentrancy),
    )


def _own(
    name: str,
    properties: VIProperties,
    gaps: list | None = None,
    with_error: bool = False,
) -> str:
    """The source of a VI ``User.vi`` whose only output is the property ``name`` of
    the VI class read from its unwired default object; ``with_error`` also wires the
    Property Node's error out to the VI's ``error out``."""
    code, type_name = _ROWS[name]
    value_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=type_name)
    out = FPTerminal(
        id="fp:out",
        index=0,
        direction="output",
        name="out",
        is_public=True,
        is_indicator=True,
        lv_type=value_type,
    )
    terminals = [
        Terminal(id="prop:ref", index=0, direction="input", lv_type=_REF),
        Terminal(id="prop:value", index=4, direction="output", lv_type=value_type),
    ]
    outputs = [out]
    flow = [Wire.from_terminals(from_terminal_id="prop:value", to_terminal_id=out.id)]
    if with_error:
        error_type = error_cluster_type()
        terminals += [
            Terminal(id="prop:ein", index=2, direction="input", lv_type=error_type),
            Terminal(id="prop:eout", index=3, direction="output", lv_type=error_type),
        ]
        outputs.append(
            FPTerminal(
                id="fp:eout",
                index=1,
                direction="output",
                name="error out",
                is_public=True,
                is_indicator=True,
                lv_type=error_type,
            )
        )
        flow.append(
            Wire.from_terminals(from_terminal_id="prop:eout", to_terminal_id="fp:eout")
        )
    prop = PrimitiveNode(
        id="prop",
        vi_path="User.vi",
        name="Property Node",
        node_type="propNode",
        object_name="VI",
        terminals=terminals,
        properties=[PropertyDef(segments=[PropertySegment(name=name, code=code)])],
        property_value_terminal_ids=["prop:value"],
    )
    context = VIContext(name="User.vi", inputs=[], outputs=outputs, data_flow=flow)
    graph = build_graph(context, context.name, [prop])
    graph._vi_properties["User.vi"] = properties  # noqa: SLF001
    return build_module(context, context.name, graph=graph, gaps=gaps)


def _run(source: str, tmp_path: Path) -> Any:
    path = tmp_path / "user.py"
    path.write_text(source, encoding="utf-8")
    namespace: dict = {"__file__": str(path)}
    exec_generated(source, namespace, str(path))
    return namespace["user"]


_REENTRANT = _properties(
    Reentrancy.SHARED_CLONE,
    Priority.TIME_CRITICAL,
    "Global VI",
    "https://example.com/help",
)


def test_the_function_carries_what_its_file_records(tmp_path: Path) -> None:
    function = _run(_own("Exec.IsReentrant", _REENTRANT), tmp_path)
    assert function.is_reentrant is True
    assert function.priority == 5
    assert function.vi_type == 3
    assert function.help_url == "https://example.com/help"
    assert function.clone_name == "User.vi"


def test_a_vi_that_is_not_reentrant_has_no_clone_name(tmp_path: Path) -> None:
    function = _run(_own("Exec.IsReentrant", _properties()), tmp_path)
    assert function.is_reentrant is False
    assert function.priority == 2
    assert function.vi_type == 1
    assert function.help_url == ""
    assert function.clone_name is None


@pytest.mark.parametrize(
    ("kind", "number"),
    [("VI", 1), ("Control", 2), ("Global VI", 3), ("Polymorphic VI", 4)],
)
def test_vi_type_is_nis_number(tmp_path: Path, kind: str, number: int) -> None:
    function = _run(_own("VIType", _properties(vi_type=kind)), tmp_path)
    assert function.vi_type == number


def test_every_vi_kind_has_nis_number() -> None:
    """Every kind ``VIKind`` has maps to a number of NI's VI Type list."""
    assert {kind.label: _VI_TYPE_NUMBER[kind] for kind in VIKind} == {
        "VI": 1,
        "Control": 2,
        "Global VI": 3,
        "Polymorphic VI": 4,
    }


def test_a_file_kind_nis_list_lacks_is_a_gap(tmp_path: Path) -> None:
    gaps: list = []
    source = _own("VIType", _properties(vi_type="unknown (code 99)"), gaps)
    assert [(gap.vi, gap.node_name) for gap in gaps] == [("User.vi", "VI Type")]
    assert "unknown (code 99)" in gaps[0].reason
    function = _run(source, tmp_path)
    assert function.vi_type is None
    with pytest.raises(NotImplementedError, match="User.vi"):
        function()


@pytest.mark.parametrize(
    ("priority", "number"),
    [
        (Priority.BACKGROUND, 1),
        (Priority.NORMAL, 2),
        (Priority.ABOVE_NORMAL, 3),
        (Priority.HIGH, 4),
        (Priority.TIME_CRITICAL, 5),
        (Priority.SUBROUTINE, 6),
    ],
)
def test_priority_is_nis_number(
    tmp_path: Path, priority: Priority, number: int
) -> None:
    function = _run(_own("Exec.Priority", _properties(priority=priority)), tmp_path)
    assert function.priority == number


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Exec.IsReentrant", True),
        ("Exec.Priority", 5),
        ("Exec.ReentrancyType", 0),
        ("VIType", 3),
        ("Help.URL", "https://example.com/help"),
        ("IsCloneVI", True),
        ("Clone Name", "User.vi"),
    ],
)
def test_the_default_vi_object_reads_its_own_function(
    tmp_path: Path, name: str, expected: object
) -> None:
    source = _own(name, _REENTRANT)
    assert "lvkit" not in source
    guarded = name in {"VIType", "Exec.ReentrancyType"}
    assert source.count("NotImplementedError") == guarded
    assert _run(source, tmp_path)().out == expected


@pytest.mark.parametrize(
    ("reentrancy", "number"),
    [
        (Reentrancy.SHARED_CLONE, 0),
        (Reentrancy.PREALLOCATED_CLONE, 1),
        (Reentrancy.NON_REENTRANT, None),
    ],
)
def test_the_reentrancy_type_is_nis_number_of_a_reentrant_vi(
    tmp_path: Path, reentrancy: Reentrancy, number: int | None
) -> None:
    function = _run(
        _own("Exec.ReentrancyType", _properties(reentrancy=reentrancy)), tmp_path
    )
    assert function.reentrancy_type == number


def test_a_non_reentrant_vi_is_no_clone(tmp_path: Path) -> None:
    assert _run(_own("IsCloneVI", _properties()), tmp_path)().out is False


def test_clone_name_of_a_non_reentrant_vi_is_an_error_on_the_error_out(
    tmp_path: Path,
) -> None:
    source = _own("Clone Name", _properties(), with_error=True)
    assert "lvkit" not in source
    result = _run(source, tmp_path)()
    assert result.out == ""
    assert result.error_out.code == CLONE_NAME_ERROR_CODE == 42
    assert result.error_out.status is True
    assert result.error_out.source == "VI Clone Name"
    assert "User.vi" in result.error_out.message


def test_clone_name_of_a_reentrant_vi_is_its_name_and_no_error(tmp_path: Path) -> None:
    result = _run(_own("Clone Name", _REENTRANT, with_error=True), tmp_path)()
    assert result.out == "User.vi"
    assert result.error_out is None


@pytest.mark.parametrize(
    ("kind", "number"),
    [("VI", 1), ("Control", 2), ("Global VI", 3), ("Polymorphic VI", 4)],
)
def test_vi_type_read_gives_nis_number(
    tmp_path: Path, kind: str, number: int
) -> None:
    source = _own("VIType", _properties(vi_type=kind), with_error=True)
    result = _run(source, tmp_path)()
    assert (result.out, result.error_out) == (number, None)


def _help_url_writer(target_key: str) -> Recipe:
    """A VI whose Property Node on the reference of ``target_key`` writes its
    input ``t`` to Help:Document Web URL, then reads it into ``out``."""
    build = other_title(target_key, "wr")

    def recipe(vi: str) -> tuple[VIContext, list[GraphNode]]:
        context, nodes = build(vi)
        for node in nodes:
            if isinstance(node, PrimitiveNode) and node.properties:
                node.properties = [
                    PropertyDef(segments=[PropertySegment(name="Help.URL", code=698)])
                    for _ in node.properties
                ]
        return context, nodes

    return recipe


def test_a_help_url_write_sets_the_help_url_and_nothing_else(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    project.graph._vi_properties[target_key] = _properties(  # noqa: SLF001
        Reentrancy.SHARED_CLONE, Priority.HIGH, "VI", "http://old"
    )
    project.add_vi("Runner", _help_url_writer(target_key))
    project.graph._dep_graph.add_edge(  # noqa: SLF001
        project.vis["Runner"], target_key
    )
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        function = module("target").target
        names = ("is_reentrant", "priority", "vi_type", "clone_name", "window_title")
        before = {name: getattr(function, name) for name in names}
        assert function.help_url == "http://old"
        assert module("runner").runner(t="http://new").out == "http://new"
        assert function.help_url == "http://new"
        assert {name: getattr(function, name) for name in names} == before


def _callee_with_attributes() -> str:
    return (
        _CALLEE
        + "callee.is_reentrant = True\n"
        + "callee.priority = 4\n"
        + "callee.reentrancy_type = 1\n"
        + "callee.vi_type = 2\n"
        + "callee.help_url = 'http://help'\n"
        + "callee.clone_name = 'Callee.vi'\n"
    )


@pytest.mark.parametrize("name", sorted(_ROWS))
def test_a_vi_reference_reads_the_referenced_functions_attribute(
    tmp_path: Path, name: str
) -> None:
    code = _ROWS[name][0]
    prop = d.vi_name_property(60, code, name)
    loaded = d.load(
        tmp_path,
        [_STATIC, prop, _CALL],
        [d.wire(100, 11, 61), d.wire(101, 11, 31), d.wire(102, 69, 41)],
    )
    source = without_panel_attributes(
        build_module(
            loaded.graph.get_vi_context(loaded.caller),
            loaded.caller,
            graph=loaded.graph,
        )
    )
    attribute = {
        525: "is_reentrant",
        526: "priority",
        648: "reentrancy_type",
        562: "vi_type",
        645: "clone_name",
        646: "is_reentrant",
        698: "help_url",
    }[code]
    assert f"= callee.{attribute}\n" in source
    guarded = name in {"VIType", "Exec.ReentrancyType"}
    assert source.count("NotImplementedError") == guarded
    for refs in _package(tmp_path, source, _callee_with_attributes()):
        refs.refs()  # type: ignore[attr-defined]
        assert "generated_refs.callee" in sys.modules


@pytest.mark.parametrize(
    ("block", "url"),
    [
        (b"\x00\x00\x00\x00", ""),
        (b"\x00\x00\x00\x0dhttps://a.b/c\x00\x00\x00", "https://a.b/c"),
    ],
)
def test_the_help_url_is_read_from_the_vis_help_website_block(
    tmp_path: Path, block: bytes, url: str
) -> None:
    (tmp_path / "x.xml").write_text("<LVSRData></LVSRData>", encoding="utf-8")
    (tmp_path / "x_HLPW.bin").write_bytes(block)
    assert parse_vi_metadata(tmp_path / "x.xml")["help_url"] == url


def test_a_vi_with_no_help_website_block_has_no_help_url(tmp_path: Path) -> None:
    (tmp_path / "x.xml").write_text("<LVSRData></LVSRData>", encoding="utf-8")
    assert parse_vi_metadata(tmp_path / "x.xml")["help_url"] == ""
