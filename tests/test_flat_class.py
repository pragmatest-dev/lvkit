"""Flatten To String and Unflatten From String of a LabVIEW class object.

Each case is a handmade project of classes (``tests/class_graphs.py``) with one VI
that flattens the object on its input and unflattens the string back; the package
is generated, imported and run. The bytes are NI's "Flattened Data" layout for a
class (``codegen/classes_module.py``), which the parser also reads
(``parser.vi.read_class_object``): the worked two-level ``Alpha.lvclass`` string of
labviewwiki.org, "LabVIEW Object", is checked byte for byte."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import Constant, GraphNode, PrimitiveNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.parser.vi import read_class_object
from tests.class_graphs import INT, Project, Recipe, class_type, pane, wire

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
U16 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt16")
VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
PICTURE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Picture")
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(
            name="status", type=LVType(LVTypeKind.PRIMITIVE, underlying_type="Boolean")
        ),
        ClusterField(name="code", type=INT),
        ClusterField(name="source", type=STRING),
    ],
)
BASE, CHILD = class_type("Base"), class_type("Child")
LABVIEW_OBJECT = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="LabVIEW Object",
)


def _node(vi: str, tag: str, prim_id: int, terminals: list[tuple]) -> PrimitiveNode:
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name=tag,
        prim_id=prim_id,
        node_type="prim",
        terminals=[
            Terminal(id=f"{vi}:{tag}.{i}", index=i, direction=d, lv_type=t)
            for i, d, t in terminals
        ],
    )


def round_trip(declared: LVType, order: int | None = None) -> Recipe:
    """A VI whose ``obj`` is flattened to ``data`` and unflattened to ``back``;
    ``order`` is the ``byte order`` constant (0, 1, 2) wired to both nodes."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        flatten = [(11, "input", declared), (3, "output", STRING)]
        unflatten = [(11, "input", STRING), (2, "output", declared)]
        flows = [
            wire(vi, "obj", "fl.11"),
            wire(vi, "fl.3", "data"),
            wire(vi, "fl.3", "un.11"),
            wire(vi, "un.2", "back"),
        ]
        constants = []
        if order is not None:
            flatten.append((9, "input", U16))
            unflatten.append((9, "input", U16))
            constants = [Constant(id=f"{vi}:c9", value=order, lv_type=U16)]
            flows += [wire(vi, "c9", "fl.9"), wire(vi, "c9", "un.9")]
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", declared, "obj", 0)],
                outputs=[
                    pane(vi, "output", "data", STRING, "data", 1),
                    pane(vi, "output", "back", declared, "back", 2),
                ],
                constants=constants,
                data_flow=flows,
            ),
            [
                _node(vi, "fl", 1164, flatten),
                _node(vi, "un", 1165, unflatten),
            ],
        )

    return build


def unflatten_only(declared: LVType) -> Recipe:
    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "data", STRING, "data", 0)],
                outputs=[
                    pane(vi, "output", "back", declared, "back", 1),
                    pane(vi, "output", "error_out", ERROR, "error out", 2),
                ],
                data_flow=[
                    wire(vi, "data", "un.11"),
                    wire(vi, "un.2", "back"),
                    wire(vi, "un.0", "error_out"),
                ],
            ),
            [
                _node(
                    vi,
                    "un",
                    1165,
                    [
                        (11, "input", STRING),
                        (2, "output", declared),
                        (0, "output", ERROR),
                    ],
                )
            ],
        )

    return build


