"""A cluster or array constant is written field by field from its type and its
decoded value, never as a string of its display text."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import hightime
import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.construction import decode_constant
from lvkit.graph.models import ConstantNode, VIContext
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    VariantData,
    plain_structured,
)
from lvkit.parser.models import ParsedConstant
from lvkit.parser.vi import _decode_element
from lvkit.timestamp_value import EPOCH_1904, TIMESTAMP_FLAVOR
from tests.class_graphs import Project, class_type, pane, wire
from tests.helpers import generated_variants

_EMPTY_VARIANT = bytes.fromhex("0900800000000001000400000001000000000000")
_I32_VARIANT = (
    bytes.fromhex("09008000")
    + (2).to_bytes(4, "big")
    + bytes.fromhex("00040000")  # type 0: Void
    + bytes.fromhex("00040003")  # type 1: 32-bit integer
    + bytes.fromhex("0001")  # has a value
    + bytes.fromhex("0001")  # of type 1
    + (5).to_bytes(4, "big")
    + (0).to_bytes(4, "big")  # no attributes
)
_ONE_ATTRIBUTE = (
    _EMPTY_VARIANT[:-4]
    + (1).to_bytes(4, "big")
    + (1).to_bytes(4, "big")
    + b"a"
    + _EMPTY_VARIANT
)
_STRING_VARIANT = (
    bytes.fromhex("09008000")
    + (2).to_bytes(4, "big")
    + bytes.fromhex("00040000")  # type 0: Void
    + bytes.fromhex("00080030FFFFFFFF")  # type 1: string
    + bytes.fromhex("0001")  # has a value
    + bytes.fromhex("0001")  # of type 1
    + (1).to_bytes(4, "big")
    + b"x"
    + (0).to_bytes(4, "big")  # no attributes
)
_TWO_ATTRIBUTES = (
    _EMPTY_VARIANT[:-4]
    + (2).to_bytes(4, "big")
    + (1).to_bytes(4, "big")
    + b"a"
    + _I32_VARIANT[:-8]
    + (1).to_bytes(4, "big")
    + (0).to_bytes(4, "big")
    + (1).to_bytes(4, "big")
    + b"b"
    + _STRING_VARIANT
)

_VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_ROOT = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UDClassInst",
    classname="LabVIEW Object",
)
_TIMESTAMP = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor=TIMESTAMP_FLAVOR,
)


def _cluster(*fields: tuple[str, LVType], typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[ClusterField(name=n, type=t) for n, t in fields],
        typedef_name=typedef,
    )


def _array(element: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=element,
        dimensions=1,
    )


def _node(lv_type: LVType, data: bytes) -> ConstantNode:
    decoded = decode_constant(
        ParsedConstant(uid="c", type_desc="", value=data.hex()), lv_type=lv_type
    )
    return ConstantNode(
        id="c",
        vi_path="v.vi",
        value=decoded.value,
        lv_type=lv_type,
        unwritable=decoded.unwritable,
        empty_variant=decoded.empty_variant,
        structured_value=decoded.structured,
    )


def _expression(lv_type: LVType, data: bytes) -> str:
    return CodeGenContext().constant_expression(_node(lv_type, data))


def _string(text: str) -> bytes:
    return len(text).to_bytes(4, "big") + text.encode()


def _evaluate(expression: str) -> object:
    scope = {
        **vars(generated_variants()),
        "SimpleNamespace": SimpleNamespace,
        "hightime": hightime,
        "UTC": UTC,
    }
    return eval(expression, scope)  # noqa: S307


def test_a_cluster_of_a_string_and_an_empty_variant_is_a_tuple() -> None:
    lv_type = _cluster(("name", _STRING), ("Value", _VARIANT))
    expression = _expression(lv_type, _string("") + _EMPTY_VARIANT)
    assert expression == "('', Variant())"
    assert _evaluate(expression) == ("", generated_variants().Variant())


def test_a_typedef_cluster_with_a_class_object_field_is_a_namespace() -> None:
    lv_type = _cluster(
        ("test", _ROOT), ("resultStatus", _I32), typedef="TestResult.ctl"
    )
    expression = _expression(lv_type, bytes(4) + bytes(4))
    assert expression == "SimpleNamespace(test=object(), resultstatus=0)"
    value = _evaluate(expression)
    assert type(value.test) is object  # type: ignore[attr-defined]
    assert value.resultstatus == 0  # type: ignore[attr-defined]


def test_a_cluster_field_of_a_loaded_class_is_a_new_object_of_it(
    tmp_path: Path,
) -> None:
    from lvkit.graph.models import Constant, GraphNode

    owner = class_type("Base")
    lv_type = _cluster(("obj", owner), ("n", _I32), typedef="Holder.ctl")

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", lv_type, "out", 0)],
                constants=[
                    Constant(
                        id=f"{vi}:const",
                        value="{'obj': Base.lvclass, 'n': 3}",
                        lv_type=lv_type,
                        structured_value={"obj": "Base.lvclass", "n": "3"},
                    )
                ],
                data_flow=[wire(vi, "const", "out")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Base", [])
    project.add_vi("Build", build)
    package = project.generate()
    assert package.gaps == []
    assert "SimpleNamespace(obj=Base(), n=3)" in package.sources["build"]
    with package.use() as module:
        made = module("build").build().out
        assert isinstance(made.obj, module("base").Base)
        assert made.n == 3


def test_a_cluster_holding_a_variant_with_a_value_writes_the_variant() -> None:
    lv_type = _cluster(("v", _VARIANT), ("n", _I32))
    expression = _expression(lv_type, _I32_VARIANT + bytes(4))
    assert expression == "(Variant(5, I32, {}), 0)"
    variants = generated_variants()
    assert _evaluate(expression) == (variants.Variant(5, variants.I32), 0)


def test_an_empty_variant_with_attributes_in_a_cluster_is_a_variant_of_them() -> None:
    lv_type = _cluster(("v", _VARIANT), ("n", _I32))
    expression = _expression(lv_type, _ONE_ATTRIBUTE + bytes(4))
    assert expression == "(Variant(None, None, {'a': Variant()}), 0)"


def test_an_empty_variant_constant_with_attributes_is_a_variant_of_them() -> None:
    expression = _expression(_VARIANT, _TWO_ATTRIBUTES)
    assert expression == (
        "Variant(None, None, {'a': Variant(1, I32, {}), "
        "'b': Variant('x', String, {})})"
    )
    variants = generated_variants()
    assert _evaluate(expression) == variants.Variant(
        attributes={
            "a": variants.Variant(1, variants.I32),
            "b": variants.Variant("x", variants.String),
        }
    )


def test_an_empty_variant_constant_without_attributes_is_an_empty_variant() -> None:
    assert _expression(_VARIANT, _EMPTY_VARIANT) == "Variant()"


def test_a_variant_with_a_value_and_attributes_is_a_variant_of_both() -> None:
    data = _I32_VARIANT[:-4] + (1).to_bytes(4, "big") + _string("a") + _EMPTY_VARIANT
    expression = _expression(_VARIANT, data)
    assert expression == "Variant(5, I32, {'a': Variant()})"
    variants = generated_variants()
    assert _evaluate(expression) == variants.Variant(
        5, variants.I32, {"a": variants.Variant()}
    )


def test_the_display_text_of_a_variant_with_attributes_is_unchanged() -> None:
    node = _node(_VARIANT, _TWO_ATTRIBUTES)
    assert node.value == "Variant(attributes={'a': Variant(1), 'b': Variant('x')})"


def test_a_timestamp_constant_is_the_exact_hightime_datetime() -> None:
    data = (3_800_000_000).to_bytes(8, "big") + (2**63).to_bytes(8, "big")
    expression = _expression(_TIMESTAMP, data)
    assert expression == (
        "hightime.datetime(2024, 5, 31, 11, 33, 20, microsecond=500000, tzinfo=UTC)"
    )
    assert _evaluate(expression) == EPOCH_1904 + hightime.timedelta(
        seconds=3_800_000_000.5
    )


def test_a_cluster_with_a_timestamp_field_holds_the_datetime() -> None:
    lv_type = _cluster(("test", _ROOT), ("startTimestamp", _TIMESTAMP))
    expression = _expression(lv_type, bytes(4) + bytes(16))
    assert (
        expression == "(object(), hightime.datetime(1904, 1, 1, 0, 0, 0, tzinfo=UTC))"
    )
    assert _evaluate(expression)[1] == EPOCH_1904


def test_a_part_with_no_python_value_is_a_gap_naming_the_field_and_type() -> None:
    lv_type = _cluster(
        ("map", LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Map"))
    )
    node = ConstantNode(
        id="c",
        vi_path="v.vi",
        value="{'map': {}}",
        lv_type=lv_type,
        structured_value={"map": "{}"},
    )
    with pytest.raises(ConversionGap, match="'map'.*Map value"):
        CodeGenContext().constant_expression(node)


def test_a_cluster_of_plain_values_is_unchanged() -> None:
    named = _cluster(("length", _I32), ("# elements", _I32), typedef="Header.ctl")
    data = (7).to_bytes(4, "big") + (3).to_bytes(4, "big")
    assert _expression(named, data) == "SimpleNamespace(length=7, _elements=3)"
    anonymous = _cluster(("a", _I32), ("b", _STRING))
    assert _expression(anonymous, data[:4] + _string("x")) == "(7, 'x')"
    single = _cluster(("a", _I32))
    assert _expression(single, data[:4]) == "(7,)"


def test_an_array_of_numbers_is_a_list() -> None:
    data = (2).to_bytes(4, "big") + (1).to_bytes(4, "big") + (2).to_bytes(4, "big")
    assert _expression(_array(_I32), data) == "[1, 2]"


def test_an_array_of_clusters_is_a_list_of_tuples() -> None:
    element = _cluster(("a", _I32), ("v", _VARIANT))
    data = (2).to_bytes(4, "big") + (
        (1).to_bytes(4, "big")
        + _EMPTY_VARIANT
        + (2).to_bytes(4, "big")
        + _EMPTY_VARIANT
    )
    expression = _expression(_array(element), data)
    assert expression == "[(1, Variant()), (2, Variant())]"


def test_an_error_cluster_constant_is_unchanged() -> None:
    error = _cluster(
        ("status", LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")),
        ("code", _I32),
        ("source", _STRING),
    )
    assert _expression(error, b"\x00" + bytes(4) + _string("")) == "None"
    expression = _expression(error, b"\x01" + (5).to_bytes(4, "big") + _string("x"))
    assert expression == "LabVIEWError(code=5, source='x', status=True)"


def test_an_error_cluster_field_is_the_value_of_an_error_wire() -> None:
    error = _cluster(
        ("status", LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")),
        ("code", _I32),
        ("source", _STRING),
    )
    lv_type = _cluster(("error", error), ("n", _I32))
    data = b"\x01" + (5).to_bytes(4, "big") + _string("x") + bytes(4)
    assert _expression(lv_type, data) == (
        "(LabVIEWError(code=5, source='x', status=True), 0)"
    )


def test_an_enum_constant_is_unchanged() -> None:
    enum = LVType(kind=LVTypeKind.ENUM, underlying_type="NumUInt16")
    node = _node(enum, (3).to_bytes(2, "big"))
    assert CodeGenContext().constant_expression(node) == "3"


# === the decoded value of a Variant ===


def test_a_variants_decoded_value_holds_its_type_and_value() -> None:
    _, _, empty = _decode_element(_EMPTY_VARIANT, _VARIANT)
    assert empty == VariantData(None, None)
    _, _, held = _decode_element(_I32_VARIANT, _VARIANT)
    assert isinstance(held, VariantData)
    assert held.value_type is not None
    assert held.value_type.underlying_type == "NumInt32"
    assert held.value == "5"
    assert held.attributes == ()
    _, _, attributed = _decode_element(_ONE_ATTRIBUTE, _VARIANT)
    assert attributed == VariantData(None, None, (("a", VariantData(None, None)),))


def test_a_front_panel_default_reads_a_variant_as_it_always_has() -> None:
    lv_type = _cluster(("v", _VARIANT), ("w", _VARIANT))
    _, _, structured = _decode_element(_EMPTY_VARIANT + _I32_VARIANT, lv_type)
    assert plain_structured(structured) == {"v": "Variant()", "w": "5"}
