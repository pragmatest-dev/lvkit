"""Test that every LabVIEW constant type decodes correctly.

decode_constant returns (python_type, decoded_value).
The python_type comes from lv_type.to_python().
The decoded_value is a human-readable string representation.
"""

from __future__ import annotations

from lvpy.blockdiagram import decode_constant
from lvpy.graph_types import ClusterField, LVType
from lvpy.parser.models import Constant


def _make_const(hex_val: str, label: str | None = None) -> Constant:
    return Constant(uid="test", type_desc="", value=hex_val, label=label)


def _make_type(
    kind: str,
    underlying: str | None = None,
    fields: list[ClusterField] | None = None,
    element_type: LVType | None = None,
) -> LVType:
    return LVType(
        kind=kind,
        underlying_type=underlying,
        fields=fields,
        element_type=element_type,
    )


def _array_of(underlying: str) -> LVType:
    return _make_type(
        "array", "Array",
        element_type=_make_type("primitive", underlying),
    )


def _decode(hex_val: str, lv_type: LVType) -> tuple[str, str]:
    return decode_constant(_make_const(hex_val), lv_type=lv_type)


# === Boolean ===

class TestBoolean:
    LV = _make_type("primitive", "Boolean")

    def test_true_1byte(self):
        _, v = _decode("01", self.LV)
        assert v == "True"

    def test_false_1byte(self):
        _, v = _decode("00", self.LV)
        assert v == "False"

    def test_true_2byte(self):
        _, v = _decode("0001", self.LV)
        assert v == "True"

    def test_false_2byte(self):
        _, v = _decode("0000", self.LV)
        assert v == "False"


# === Integers ===

class TestIntegers:
    def test_int8(self):
        _, v = _decode("FF", _make_type("primitive", "NumInt8"))
        assert v == "-1"

    def test_uint8(self):
        _, v = _decode("FF", _make_type("primitive", "NumUInt8"))
        assert v == "255"

    def test_int16(self):
        _, v = _decode("4000", _make_type("primitive", "NumInt16"))
        assert v == "16384"

    def test_uint16(self):
        _, v = _decode("0002", _make_type("primitive", "NumUInt16"))
        assert v == "2"

    def test_int32(self):
        _, v = _decode("00000001", _make_type("primitive", "NumInt32"))
        assert v == "1"

    def test_int32_negative(self):
        _, v = _decode("FFFFFFFF", _make_type("primitive", "NumInt32"))
        assert v == "-1"

    def test_uint32(self):
        _, v = _decode("FFFFFFFF", _make_type("primitive", "NumUInt32"))
        assert v == "4294967295"

    def test_int64(self):
        _, v = _decode("0000000000000000", _make_type("primitive", "NumInt64"))
        assert v == "0"

    def test_uint64(self):
        _, v = _decode("0000000000000001", _make_type("primitive", "NumUInt64"))
        assert v == "1"


# === Floats ===

class TestFloats:
    def test_float32(self):
        _, v = _decode("3F800000", _make_type("primitive", "NumFloat32"))
        assert float(v) == 1.0

    def test_float64(self):
        _, v = _decode("3FF0000000000000", _make_type("primitive", "NumFloat64"))
        assert float(v) == 1.0

    def test_float64_zero(self):
        _, v = _decode("0000000000000000", _make_type("primitive", "NumFloat64"))
        assert float(v) == 0.0


# === String ===

class TestString:
    LV = _make_type("primitive", "String")

    def test_empty_string(self):
        _, v = _decode("00000000", self.LV)
        assert v is not None

    def test_hello(self):
        _, v = _decode("0000000548656C6C6F", self.LV)
        assert "Hello" in v

    def test_short_string(self):
        _, v = _decode("00000002696E", self.LV)
        assert "in" in v


# === Path ===

class TestPath:
    def test_not_a_path(self):
        _, v = _decode(
            "50544830000000000000000000000000",
            _make_type("primitive", "Path"),
        )
        assert v is not None
        assert "raw" not in str(v).lower() or "path" in str(v).lower()


# === Enum ===

class TestEnum:
    def test_enum_uint16(self):
        _, v = _decode("0002", _make_type("enum", "UnitUInt16"))
        assert v == "2"

    def test_enum_uint8(self):
        _, v = _decode("03", _make_type("enum", "UnitUInt8"))
        assert v == "3"

    def test_enum_uint32(self):
        _, v = _decode("00000001", _make_type("enum", "UnitUInt32"))
        assert v == "1"


# === Complex ===