def classes(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_class(
        "Base",
        [ClusterField(name="Count", type=INT), ClusterField(name="Name", type=STRING)],
    )
    project.add_class(
        "Child", [ClusterField(name="Extra", type=INT)], parent="Base"
    )
    return project


def test_a_class_with_a_field_that_has_no_flattened_form_is_a_gap(
    tmp_path: Path,
) -> None:
    """A Variant has no flattened form in lvkit, so a class that holds one is a
    gap of the node, naming the class and the field, never an lvkit error."""
    project = Project(tmp_path)
    variant = VARIANT
    project.add_class("Base", [ClusterField(name="Drawing", type=variant)])
    project.add_vi("RoundTrip", round_trip(BASE))
    gaps: list[Gap] = []
    project.generate(gaps)
    assert len(gaps) == 2
    assert all("'Drawing' of the class Base.lvclass" in g.reason for g in gaps)
    assert all("LVVariant" in g.reason for g in gaps)


def test_a_class_holding_a_class_object_flattens_it_inside_its_data(
    tmp_path: Path,
) -> None:
    """A class held in a field is a flattened object inside the cluster of the
    field, written by the same functions."""
    project = Project(tmp_path)
    project.add_class("Item", [ClusterField(name="Value", type=INT)])
    project.add_class(
        "Holder", [ClusterField(name="Inner", type=class_type("Item"))]
    )
    project.add_vi("RoundTrip", round_trip(class_type("Holder")))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        outer, inner = module("holder").Holder(), module("item").Item()
        inner._Item__value = 2
        outer._Holder__inner = inner
        back = module("roundtrip").roundtrip(obj=outer).back
        assert type(back._Holder__inner) is module("item").Item
        assert back._Holder__inner._Item__value == 2


def pascal_name(*parts: str) -> bytes:
    """The class name section: a length byte, Pascal strings, a zero, padding."""
    body = b"".join(bytes([len(p)]) + p.encode() for p in parts) + b"\x00"
    section = bytes([len(body)]) + body
    return section + b"\x00" * (-len(section) % 4)


def test_a_class_with_fields_round_trips_in_nis_layout(tmp_path: Path) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    assert package.gaps == []
    assert "lvkit" not in package.sources["roundtrip"]
    with package.use() as module:
        obj = module("base").Base()
        obj._Base__count = 7
        obj._Base__name = "hello"
        result = module("roundtrip").roundtrip(obj=obj)
        private = struct.pack(">i", 7) + struct.pack(">i", 5) + b"hello"
        assert result.data.encode("latin-1") == (
            struct.pack(">I", 1)
            + pascal_name("Base.lvclass")
            + struct.pack(">HHHH", 1, 0, 0, 0)
            + struct.pack(">i", len(private))
            + private
        )
        assert type(result.back) is module("base").Base
        assert (result.back._Base__count, result.back._Base__name) == (7, "hello")


@pytest.mark.parametrize(
    ("order", "prefix"), [(0, ">"), (1, "="), (2, "<")]
)
def test_a_class_flattens_in_the_byte_order_of_the_node(
    tmp_path: Path, order: int, prefix: str
) -> None:
    """NI, "Flatten To String", byte order: 0 "big-endian, network order", 1
    "native, host order", 2 "little-endian". Every number of the object (the levels,
    the versions, the size of the private data, its fields) is in that order, and
    the same object reads back."""
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE, order))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("base").Base()
        obj._Base__count = 7
        obj._Base__name = "hello"
        result = module("roundtrip").roundtrip(obj=obj)
        private = struct.pack(prefix + "i", 7) + struct.pack(prefix + "i", 5) + b"hello"
        assert result.data.encode("latin-1") == (
            struct.pack(prefix + "I", 1)
            + pascal_name("Base.lvclass")
            + struct.pack(prefix + "HHHH", 1, 0, 0, 0)
            + struct.pack(prefix + "i", len(private))
            + private
        )
        assert (result.back._Base__count, result.back._Base__name) == (7, "hello")


def test_a_child_class_writes_every_level_oldest_ancestor_first(
    tmp_path: Path,
) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    with package.use() as module:
        obj = module("child").Child()
        obj._Base__count, obj._Base__name, obj._Child__extra = 3, "x", 9
        result = module("roundtrip").roundtrip(obj=obj)
        data = result.data.encode("latin-1")
        # level in hierarchy 2, then the child's name and one version per level,
        # the child's first
        assert data[:4] == struct.pack(">I", 2)
        name = pascal_name("Child.lvclass")
        assert data[4 : 4 + len(name)] == name
        versions = struct.pack(">HHHH", 1, 0, 0, 0) * 2
        start = 4 + len(name)
        assert data[start : start + 16] == versions
        # Base's cluster (a 32-bit integer and a string) comes before Child's.
        base_part = struct.pack(">i", 3) + struct.pack(">i", 1) + b"x"
        child_part = struct.pack(">i", 9)
        assert data[start + 16 :] == (
            struct.pack(">i", len(base_part))
            + base_part
            + struct.pack(">i", len(child_part))
            + child_part
        )
        extent = read_class_object(data)
        assert extent is not None and extent.size == len(data) and extent.level == 2
        assert type(result.back) is module("child").Child
        assert (
            result.back._Base__count,
            result.back._Base__name,
            result.back._Child__extra,
        ) == (3, "x", 9)


def test_only_an_unflatten_of_a_class_catches_the_class_failure(
    tmp_path: Path,
) -> None:
    project = classes(tmp_path)
    project.add_vi("OfNumber", unflatten_only(INT))
    project.add_vi("OfClass", unflatten_only(BASE))
    package = project.generate()
    assert "FlattenedClassError" not in package.sources["ofnumber"]
    assert "FlattenedClassError" in package.sources["ofclass"]


def test_labview_object_flattens_as_level_zero(tmp_path: Path) -> None:
    project = classes(tmp_path)
    project.add_vi("RoundTrip", round_trip(LABVIEW_OBJECT))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        result = module("roundtrip").roundtrip(obj=object())
        assert result.data == "\x00\x00\x00\x00"
        assert type(result.back) is object
        # A class on a LabVIEW Object wire keeps its own class.
        obj = module("base").Base()
        again = module("roundtrip").roundtrip(obj=obj)
        assert type(again.back) is module("base").Base


