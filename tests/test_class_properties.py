"""A property is a VI the class lists inside a ``Property Definition`` folder, a
getter or a setter by its own connector pane, written as a Python property over
the private attribute of the one field its diagram moves. No VI name decides it."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lvkit.class_property import PropertyRole, property_role
from lvkit.codegen.class_builder import ClassBuilder
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.structure import LVClass, LVPrivateDataField, parse_lvclass
from tests.helpers import build_graph

_CLASS_TYPE = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="C.lvclass",
)
_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_FIELDS = [ClusterField(name="Count", type=_INT), ClusterField(name="Name", type=_INT)]

_LVCLASS = """<?xml version='1.0' encoding='UTF-8'?>
<LVClass LVVersion="25008000">
\t<Item Name="C.ctl" Type="Class Private Data" URL="C.ctl"/>
\t<Item Name="Accessor" Type="Folder">
\t\t<Item Name="Count" Type="Property Definition">
\t\t\t<Property Name="NI.ClassItem.Property.LongName" Type="Str">Item Count</Property>
\t\t\t<Item Name="Read Count.vi" Type="VI" URL="../Accessor/Read Count.vi">
\t\t\t\t<Property Name="NI.ClassItem.MethodScope" Type="UInt">1</Property>
\t\t\t</Item>
\t\t\t<Item Name="Write Count.vi" Type="VI" URL="../Accessor/Write Count.vi">
\t\t\t\t<Property Name="NI.ClassItem.MethodScope" Type="UInt">1</Property>
\t\t\t</Item>
\t\t</Item>
\t\t<Item Name="Name" Type="Property Definition">
\t\t\t<Property Name="NI.ClassItem.Property.LongName" Type="Str">Label</Property>
\t\t\t<Item Name="Read Name.vi" Type="VI" URL="../Accessor/Read Name.vi">
\t\t\t\t<Property Name="NI.ClassItem.MethodScope" Type="UInt">3</Property>
\t\t\t</Item>
\t\t</Item>
\t</Item>
\t<Item Name="Get Config.vi" Type="VI" URL="Get Config.vi">
\t\t<Property Name="NI.ClassItem.MethodScope" Type="UInt">1</Property>
\t</Item>
</LVClass>
"""


def _terminal(
    tid: str, direction: str, lv_type: LVType, name: str, index: int
) -> FPTerminal:
    return FPTerminal(
        id=tid,
        index=index,
        direction=direction,
        name=name,
        lv_type=lv_type,
        is_public=True,
        is_indicator=direction == "output",
    )


def test_property_folders_list_their_vis_and_nothing_else_is_a_property(
    tmp_path: Path,
) -> None:
    path = tmp_path / "C.lvclass"
    path.write_text(_LVCLASS, encoding="utf-8")
    methods = {m.name: m for m in parse_lvclass(path).methods}
    assert {n: (m.property_name, m.scope) for n, m in methods.items()} == {
        "Read Count": ("Item Count", "public"),
        "Write Count": ("Item Count", "public"),
        "Read Name": ("Label", "protected"),
        "Get Config": (None, "public"),
    }


def test_a_pane_decides_which_accessor_a_property_vi_is() -> None:
    self_in = _terminal("a", "input", _CLASS_TYPE, "C", 0)
    self_out = _terminal("b", "output", _CLASS_TYPE, "C out", 1)
    value_in = _terminal("c", "input", _INT, "v", 2)
    value_out = _terminal("d", "output", _INT, "v", 3)
    assert property_role([self_in], [self_out, value_out], "C") is PropertyRole.GETTER
    assert property_role([self_in, value_in], [self_out], "C") is PropertyRole.SETTER
    assert property_role([self_in], [self_out], "C") is None
    assert property_role([self_in, value_in], [value_out], "C") is PropertyRole.SETTER
    assert property_role([self_in], [value_out, value_out], "C") is None


def _fields(lv_type: LVType, caller_vi_key: str | None = None) -> list[ClusterField]:
    return _FIELDS


def _read(name: str, field_index: int) -> tuple[VIContext, list[PrimitiveNode]]:
    context = VIContext(
        name=name,
        inputs=[_terminal(f"{name}:in", "input", _CLASS_TYPE, "C", 0)],
        outputs=[
            _terminal(f"{name}:out", "output", _INT, "value", 1),
        ],
        data_flow=[
            Wire.from_terminals(f"{name}:in", f"{name}:agg"),
            Wire.from_terminals(f"{name}:field", f"{name}:out"),
        ],
    )
    node = PrimitiveNode(
        id=f"{name}:unbundle",
        vi_path=name,
        node_type="nMux",
        terminals=[
            Terminal(
                id=f"{name}:agg",
                index=0,
                direction="input",
                lv_type=_CLASS_TYPE,
                nmux_role="agg",
            ),
            Terminal(
                id=f"{name}:field",
                index=1,
                direction="output",
                lv_type=_INT,
                nmux_role="list",
                nmux_field_index=field_index,
            ),
        ],
    )
    return context, [node]


def _write(name: str, field_index: int) -> tuple[VIContext, list[PrimitiveNode]]:
    context = VIContext(
        name=name,
        inputs=[
            _terminal(f"{name}:in", "input", _CLASS_TYPE, "C", 0),
            _terminal(f"{name}:value", "input", _INT, "value", 1),
        ],
        outputs=[_terminal(f"{name}:out", "output", _CLASS_TYPE, "C", 2)],
        data_flow=[
            Wire.from_terminals(f"{name}:in", f"{name}:agg"),
            Wire.from_terminals(f"{name}:value", f"{name}:field"),
            Wire.from_terminals(f"{name}:agg_out", f"{name}:out"),
        ],
    )
    node = PrimitiveNode(
        id=f"{name}:bundle",
        vi_path=name,
        node_type="nMux",
        terminals=[
            Terminal(
                id=f"{name}:agg",
                index=0,
                direction="input",
                lv_type=_CLASS_TYPE,
                nmux_role="agg",
            ),
            Terminal(
                id=f"{name}:agg_out",
                index=1,
                direction="output",
                lv_type=_CLASS_TYPE,
                nmux_role="agg",
            ),
            Terminal(
                id=f"{name}:field",
                index=2,
                direction="input",
                lv_type=_INT,
                nmux_role="list",
                nmux_field_index=field_index,
            ),
        ],
    )
    return context, [node]


def _class_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    path = tmp_path / "C.lvclass"
    path.write_text(_LVCLASS, encoding="utf-8")
    lvclass = parse_lvclass(path)
    lvclass.private_data_fields = [
        LVPrivateDataField(name="Count", python_type="int"),
        LVPrivateDataField(name="Name", python_type="int"),
    ]
    graph = InMemoryVIGraph()
    monkeypatch.setattr(
        graph, "get_type_fields", lambda lv_type, caller_vi_key=None: _FIELDS
    )
    contexts: dict[str, VIContext] = {}
    for method_name, (context, nodes) in {
        "Read Count": _read("Read Count", 0),
        "Write Count": _write("Write Count", 0),
        "Read Name": _read("Read Name", 1),
    }.items():
        build_graph(context, context.name, nodes, graph=graph)
        contexts[method_name] = context
    config = VIContext(
        name="Get Config",
        inputs=[_terminal("cfg:in", "input", _CLASS_TYPE, "C", 0)],
        outputs=[_terminal("cfg:out", "output", _CLASS_TYPE, "C", 1)],
        data_flow=[Wire.from_terminals("cfg:in", "cfg:out")],
    )
    build_graph(config, "Get Config", [], graph=graph)
    contexts["Get Config"] = config
    module = ClassBuilder().build_class_module(
        lvclass, method_contexts=contexts, graph=graph
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)


def test_the_generated_class_has_properties_over_private_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = _class_source(monkeypatch, tmp_path)
    namespace: dict[str, object] = {}
    exec(compile(source, "<class>", "exec"), namespace)  # noqa: S102
    cls = namespace["C"]
    obj = cls()  # type: ignore[operator]
    assert obj._count == 0
    assert obj._name == 0
    assert isinstance(cls.item_count, property)  # type: ignore[attr-defined]
    obj.item_count = 7
    assert obj.item_count == 7
    assert obj._count == 7
    # A protected property is named with one underscore; it has no setter.
    assert isinstance(cls._label, property)  # type: ignore[attr-defined]
    obj._name = 3
    assert obj._label == 3
    # A VI outside every property folder is an ordinary method, whatever its name.
    assert not isinstance(cls.get_config, property)  # type: ignore[attr-defined]
    assert callable(cls.get_config)  # type: ignore[attr-defined]


def test_a_property_vi_whose_diagram_moves_no_field_is_an_ordinary_method(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "C.lvclass"
    path.write_text(_LVCLASS, encoding="utf-8")
    lvclass = parse_lvclass(path)
    graph = InMemoryVIGraph()
    monkeypatch.setattr(
        graph, "get_type_fields", lambda lv_type, caller_vi_key=None: _FIELDS
    )
    context = VIContext(
        name="Read Count",
        inputs=[_terminal("x:in", "input", _CLASS_TYPE, "C", 0)],
        outputs=[_terminal("x:out", "output", _INT, "value", 1)],
        data_flow=[],
    )
    build_graph(context, "Read Count", [], graph=graph)
    module = ClassBuilder().build_class_module(
        LVClass(name="C", path=path, methods=lvclass.methods[:1]),
        method_contexts={"Read Count": context},
        graph=graph,
    )
    ast.fix_missing_locations(module)
    assert "@property" not in ast.unparse(module)


def test_a_property_that_would_hide_a_field_attribute_is_ordinary_methods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = _LVCLASS.replace(">Label<", ">Name<")
    (tmp_path / "C.lvclass").write_text(text, encoding="utf-8")
    lvclass = parse_lvclass(tmp_path / "C.lvclass")
    lvclass.private_data_fields = [LVPrivateDataField(name="Name", python_type="int")]
    graph = InMemoryVIGraph()
    monkeypatch.setattr(graph, "get_type_fields", _fields)
    context, nodes = _read("Read Name", 1)
    build_graph(context, context.name, nodes, graph=graph)
    module = ClassBuilder().build_class_module(
        lvclass, method_contexts={"Read Name": context}, graph=graph
    )
    ast.fix_missing_locations(module)
    source = ast.unparse(module)
    assert "@property" not in source
    assert "def __init__" in source and "self._name = " in source


def test_only_the_accessors_that_move_the_field_are_properties(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "C.lvclass").write_text(_LVCLASS, encoding="utf-8")
    lvclass = parse_lvclass(tmp_path / "C.lvclass")
    lvclass.private_data_fields = [
        LVPrivateDataField(name="Count", python_type="int"),
        LVPrivateDataField(name="Name", python_type="int"),
    ]
    graph = InMemoryVIGraph()
    monkeypatch.setattr(graph, "get_type_fields", _fields)
    read, read_nodes = _read("Read Count", 0)
    build_graph(read, read.name, read_nodes, graph=graph)
    # The setter's value goes nowhere near a Bundle By Name.
    write, _ = _write("Write Count", 0)
    build_graph(write, write.name, [], graph=graph)
    module = ClassBuilder().build_class_module(
        lvclass,
        method_contexts={"Read Count": read, "Write Count": write},
        graph=graph,
    )
    ast.fix_missing_locations(module)
    source = ast.unparse(module)
    assert source.count("@property") == 1
    assert ".setter" not in source
    assert "def write_count(self, value" in source
