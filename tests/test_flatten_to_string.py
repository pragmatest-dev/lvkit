"""Flatten To String (1164): the exact bytes NI's "Flattened Data" page gives
for each type, the byte order and size inputs, the error wire, and the round
trip through Unflatten From String."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from tests.helpers import error_cluster_type
from tests.native_vi import (
    Generated,
    array,
    generate,
    node,
    result_values,
    scalar,
)

I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
U8 = scalar("NumUInt8")
U16 = scalar("NumUInt16")
DBL = scalar("NumFloat64")
BOOL = scalar("Boolean")
STR = scalar("String")


def flatten(
    source: LVType,
    order: int | None = None,
    prefix: bool | None = None,
) -> Generated:
    """A VI whose Flatten To String flattens its ``x`` control; ``order`` and
    ``prefix`` are wired from constants when given."""
    terminals = [(3, "output", STR), (11, "input", source)]
    wires = [("fp:x", "ft:11"), ("ft:3", "fp:r")]
    constants: dict[str, tuple[object, LVType]] = {}
    if order is not None:
        terminals.append((9, "input", U16))
        wires.append(("c9", "ft:9"))
        constants["c9"] = (order, U16)
    if prefix is not None:
        terminals.append((10, "input", BOOL))
        wires.append(("c10", "ft:10"))
        constants["c10"] = (prefix, BOOL)
    return generate(
        {"x": source},
        {"r": STR},
        [node("ft", 1164, terminals)],
        wires,
        constants=constants,
    )


def flat_hex(generated: Generated, value: object) -> str:
    return result_values(generated.function(value))[0].encode("latin-1").hex()


def test_the_catalog_names_flatten_to_string_and_its_op() -> None:
    resolved = get_resolver().resolve(prim_id=1164)
    assert resolved is not None
    assert (resolved.name, resolved.op) == ("Flatten To String", "FLATTEN_TO_STRING")
    names = {t.index: t.name for t in resolved.terminals}
    assert names == {
        0: "error_out",
        2: "type_string",
        3: "data_string",
        8: "error_in",
        9: "byte_order",
        10: "prepend_size",
        11: "anything",
    }


@pytest.mark.parametrize(
    ("source", "value", "expected"),
    [
        (U8, 1, "01"),
        (I32, -1, "ffffffff"),
        (DBL, 1.0, "3ff0000000000000"),
        (BOOL, True, "01"),
        (STR, "ABC", "00000003414243"),
        (array("NumInt8", 2), [[1, 2, 3], [4, 5, 6]], "0000000200000003010203040506"),
        (array("NumInt16"), [1, 2], "0000000200010002"),
    ],
)
def test_flattened_bytes_are_those_of_nis_flattened_data_page(
    source: LVType, value: object, expected: str
) -> None:
    generated = flatten(source)
    assert flat_hex(generated, value) == expected
    assert "lvkit" not in generated.source


def test_a_cluster_is_the_concatenation_of_its_elements() -> None:
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="s", type=STR), ClusterField(name="n", type=I16)],
    )
    assert flat_hex(flatten(record), ("ABC", 4)) == "000000034142430004"


def test_prepend_size_false_leaves_the_size_off_a_string() -> None:
    assert flat_hex(flatten(STR, prefix=False), "ABC") == "414243"


def test_prepend_size_false_keeps_the_size_of_a_cluster_member() -> None:
    assert flat_hex(flatten(array("NumInt16"), prefix=False), [1, 2]) == "00010002"
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name="s", type=STR)],
    )
    assert flat_hex(flatten(record, prefix=False), ("AB",)) == "000000024142"


def test_little_endian_integer() -> None:
    generated = flatten(I32, order=2)
    assert "struct.pack('<i'" in generated.source
    assert flat_hex(generated, -2) == "feffffff"


def test_little_endian_double() -> None:
    assert flat_hex(flatten(DBL, order=2), 1.0) == "000000000000f03f"


def test_little_endian_also_orders_the_size_of_a_string() -> None:
    assert flat_hex(flatten(STR, order=2), "ab") == "020000006162"


def test_big_endian_constant_is_the_default() -> None:
    assert flat_hex(flatten(I32, order=0), 1) == "00000001"


def test_flatten_then_unflatten_gives_the_value_back() -> None:
    record = LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[
            ClusterField(name="count", type=I32),
            ClusterField(name="label", type=STR),
            ClusterField(name="values", type=array("NumFloat64")),
        ],
    )
    generated = generate(
        {"x": record},
        {"v": record},
        [
            node("ft", 1164, [(3, "output", STR), (11, "input", record)]),
            node(
                "uf",
                1165,
                [(2, "output", record), (7, "input", record), (11, "input", STR)],
            ),
        ],
        [("fp:x", "ft:11"), ("ft:3", "uf:11"), ("uf:2", "fp:v")],
    )
    value = (7, "hello", [1.5, -2.0])
    assert result_values(generated.function(value))[0] == value


def test_the_error_wire_passes_through() -> None:
    generated = generate(
        {"x": I32, "error_in": error_cluster_type()},
        {"r": STR, "error_out": error_cluster_type()},
        [
            node(
                "ft",
                1164,
                [
                    (0, "output", error_cluster_type()),
                    (3, "output", STR),
                    (8, "input", error_cluster_type()),
                    (11, "input", I32),
                ],
            )
        ],
        [
            ("fp:x", "ft:11"),
            ("fp:error_in", "ft:8"),
            ("ft:3", "fp:r"),
            ("ft:0", "fp:error_out"),
        ],
    )
    result = generated.function(5, None)
    assert (result.r, result.error_out) == ("\x00\x00\x00\x05", None)


def test_a_wired_type_string_output_is_the_literal_descriptor() -> None:
    """The 7.x type string of an I32 is NI's Type Descriptors example, [4, 3]."""
    generated = generate(
        {"x": I32},
        {"t": array("NumInt16")},
        [
            node(
                "ft",
                1164,
                [
                    (2, "output", array("NumInt16")),
                    (3, "output", STR),
                    (11, "input", I32),
                ],
            )
        ],
        [("fp:x", "ft:11"), ("ft:2", "fp:t")],
    )
    assert result_values(generated.function(1))[0] == [4, 3]
    assert "pickle" not in generated.source


def test_a_type_with_no_flattened_form_is_a_gap_that_raises_where_it_runs() -> None:
    generated = flatten(scalar("Variant"))
    with pytest.raises(NotImplementedError, match="does not handle.*Variant"):
        generated.function(None)