def test_the_default_form_is_a_default_object(tmp_path: Path) -> None:
    """NI: at level 1 with version 0 "the flattened data represents the default
    data of the class, and LabVIEW excludes the remaining field"."""
    project = classes(tmp_path)
    project.add_vi("Read", unflatten_only(BASE))
    package = project.generate()
    default = struct.pack(">I", 1) + pascal_name("Base.lvclass") + b"\x00" * 8
    with package.use() as module:
        back = module("read").read(data=default.decode("latin-1")).back
        assert type(back) is module("base").Base
        assert back._Base__count == 0 and back._Base__name == ""


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (struct.pack(">I", 1) + pascal_name("Other.lvclass") + b"\x00" * 8, 1527),
        (
            struct.pack(">I", 1)
            + pascal_name("Base.lvclass")
            + struct.pack(">HHHH", 2, 0, 0, 0),
            1401,
        ),
        (struct.pack(">I", 3) + pascal_name("Base.lvclass") + b"\x00" * 24, 1403),
        (struct.pack(">I", 1) + b"\x05", 1403),
    ],
)
def test_data_that_is_not_a_class_of_the_package_is_nis_error(
    tmp_path: Path, raw: bytes, code: int
) -> None:
    """NI, "LabVIEW Object-Oriented Programming Error Codes": 1527 a class "not
    currently loaded", 1401 data of a newer version than the class, 1403 data that
    is "corrupt"."""
    project = classes(tmp_path)
    project.add_vi("Read", unflatten_only(BASE))
    package = project.generate()
    with package.use() as module:
        result = module("read").read(data=raw.decode("latin-1"))
        assert result.error_out.code == code
        # the value output is the class default, as for any failed node
        assert type(result.back) is module("base").Base


def _with_variant_class(tmp_path: Path) -> Project:
    project = classes(tmp_path)
    variant = VARIANT
    project.add_class("Bad", [ClusterField(name="Drawing", type=variant)])
    return project


def test_a_labview_object_wire_works_for_classes_the_package_can_flatten(
    tmp_path: Path,
) -> None:
    """A class with a field that has no flattened form is not a gap of a LabVIEW
    Object wire; the classes with plain fields still round trip."""
    project = _with_variant_class(tmp_path)
    project.add_vi("RoundTrip", round_trip(LABVIEW_OBJECT))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("base").Base()
        obj._Base__count = 4
        back = module("roundtrip").roundtrip(obj=obj).back
        assert type(back) is module("base").Base and back._Base__count == 4


def test_a_labview_object_holding_such_a_class_raises_naming_the_field(
    tmp_path: Path,
) -> None:
    project = _with_variant_class(tmp_path)
    project.add_vi("RoundTrip", round_trip(LABVIEW_OBJECT))
    project.add_vi("Read", unflatten_only(LABVIEW_OBJECT))
    package = project.generate()
    with package.use() as module:
        with pytest.raises(Exception) as written:
            module("roundtrip").roundtrip(obj=module("bad").Bad())
        assert "'Drawing' of the class Bad.lvclass" in str(written.value)
        raw = (
            struct.pack(">I", 1)
            + pascal_name("Bad.lvclass")
            + struct.pack(">HHHH", 1, 0, 0, 0)
            + struct.pack(">i", 4)
            + b"\x00" * 4
        )
        error = module("read").read(data=raw.decode("latin-1")).error_out
        assert error.code == 1403
        assert "'Drawing' of the class Bad.lvclass" in error.message
        assert "at byte" in error.message


def test_a_specific_class_whose_descendant_has_such_a_field_still_generates(
    tmp_path: Path,
) -> None:
    """Only the class the wire names and its ancestors must be readable."""
    project = classes(tmp_path)
    variant = VARIANT
    project.add_class(
        "Drawn", [ClusterField(name="Drawing", type=variant)], parent="Base"
    )
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    assert package.gaps == []


_WIKI_ALPHA = bytes.fromhex(
    "00000002 2111 416C70686120 4163746F722E6C766C6962 0D 416C7068612E6C76636C617373"
    " 00 0000 0001000000000001 0001000000000005"
    " 00000000 00000014 00000000 0000000C 48656C6C6F20576F726C6421".replace(" ", "")
)


def test_the_wikis_two_level_string_is_read_as_the_layout_says() -> None:
    """labviewwiki.org, "LabVIEW Object": ``Alpha.lvclass`` (in ``Alpha
    Actor.lvlib``) is a child of ``Actor.lvclass``; its private data is a Notifier
    refnum and the string ``Hello World!``, and ``Actor.lvclass`` is default."""
    assert pascal_name("Alpha Actor.lvlib", "Alpha.lvclass") == _WIKI_ALPHA[4:40]
    extent = read_class_object(_WIKI_ALPHA)
    assert extent is not None and extent.size == len(_WIKI_ALPHA)
    assert extent.level == 2
    assert [end - start for start, end in extent.private] == [0, 20]
