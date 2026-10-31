"""LabVIEW's error cluster is decided by its fields alone: ``status`` (Boolean),
``code`` (I32) and ``source`` (String), in that order. The name of the typedef a
cluster comes from never decides."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.render_lvnet import lvnet_type_ref
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.parser.type_mapping import parse_vctp_types
from tests.native_vi import generate, scalar

BOOL = scalar("Boolean")
I32 = scalar("NumInt32")
U32 = scalar("NumUInt32")
STRING = scalar("String")

_STATUS = ("status", BOOL)
_CODE = ("code", I32)
_SOURCE = ("source", STRING)


def cluster(*fields: tuple[str, LVType | None], typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef,
        fields=[ClusterField(name=n, type=t) for n, t in fields],
    )


@pytest.mark.parametrize(
    "lv_type,expected",
    [
        (cluster(_STATUS, _CODE, _SOURCE), True),
        (cluster(_STATUS, _CODE, _SOURCE, typedef="error in.ctl"), True),
        (cluster(("Status", BOOL), ("Code", I32), ("Source", STRING)), True),
        (cluster(_CODE, _SOURCE, typedef="Filtered Error Details.ctl"), False),
        (cluster(_STATUS, _CODE, _SOURCE, ("extra", I32)), False),
        (cluster(_CODE, _STATUS, _SOURCE), False),
        (cluster(_STATUS, ("code", U32), _SOURCE), False),
        (cluster(_STATUS, ("code", BOOL), ("source", BOOL)), False),
        (cluster(_STATUS, _CODE, ("source", None)), False),
        (cluster(typedef="Error Details.ctl"), False),
        (LVType(kind=LVTypeKind.CLUSTER, typedef_name="error codes.ctl"), False),
        (LVType(kind=LVTypeKind.TYPEDEF_REF, typedef_name="Error Codes.ctl"), False),
    ],
)
def test_only_the_fields_decide_whether_a_cluster_is_an_error_cluster(
    lv_type: LVType, expected: bool
) -> None:
    assert (lv_type.type_descriptor() == "Error") is expected


_VCTP = """
<TypeDesc Type="Boolean" Label="status" />
<TypeDesc Type="NumInt32" Label="code" />
<TypeDesc Type="String" Label="source" />
<TypeDesc Type="Array" Label="Enum Text">
  <Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />
  <TypeDesc TypeID="2" />
</TypeDesc>
<TypeDesc Type="NumUInt32" Label="Enum" />
<TypeDesc Type="TypeDef">
  <TypeDesc Type="Cluster" Nested="True" Label="Filtered Error Details">
    <TypeDesc TypeID="1" />
    <TypeDesc TypeID="2" />
  </TypeDesc>
  <Label Text="Lib.lvlib" />
  <Label Text="Filtered Error Details - Cluster.ctl" />
</TypeDesc>
<TypeDesc Type="TypeDef">
  <TypeDesc Type="Cluster" Nested="True" Label="PS2000 error">
    <TypeDesc TypeID="4" />
    <TypeDesc TypeID="3" />
  </TypeDesc>
  <Label Text="PS2000 error codes.ctl" />
</TypeDesc>
<TypeDesc Type="Cluster" Label="error in (no error)">
  <TypeDesc TypeID="0" />
  <TypeDesc TypeID="1" />
  <TypeDesc TypeID="2" />
</TypeDesc>
"""


def test_a_typedef_named_error_keeps_its_fields_in_describe_and_lvnet(
    tmp_path: Path,
) -> None:
    xml = tmp_path / "vi.xml"
    xml.write_text(f"<RSRC><VCTP><Section>{_VCTP}</Section></VCTP></RSRC>")
    types = parse_vctp_types(xml)
    filtered, ps2000, real = types[5], types[6], types[7]

    assert filtered.type_descriptor() == (
        "Filtered Error Details - Cluster{code, source}"
    )
    assert lvnet_type_ref(filtered) == "Filtered Error Details - Cluster"
    assert [f.name for f in filtered.fields or []] == ["code", "source"]

    assert ps2000.type_descriptor() == "PS2000 error codes{Enum, Enum Text}"
    assert lvnet_type_ref(ps2000) == "PS2000 error codes"

    assert real.type_descriptor() == "Error"
    assert lvnet_type_ref(real) == "Cluster{ status : TF, code : I32, source : String }"


_FILTERED = cluster(_CODE, _SOURCE, typedef="Filtered Error Details - Cluster.ctl")
_ERROR = cluster(_STATUS, _CODE, _SOURCE)


def test_a_cluster_typedef_named_error_is_an_output_of_the_generated_function() -> None:
    generated = generate(
        {"details": _FILTERED},
        {"details out": _FILTERED},
        [],
        [("fp:details", "fp:details out")],
    )
    assert generated.function((7, "src")).details_out == (7, "src")


def test_a_real_error_cluster_is_still_not_an_output() -> None:
    generated = generate(
        {"error in": _ERROR},
        {"error out": _ERROR},
        [],
        [("fp:error in", "fp:error out")],
    )
    assert "error_out" not in generated.source.split("def native_test")[1]
