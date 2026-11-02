"""A class property is the VIs a ``.lvclass`` lists in a ``Property Definition``
folder: a Python property named by its long name and spelled by the access level
of its VIs, its getter and setter written from their own diagrams, over the
private storage ``__<field>``; a VI outside every folder is a method. Nothing is
decided by a VI's name."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lvkit.class_property import PropertyRole, property_role
from lvkit.codegen import build_module
from lvkit.codegen.class_attributes import ClassAttributes
from lvkit.codegen.class_model import class_model
from lvkit.codegen.errors import CodeGenError
from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.membership import ListedMethod
from lvkit.graph.models import PrimitiveNode, VIContext, VINode, Wire
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.pipeline import create_import_resolver
from lvkit.structure import LVClass, LVPrivateDataField, parse_lvclass
from tests.helpers import build_graph, key_graph_types, with_file_keys

_CLASS_TYPE = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="C.lvclass",
)
_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_FIELDS = [ClusterField(name="Count", type=_INT), ClusterField(name="Name", type=_INT)]

_PUBLIC, _PRIVATE, _PROTECTED, _COMMUNITY = 1, 2, 3, 4


def _is_c(terminal: Terminal) -> bool:
    classname = terminal.lv_type.classname if terminal.lv_type else None
    return classname is not None and classname.rsplit(":", 1)[-1] == "C.lvclass"


def _item(vi: str, scope: int, indent: str = "\t\t\t") -> str:
    return (
        f'{indent}<Item Name="{vi}.vi" Type="VI" URL="../Accessor/{vi}.vi">\n'
        f'{indent}\t<Property Name="NI.ClassItem.MethodScope" Type="UInt">'
        f"{scope}</Property>\n{indent}</Item>\n"
    )


def _lvclass(
    properties: list[tuple[str, str, list[tuple[str, int]]]],
    methods: list[tuple[str, int]],
) -> str:
    """A class file: ``properties`` are (folder, long name, [(vi, scope)])."""
    folders = ""
    for folder, long_name, vis in properties:
        folders += (
            f'\t\t<Item Name="{folder}" Type="Property Definition">\n'
            '\t\t\t<Property Name="NI.ClassItem.Property.LongName" Type="Str">'
            f"{long_name}</Property>\n"
            + "".join(_item(vi, scope) for vi, scope in vis)
            + "\t\t</Item>\n"
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<LVClass LVVersion="25008000">\n'
        '\t<Item Name="C.ctl" Type="Class Private Data" URL="C.ctl"/>\n'
        f'\t<Item Name="Accessor" Type="Folder">\n{folders}\t</Item>\n'
        + "".join(_item(vi, scope, "\t") for vi, scope in methods)
        + "</LVClass>\n"
    )


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
    path.write_text(
        _lvclass(
            [
                ("Count", "Item Count", [("Read Count", 1), ("Write Count", 1)]),
                ("Name", "Label", [("Read Name", 3)]),
            ],
            [("Get Config", 1)],
        ),
        encoding="utf-8",
    )
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
    assert property_role([self_in], [self_out, value_out], _is_c) is PropertyRole.GETTER
    assert property_role([self_in, value_in], [self_out], _is_c) is PropertyRole.SETTER
    assert property_role([self_in], [self_out], _is_c) is None
    assert property_role([self_in], [value_out, value_out], _is_c) is None


# --- a handmade class: its VIs are built from nodes -------------------------------

Spec = Callable[[str], tuple[VIContext, list[PrimitiveNode]]]


def _mux(name: str, role: str, field_index: int) -> PrimitiveNode:
    """The Bundle (``role`` input) or Unbundle (output) By Name of one field."""
    bundle = role == "input"
    terminals = [
        Terminal(
            id=f"{name}:agg",
            index=0,
            direction="input",
            lv_type=_CLASS_TYPE,
            nmux_role="agg",
        ),
        Terminal(
            id=f"{name}:field",
            index=2 if bundle else 1,
            direction=role,
            lv_type=_INT,
            nmux_role="list",
            nmux_field_index=field_index,
        ),
    ]
    if bundle:
        terminals.append(
            Terminal(
                id=f"{name}:agg_out",
                index=1,
                direction="output",
                lv_type=_CLASS_TYPE,
                nmux_role="agg",
            )
        )
    return PrimitiveNode(
        id=f"{name}:mux", vi_path=name, node_type="nMux", terminals=terminals
    )


def _double(name: str) -> PrimitiveNode:
    """Add: twice its one input (the logic of an accessor)."""
    return PrimitiveNode(
        id=f"{name}:add",
        vi_path=name,
        name="Add",
        prim_id=1050,
        terminals=[
            Terminal(id=f"{name}:add_y", index=1, direction="input", lv_type=_INT),
            Terminal(id=f"{name}:add_x", index=2, direction="input", lv_type=_INT),
            Terminal(id=f"{name}:add_sum", index=0, direction="output", lv_type=_INT),
        ],
    )


def reader(field_index: int, doubled: bool = False, extra_input: bool = False) -> Spec:
    """A VI that reads one field (twice its value when ``doubled``)."""

    def build(name: str) -> tuple[VIContext, list[PrimitiveNode]]:
        inputs = [_terminal(f"{name}:in", "input", _CLASS_TYPE, "C", 0)]
        if extra_input:
            inputs.append(_terminal(f"{name}:x", "input", _INT, "x", 3))
        nodes = [_mux(name, "output", field_index)]
        wires = [
            Wire.from_terminals(f"{name}:in", f"{name}:agg"),
            Wire.from_terminals(f"{name}:in", f"{name}:cout"),
        ]
        if doubled:
            nodes.append(_double(name))
            wires += [
                Wire.from_terminals(f"{name}:field", f"{name}:add_x"),
                Wire.from_terminals(f"{name}:field", f"{name}:add_y"),
                Wire.from_terminals(f"{name}:add_sum", f"{name}:out"),
            ]
        else:
            wires.append(Wire.from_terminals(f"{name}:field", f"{name}:out"))
        return (
            VIContext(
                name=name,
                inputs=inputs,
                outputs=[
                    _terminal(f"{name}:cout", "output", _CLASS_TYPE, "C out", 1),
                    _terminal(f"{name}:out", "output", _INT, "value", 2),
                ],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def writer(field_index: int, doubled: bool = False) -> Spec:
    """A VI that writes one field (twice the value when ``doubled``)."""

    def build(name: str) -> tuple[VIContext, list[PrimitiveNode]]:
        nodes = [_mux(name, "input", field_index)]
        wires = [
            Wire.from_terminals(f"{name}:agg_out", f"{name}:cout"),
            Wire.from_terminals(f"{name}:in", f"{name}:agg"),
        ]
        if doubled:
            nodes.append(_double(name))
            wires += [
                Wire.from_terminals(f"{name}:value", f"{name}:add_x"),
                Wire.from_terminals(f"{name}:value", f"{name}:add_y"),
                Wire.from_terminals(f"{name}:add_sum", f"{name}:field"),
            ]
        else:
            wires.append(Wire.from_terminals(f"{name}:value", f"{name}:field"))
        return (
            VIContext(
                name=name,
                inputs=[
                    _terminal(f"{name}:in", "input", _CLASS_TYPE, "C", 0),
                    _terminal(f"{name}:value", "input", _INT, "value", 2),
                ],
                outputs=[_terminal(f"{name}:cout", "output", _CLASS_TYPE, "C out", 1)],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def passthrough(name: str) -> tuple[VIContext, list[PrimitiveNode]]:
    return (
        VIContext(
            name=name,
            inputs=[_terminal(f"{name}:in", "input", _CLASS_TYPE, "C", 0)],
            outputs=[_terminal(f"{name}:cout", "output", _CLASS_TYPE, "C out", 1)],
            data_flow=[Wire.from_terminals(f"{name}:in", f"{name}:cout")],
        ),
        [],
    )


@dataclass
class Built:
    graph: InMemoryVIGraph
    lvclass: LVClass
    class_key: str
    keys: dict[str, str]
    contexts: dict[str, VIContext]

    def source(self) -> str:
        out = Path("/pkg")
        resolver = create_import_resolver(
            "pkg",
            out,
            {self.class_key: out / "c.py"},
            out / "c.py",
            caller_key=self.class_key,
            graph=self.graph,
        )
        return build_type_module(self.graph, self.class_key, resolver)

    def instance(self) -> Any:
        namespace: dict[str, Any] = {}
        exec(compile(self.source(), "<class>", "exec"), namespace)  # noqa: S102
        return namespace["C"]()


def build_class(
    tmp_path: Path, xml: str, specs: dict[str, Spec], fields: list[ClusterField]
) -> Built:
    """The class file ``xml`` as the loader makes it: a class node, a node per
    VI with its pane, and the members the class file lists (they link as
    ownership edges)."""
    path = tmp_path / "C.lvclass"
    path.write_text(xml, encoding="utf-8")
    lvclass = parse_lvclass(path)
    lvclass.private_data_fields = [
        LVPrivateDataField(name=f.name, python_type="int") for f in fields
    ]
    graph = InMemoryVIGraph()
    class_key = str(path.resolve())
    keys = {m.name: str(tmp_path / Path(m.vi_path).name) for m in lvclass.methods}
    graph._dep_graph.add_node(  # noqa: SLF001
        class_key,
        node_type=NodeType.CLASS,
        fields=fields,
        parent_class="LabVIEW Object",
        parent_key=None,
        name="C.lvclass",
        qname="C.lvclass",
        method_paths={Path(m.vi_path).name: keys[m.name] for m in lvclass.methods},
    )
    graph._vi_display_names[class_key] = "C.lvclass"  # noqa: SLF001
    contexts: dict[str, VIContext] = {}
    listed: dict[str, ListedMethod | None] = {}
    for method in lvclass.methods:
        key = keys[method.name]
        context, nodes = specs.get(method.name, passthrough)(key)
        build_graph(context, key, nodes, graph=graph)
        graph._graph.add_node(  # noqa: SLF001
            key,
            node=VINode(
                id=key,
                vi_path=key,
                name=method.name,
                terminals=[*context.inputs, *context.outputs],
            ),
        )
        graph._dep_graph.add_node(key)  # noqa: SLF001
        listed[key] = ListedMethod.from_item(method)
        contexts[method.name] = context
    graph._record_members(class_key, listed)  # noqa: SLF001
    key_graph_types(graph, {"C.lvclass": class_key, "Lib.lvlib:C.lvclass": class_key})
    return Built(graph, lvclass, class_key, keys, contexts)


def _count_class(
    tmp_path: Path,
    read: Spec | None,
    write: Spec | None,
    scope: int = _PUBLIC,
    write_scope: int | None = None,
) -> Built:
    """The class ``C`` with fields Count and Name, the property ``Count`` (its
    read and write VIs of ``scope``, the write VI of ``write_scope`` when given)
    and the methods Peek (reads Count) and Get Config."""
    vis = [("Read Count", scope)] if read else []
    if write:
        vis.append(("Write Count", scope if write_scope is None else write_scope))
    xml = _lvclass([("Count", "Count", vis)], [("Peek", _PUBLIC), ("Get Config", 1)])
    specs: dict[str, Spec] = {"Peek": reader(0), "Get Config": passthrough}
    if read:
        specs["Read Count"] = read
    if write:
        specs["Write Count"] = write
    return build_class(tmp_path, xml, specs, _FIELDS)


def _attributes(built: Built) -> ClassAttributes:
    return class_model(built.graph, built.class_key).attributes


# (a) a public read and write VI: the property `count` over the storage `__count`
def test_public_accessors_are_a_public_property_over_the_private_field(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    attributes = _attributes(built)
    assert attributes.fields == ["__count", "__name"]
    assert {a.python_name for a in attributes.accessors.values()} == {"count"}
    source = built.source()
    assert source.count("@property") == 1 and "@count.setter" in source
    assert "def read_count" not in source and "def write_count" not in source
    assert "self.__count = 0" in source and "return self.__count" in source
    obj = built.instance()
    assert sorted(vars(obj)) == ["_C__count", "_C__name"]
    assert obj.count == 0
    obj.count = 7
    assert obj._C__count == 7
    # A method of the class reads the field, not the property.
    assert obj.peek() == 7


def _call(
    name: str, key: str, inputs: list[Terminal], outputs: list[Terminal]
) -> VINode:
    return VINode(
        id="call",
        vi_path="caller",
        name=name,
        node_type="iUse",
        callee_key=key,
        terminals=[*inputs, *outputs],
    )


def _reading_caller(built: Built) -> VIContext:
    """A VI of no class that reads the property Count of its ``obj``."""
    read_call = _call(
        "Read Count.vi",
        built.keys["Read Count"],
        [Terminal(id="c:in", index=0, direction="input", lv_type=_CLASS_TYPE)],
        [
            Terminal(id="c:cout", index=1, direction="output", lv_type=_CLASS_TYPE),
            Terminal(id="c:val", index=2, direction="output", lv_type=_INT),
        ],
    )
    reading = VIContext(
        name="caller",
        inputs=[_terminal("obj", "input", _CLASS_TYPE, "obj", 0)],
        outputs=[_terminal("result", "output", _INT, "result", 1)],
        data_flow=[
            Wire.from_terminals("obj", "c:in"),
            Wire.from_terminals("c:val", "result"),
        ],
    )
    build_graph(reading, "caller", [read_call], graph=built.graph)
    return reading


def test_a_call_of_a_read_vi_reads_the_property(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    code = build_module(_reading_caller(built), "caller", graph=built.graph)
    assert "obj.count" in code
    assert "read_count" not in code
    namespace: dict[str, Any] = {}
    exec(compile(code, "<caller>", "exec"), namespace)  # noqa: S102
    obj = built.instance()
    obj.count = 9
    assert namespace["caller"](obj).result == 9


def test_a_call_of_a_write_vi_writes_the_property(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    write_call = _call(
        "Write Count.vi",
        built.keys["Write Count"],
        [
            Terminal(id="w:in", index=0, direction="input", lv_type=_CLASS_TYPE),
            Terminal(id="w:value", index=2, direction="input", lv_type=_INT),
        ],
        [Terminal(id="w:cout", index=1, direction="output", lv_type=_CLASS_TYPE)],
    )
    writing = VIContext(
        name="caller2",
        inputs=[
            _terminal("obj2", "input", _CLASS_TYPE, "obj", 0),
            _terminal("v", "input", _INT, "v", 2),
        ],
        outputs=[_terminal("obj_out", "output", _CLASS_TYPE, "obj out", 1)],
        data_flow=[
            Wire.from_terminals("obj2", "w:in"),
            Wire.from_terminals("v", "w:value"),
            Wire.from_terminals("w:cout", "obj_out"),
        ],
    )
    write_call.vi_path = "caller2"
    build_graph(writing, "caller2", [write_call], graph=built.graph)
    code = build_module(writing, "caller2", graph=built.graph)
    assert "obj.count = v" in code
    namespace: dict[str, Any] = {}
    exec(compile(code, "<caller2>", "exec"), namespace)  # noqa: S102
    obj = built.instance()
    namespace["caller2"](obj, 5)
    assert obj.count == 5


def test_a_call_of_a_read_vi_with_logic_runs_its_diagram(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0, doubled=True), writer(0, doubled=True))
    code = build_module(_reading_caller(built), "caller", graph=built.graph)
    assert "obj.count" in code and "read_count" not in code
    namespace: dict[str, Any] = {}
    exec(compile(code, "<caller>", "exec"), namespace)  # noqa: S102
    obj = built.instance()
    obj.count = 2
    assert namespace["caller"](obj).result == 8


# (b) the access level of the accessor VIs spells the property
@pytest.mark.parametrize(
    ("scope", "spelled"),
    [(_PUBLIC, "count"), (_PROTECTED, "_count"), (_COMMUNITY, "_count")],
)
def test_the_access_level_of_the_accessors_spells_the_property(
    tmp_path: Path, scope: int, spelled: str
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0), scope)
    attributes = _attributes(built)
    assert attributes.fields == ["__count", "__name"]
    assert {a.python_name for a in attributes.accessors.values()} == {spelled}
    obj = built.instance()
    setattr(obj, spelled, 3)
    assert getattr(obj, spelled) == 3 and obj.peek() == 3


def test_a_private_property_is_spelled_private_and_its_storage_kept_apart(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0), _PRIVATE)
    attributes = _attributes(built)
    # The property is `__count`, so the field it would share the name with is
    # stored as `__count_value`.
    assert {a.python_name for a in attributes.accessors.values()} == {"__count"}
    assert attributes.fields == ["__count_value", "__name"]
    source = built.source()
    assert "def __count(self)" in source and "@__count.setter" in source
    assert "return self.__count_value" in source
    obj = built.instance()
    obj._C__count = 4
    assert obj._C__count == 4 and obj._C__count_value == 4 and obj.peek() == 4


def test_a_private_property_read_outside_its_class_is_an_lvkit_error(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0), _PRIVATE)
    with pytest.raises(CodeGenError, match="private member '__count'"):
        build_module(_reading_caller(built), "caller", graph=built.graph)


def test_read_and_write_vis_of_two_levels_are_spelled_by_the_wider(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0), _PUBLIC, _PRIVATE)
    assert {a.python_name for a in _attributes(built).accessors.values()} == {"count"}
    (tmp_path / "other").mkdir()
    other = _count_class(tmp_path / "other", reader(0), writer(0), _PRIVATE, _COMMUNITY)
    assert {a.python_name for a in _attributes(other).accessors.values()} == {"_count"}
    obj = other.instance()
    obj._count = 6
    assert obj._count == 6


# (c) a read VI alone is a read-only property; a write VI alone a write-only one
def test_a_read_vi_alone_is_a_property_with_no_setter(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), None)
    source = built.source()
    assert "@property" in source and ".setter" not in source
    obj = built.instance()
    assert obj.count == 0
    with pytest.raises(AttributeError):
        obj.count = 1


def test_a_write_vi_alone_is_a_property_with_no_getter(tmp_path: Path) -> None:
    built = _count_class(tmp_path, None, writer(0))
    source = built.source()
    assert "@property().setter\n    def count(self, value" in source
    obj = built.instance()
    obj.count = 5
    assert obj.peek() == 5
    with pytest.raises(AttributeError):
        _ = obj.count


# (d) accessors with logic are written from their diagrams
def test_accessors_with_logic_are_a_property_written_from_their_diagrams(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0, doubled=True), writer(0, doubled=True))
    source = built.source()
    assert source.count("@property") == 1 and "@count.setter" in source
    obj = built.instance()
    obj.count = 4
    assert obj._C__count == 8
    assert obj.count == 16
    assert obj.peek() == 8


def test_a_pair_with_one_side_of_logic_is_one_property(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), writer(0, doubled=True))
    obj = built.instance()
    obj.count = 4
    assert obj._C__count == 8 and obj.count == 8

    (tmp_path / "other").mkdir()
    other = _count_class(tmp_path / "other", reader(0, doubled=True), writer(0))
    obj = other.instance()
    obj.count = 4
    assert obj._C__count == 4 and obj.count == 8


# (e) a VI outside every folder is a method, whatever its name
def test_a_vi_outside_every_property_folder_is_a_method(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    obj = built.instance()
    assert callable(obj.get_config) and callable(obj.peek)
    assert not isinstance(type(obj).__dict__.get("get_config"), property)


# (f) a field no property reads is stored the same way
def test_a_field_without_an_accessor_is_private_storage(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    assert built.instance()._C__name == 0
    assert "self.__name = 0" in built.source()


def test_an_accessor_with_an_extra_input_is_a_method(tmp_path: Path) -> None:
    built = _count_class(tmp_path, reader(0, extra_input=True), writer(0))
    attributes = _attributes(built)
    assert built.keys["Read Count"] not in attributes.accessors
    source = built.source()
    assert "def read_count(self, x" in source
    # The write VI alone is the property.
    assert "@property().setter" in source


def test_two_properties_over_one_field_are_two_properties(tmp_path: Path) -> None:
    xml = _lvclass(
        [
            ("Count", "Count", [("Read Count", 1), ("Write Count", 1)]),
            ("Total", "Total", [("Read Total", 1)]),
        ],
        [],
    )
    built = build_class(
        tmp_path,
        xml,
        {"Read Count": reader(0), "Write Count": writer(0), "Read Total": reader(0)},
        _FIELDS,
    )
    attributes = _attributes(built)
    assert attributes.fields == ["__count", "__name"]
    names = {a.property_name: a.python_name for a in attributes.accessors.values()}
    assert names == {"Count": "count", "Total": "total"}
    obj = built.instance()
    obj.count = 2
    assert obj.total == 2


def test_a_class_with_no_fields_has_an_empty_init(tmp_path: Path) -> None:
    xml = _lvclass([], [("Get Config", 1)])
    built = build_class(tmp_path, xml, {"Get Config": passthrough}, [])
    assert _attributes(built).fields == []
    obj = built.instance()
    assert vars(obj) == {}
    obj.get_config()


# --- a class owned by a library ----------------------------------------------------


def test_a_library_qualified_class_name_finds_the_class_its_vi_belongs_to(
    tmp_path: Path,
) -> None:
    built = _count_class(tmp_path, reader(0), writer(0))
    qualified = "Lib.lvlib:C.lvclass"
    lv_type = with_file_keys(
        dataclasses.replace(_CLASS_TYPE, classname=qualified),
        {qualified: built.class_key},
    )
    fields = built.graph.get_type_fields(lv_type)
    assert fields is not None and [f.name for f in fields] == ["Count", "Name"]
    # A type no record tied to a class file has no fields, whatever its name.
    untied = dataclasses.replace(_CLASS_TYPE, classname=qualified)
    assert built.graph.get_type_fields(untied) is None


def test_bundle_and_unbundle_on_a_library_owned_class_use_its_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qualified = dataclasses.replace(_CLASS_TYPE, classname="Lib.lvlib:C.lvclass")
    monkeypatch.setattr("tests.test_class_properties._CLASS_TYPE", qualified)
    built = _count_class(tmp_path, reader(0), writer(0))
    assert _attributes(built).fields == ["__count", "__name"]
    source = built.source()
    assert "return self.__count" in source
    obj = built.instance()
    obj.count = 4
    assert obj.peek() == 4
