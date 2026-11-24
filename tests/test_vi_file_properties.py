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
from lvkit.graph.models import (
    ExecutionProps,
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
)
from lvkit.parser.metadata import parse_vi_metadata
from tests import vi_reference_diagrams as d
from tests.helpers import build_graph, exec_generated, without_panel_attributes
from tests.test_vi_reference import _CALL, _CALLEE, _STATIC, _package

_TEXT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")

# (property name as the diagram shows it, code, the type of its value)
_ROWS = {
    "Exec.IsReentrant": (525, "Boolean"),
    "Exec.Priority": (526, "NumInt32"),
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


def _own(name: str, properties: VIProperties) -> str:
    """The source of a VI ``User.vi`` whose only output is the property ``name`` of
    the VI class read from its unwired default object."""
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
    prop = PrimitiveNode(
        id="prop",
        vi_path="User.vi",
        name="Property Node",
        node_type="propNode",
        object_name="VI",
        terminals=[
            Terminal(id="prop:ref", index=0, direction="input", lv_type=_REF),
            Terminal(id="prop:value", index=4, direction="output", lv_type=value_type),
        ],
        properties=[PropertyDef(segments=[PropertySegment(name=name, code=code)])],
        property_value_terminal_ids=["prop:value"],
    )
    context = VIContext(
        name="User.vi",
        inputs=[],
        outputs=[out],
        data_flow=[
            Wire.from_terminals(from_terminal_id="prop:value", to_terminal_id=out.id)
        ],
    )
    graph = build_graph(context, context.name, [prop])
    graph._vi_properties["User.vi"] = properties  # noqa: SLF001
    return build_module(context, context.name, graph=graph)


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
    assert not hasattr(function, "clone_name")


@pytest.mark.parametrize(
    ("kind", "number"),
    [("VI", 1), ("Control", 2), ("Global VI", 3), ("Polymorphic VI", 4)],
)
def test_vi_type_is_nis_number(tmp_path: Path, kind: str, number: int) -> None:
    function = _run(_own("VIType", _properties(vi_type=kind)), tmp_path)
    assert function.vi_type == number


def test_a_file_kind_nis_list_lacks_sets_no_vi_type(tmp_path: Path) -> None:
    source = _own("VIType", _properties(vi_type="unknown (code 99)"))
    assert ".vi_type = " not in source


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
    assert "NotImplementedError" not in source
    assert _run(source, tmp_path)().out == expected


def test_a_non_reentrant_vi_is_no_clone_and_has_no_clone_name(tmp_path: Path) -> None:
    assert _run(_own("IsCloneVI", _properties()), tmp_path)().out is False
    with pytest.raises(AttributeError):
        _run(_own("Clone Name", _properties()), tmp_path)()


def _callee_with_attributes() -> str:
    return (
        _CALLEE
        + "callee.is_reentrant = True\n"
        + "callee.priority = 4\n"
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
        562: "vi_type",
        645: "clone_name",
        646: "is_reentrant",
        698: "help_url",
    }[code]
    assert f"= callee.{attribute}\n" in source
    assert "NotImplementedError" not in source
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
