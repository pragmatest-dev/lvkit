"""A strictly typed VI refnum records the connector pane of the VIs it can hold:
the used terminals of its Function type, in slot order, with each slot's
direction, type and label."""

from __future__ import annotations

from pathlib import Path

from lvkit.models import LVTypeKind
from lvkit.parser.type_mapping import parse_vctp_types

_MAIN_XML = """<?xml version='1.0' encoding='utf-8'?>
<VI><VCTP><Section Index="0" Format="inline">
  <TypeDesc Type="Boolean" Format="inline" Label="stop" />
  <TypeDesc Type="NumInt32" Format="inline" Label="count" />
  <TypeDesc Type="Void" Format="inline" />
  <TypeDesc Type="Function" Format="inline">
    <TypeDesc TypeID="0" Flags="0x1000" />
    <TypeDesc TypeID="2" Flags="0x0000" />
    <TypeDesc TypeID="1" Flags="0x0900" />
  </TypeDesc>
  <TypeDesc Type="Refnum" RefType="LVObjCtl" Format="inline">
    <TypeDesc TypeID="3" />
  </TypeDesc>
  <TypeDesc Type="Refnum" RefType="LVObjCtl" Format="inline" />
</Section></VCTP></VI>
"""


def test_a_strictly_typed_vi_refnum_carries_its_connector_pane(
    tmp_path: Path,
) -> None:
    main = tmp_path / "Pane.xml"
    main.write_text(_MAIN_XML)
    types = parse_vctp_types(main)

    typed = types[4]
    assert typed.ref_type == "LVObjCtl"
    assert typed.element_type is not None
    pane = typed.element_type.pane
    assert pane is not None
    assert [(s.index, s.is_output, s.name) for s in pane] == [
        (0, False, "stop"),
        (2, True, "count"),
    ]
    assert [s.type.underlying_type for s in pane] == ["Boolean", "NumInt32"]
    assert pane[0].type.kind == LVTypeKind.PRIMITIVE


def test_a_vi_refnum_without_a_type_has_no_pane(tmp_path: Path) -> None:
    main = tmp_path / "Pane.xml"
    main.write_text(_MAIN_XML)
    generic = parse_vctp_types(main)[5]
    assert generic.element_type is None
