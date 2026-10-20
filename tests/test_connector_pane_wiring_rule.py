"""Issue #173: a connector-pane terminal's wiring rule is Flags bit 12
(Required) / bit 11 (Recommended) / neither (Optional) of the Function TypeDesc
that CONP points to -- not bits 8-9 of the first Function TypeDesc that fits.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml
from lvkit.parser.flags import get_wiring_rule
from lvkit.parser.front_panel import (
    parse_connector_pane,
    parse_connector_pane_types,
)
from lvkit.parser.models import (
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedWiringRule,
)

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (0x1000, ParsedWiringRule.REQUIRED),
        (0x1200, ParsedWiringRule.REQUIRED),
        (0x9200, ParsedWiringRule.REQUIRED),  # Dynamic Dispatch input
        (0x0800, ParsedWiringRule.RECOMMENDED),
        (0x0A00, ParsedWiringRule.RECOMMENDED),
        (0x8D0B, ParsedWiringRule.RECOMMENDED),  # Dynamic Dispatch output
        (0x0D08, ParsedWiringRule.RECOMMENDED),  # output (bit 8) is not Required
        (0x0000, ParsedWiringRule.OPTIONAL),
        (0x0200, ParsedWiringRule.OPTIONAL),  # bit 9 is not a wiring bit
    ],
)
def test_get_wiring_rule_decodes_bits_11_and_12(
    flags: int, expected: ParsedWiringRule
) -> None:
    assert get_wiring_rule(flags) == expected


_MAIN_XML = """\
<LVSR>
 <VCTP><Section>
  <TypeDesc Type="Function" Flags="0x0000">
   <TypeDesc TypeID="9" Flags="0x0800"/>
   <TypeDesc TypeID="9" Flags="0x0800"/>
  </TypeDesc>
  <TypeDesc Type="Function" Flags="0x0000">
   <TypeDesc TypeID="9" Flags="0x1200"/>
   <TypeDesc TypeID="9" Flags="0x0000"/>
   <TypeDesc TypeID="9" Flags="0x0D08"/>
  </TypeDesc>
  <TopLevel>
   <TypeDesc Index="0" FlatTypeID="1"/>
  </TopLevel>
 </Section></VCTP>
 <CONP><Section><TypeDesc TypeID="0"/></Section></CONP>
</LVSR>
"""


def test_parse_connector_pane_types_uses_the_type_conp_points_to(
    tmp_path: Path,
) -> None:
    """The first Function TypeDesc is an unrelated interface; CONP names the
    second. A slot with Flags 0x0000 is Optional, not unreadable."""
    main_xml = tmp_path / "vi.xml"
    main_xml.write_text(_MAIN_XML)
    pane = ParsedConnectorPane(
        pattern_id=0,
        slots=[
            ParsedConnectorPaneSlot(index=0, fp_dco_uid="a"),
            ParsedConnectorPaneSlot(index=1, fp_dco_uid="b"),
            ParsedConnectorPaneSlot(index=2, fp_dco_uid="c"),
        ],
    )
    assert parse_connector_pane_types(main_xml, pane) == {
        0: ParsedWiringRule.REQUIRED,
        1: ParsedWiringRule.OPTIONAL,
        2: ParsedWiringRule.RECOMMENDED,
    }


def test_parse_connector_pane_types_without_conp_is_empty(tmp_path: Path) -> None:
    main_xml = tmp_path / "vi.xml"
    ET.ElementTree(ET.fromstring("<LVSR/>")).write(main_xml)
    pane = ParsedConnectorPane(
        pattern_id=0, slots=[ParsedConnectorPaneSlot(index=0, fp_dco_uid="a")]
    )
    assert parse_connector_pane_types(main_xml, pane) == {}


def _rules(vi_relpath: str) -> dict[int, int]:
    vi_path = _SAMPLES / vi_relpath
    if not vi_path.exists():
        pytest.skip(f"{vi_relpath} not present in sample corpus")
    _, fp_xml, main_xml = extract_vi_xml(vi_path)
    assert fp_xml is not None and main_xml is not None
    pane = parse_connector_pane(fp_xml)
    assert pane is not None
    return parse_connector_pane_types(main_xml, pane)


R = ParsedWiringRule


@pytest.mark.needs_samples
def test_fail_if_equal_reads_required_recommended_and_optional() -> None:
    """Slot flags of its own pane: TestCase in 0x9200, x 0x1200, y 0x1200,
    delta 0x0A00, error in 0x0A00, custom report message 0x0000, TestCase out
    0x8D0B, error out 0x0D08 (slots 11, 10, 9, 6, 8, 7, 3, 0)."""
    rules = _rules("JKI-VI-Tester/source/Classes/TestCase/failIfEqual.vi")
    assert rules == {
        11: R.REQUIRED,
        10: R.REQUIRED,
        9: R.REQUIRED,
        6: R.RECOMMENDED,
        8: R.RECOMMENDED,
        7: R.OPTIONAL,
        3: R.RECOMMENDED,
        0: R.RECOMMENDED,
    }


@pytest.mark.needs_samples
def test_easy_write_xml_file_has_all_three_states() -> None:
    rules = _rules("JKI-EasyXML/Source/Easy Write XML File.vi")
    assert rules[11] == R.REQUIRED  # XML File Path 0x1000
    assert rules[10] == R.REQUIRED  # LabVIEW Data 0x1200
    assert rules[8] == R.RECOMMENDED  # error in 0x0A00
    assert rules[9] == R.RECOMMENDED  # Overwrite File? 0x0800
    assert rules[7] == R.OPTIONAL  # Stylesheet URL 0x0000


@pytest.mark.needs_samples
def test_easy_parse_xml_reads_its_own_pane_not_a_subvi_interface() -> None:
    rules = _rules("JKI-EasyXML/Source/Easy Parse XML.vi")
    assert rules[10] == R.REQUIRED  # XML String 0x11200
    assert rules[9] == R.REQUIRED  # LabVIEW Data (Type) 0x1000


@pytest.mark.needs_samples
def test_actor_core_dynamic_dispatch_input_is_required() -> None:
    rules = _rules("actor-framework/Core/ActorFramework/Actor/Actor Core.vi")
    assert rules[11] == R.REQUIRED  # Actor in 0x9200
    assert rules[3] == R.RECOMMENDED  # Actor out