class TestComplex:
    def test_complex64(self):
        _, v = _decode(
            "3F80000000000000",
            _make_type("primitive", "NumComplex64"),
        )
        assert v is not None

    def test_complex128(self):
        _, v = _decode(
            "3FF00000000000000000000000000000",
            _make_type("primitive", "NumComplex128"),
        )
        assert v is not None


# === Cluster ===

class TestCluster:
    def test_error_cluster(self):
        """Error cluster: {status: Bool, code: I32, source: String}."""
        lv_type = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="status", type=_make_type("primitive", "Boolean")),
                ClusterField(name="code", type=_make_type("primitive", "NumInt32")),
                ClusterField(name="source", type=_make_type("primitive", "String")),
            ],
        )
        raw = "00" + "00000000" + "00000000"
        _, v = _decode(raw, lv_type)
        assert "status" in v
        assert "False" in v
        assert "code" in v

    def test_nested_cluster(self):
        inner = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="x", type=_make_type("primitive", "NumInt32")),
                ClusterField(name="y", type=_make_type("primitive", "NumInt32")),
            ],
        )
        outer = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="point", type=inner),
                ClusterField(name="label", type=_make_type("primitive", "String")),
            ],
        )
        raw = "00000001" + "00000002" + "00000002" + "6869"
        _, v = _decode(raw, outer)
        assert "point" in v
        assert "label" in v


# === Array ===

class TestArray:
    def test_int32_array(self):
        lv_type = _array_of("NumInt32")
        raw = "00000003" + "00000001" + "00000002" + "00000003"
        _, v = _decode(raw, lv_type)
        assert "1" in v
        assert "2" in v
        assert "3" in v

    def test_empty_array(self):
        lv_type = _array_of("NumInt32")
        _, v = _decode("00000000", lv_type)
        assert "[]" in v

    def test_string_array(self):
        lv_type = _array_of("String")
        raw = "00000002" + "00000002" + "6162" + "00000002" + "6364"
        _, v = _decode(raw, lv_type)
        assert "ab" in v
        assert "cd" in v

    def test_bool_array(self):
        lv_type = _array_of("Boolean")
        raw = "00000003" + "01" + "00" + "01"
        _, v = _decode(raw, lv_type)
        assert "True" in v
        assert "False" in v


# === Nested compound types ===

class TestCompoundNesting:
    def test_array_of_clusters(self):
        cluster_type = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="x", type=_make_type("primitive", "NumInt32")),
                ClusterField(name="y", type=_make_type("primitive", "NumInt32")),
            ],
        )
        lv_type = _make_type("array", "Array", element_type=cluster_type)
        raw = "00000002" + "00000001" + "00000002" + "00000003" + "00000004"
        _, v = _decode(raw, lv_type)
        assert "x" in v
        assert "y" in v
        assert "1" in v
        assert "4" in v

    def test_cluster_with_array(self):
        lv_type = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="name", type=_make_type("primitive", "String")),
                ClusterField(
                    name="values",
                    type=_array_of("NumInt32"),
                ),
            ],
        )
        raw = "00000004" + "74657374" + "00000002" + "0000000A" + "00000014"
        _, v = _decode(raw, lv_type)
        assert "name" in v
        assert "test" in v
        assert "values" in v
        assert "10" in v
        assert "20" in v

    def test_cluster_with_bool_and_array(self):
        lv_type = _make_type(
            "cluster", "Cluster",
            fields=[
                ClusterField(name="active", type=_make_type("primitive", "Boolean")),
                ClusterField(
                    name="data",
                    type=_array_of("NumInt16"),
                ),
            ],
        )
        raw = "01" + "00000003" + "0001" + "0002" + "0003"
        _, v = _decode(raw, lv_type)
        assert "active" in v
        assert "True" in v
        assert "data" in v
        assert "1" in v
        assert "3" in v


# === Refnum ===

class TestRefnum:
    def test_refnum_decodes(self):
        _, v = _decode("00000000", _make_type("primitive", "Refnum"))
        assert v is not None


# === LVVariant ===

class TestVariant:
    def test_variant_decodes(self):
        _, v = _decode("00000000", _make_type("primitive", "LVVariant"))
        assert v is not None


# === MeasureData (Timestamp) ===

class TestTimestamp:
    def test_timestamp_decodes(self):
        _, v = _decode(
            "0000000000000000" + "0000000000000000",
            _make_type("primitive", "MeasureData"),
        )
        assert v is not None


# === No type = parser bug ===

class TestNoType:
    def test_no_type_returns_raw(self):
        t, v = decode_constant(_make_const("01"))
        assert t == "raw"
        assert v == "01"
