"""Tests for type mapping and VCTP parsing (parser/type_mapping.py).

Covers parse_type_map_rich, parse_vctp_types (including recursive resolve_type
with cycle detection).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lvkit.parser.type_mapping import parse_type_map_rich, parse_vctp_types

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
TESTRESULT_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestResult"
TESTCASE_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"
ADDERROR_VI = TESTRESULT_DIR / "addError.vi"
RUN_VI = TESTCASE_DIR / "run.vi"


def _get_main_xml(vi_path: Path) -> Path:
    """Extract VI and return path to main XML."""
    from lvkit.extractor import extract_vi_xml

    _, _, main_xml = extract_vi_xml(vi_path)
    assert main_xml is not None and main_xml.exists()
    return main_xml


@pytest.mark.skipif(
    not ADDERROR_VI.exists(),
    reason="JKI-VI-Tester samples not available",
)
class TestParseTypeMapRich:
    """parse_type_map_rich: full type_map from VI main XML."""

    def test_cluster_with_named_fields(self):
        """Error cluster has fields: status, code, source."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        # Find a cluster type with error cluster fields
        cluster_types = [
            lt for lt in type_map.values() if lt.kind == "cluster" and lt.fields
        ]
        assert len(cluster_types) > 0

        # At least one cluster should have the standard error fields
        error_clusters = [
            lt
            for lt in cluster_types
            if lt.fields is not None
            and len(lt.fields) == 3
            and {f.name for f in lt.fields} == {"status", "code", "source"}
        ]
        assert len(error_clusters) > 0

    def test_enum_with_labels(self):
        """resultStatus enum has labels for test outcomes."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        enum_types = [
            lt for lt in type_map.values() if lt.values and "testPass" in lt.values
        ]
        assert len(enum_types) > 0

        enum = enum_types[0]
        assert enum.values is not None
        assert "testFailure" in enum.values
        assert "testError" in enum.values
        assert "testSkipped" in enum.values
        # Values should have numeric .value
        assert enum.values["testPass"].value == 0
        assert enum.values["testFailure"].value == 1

    def test_array_type(self):
        """errors.ctl is an array typedef."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        array_types = [lt for lt in type_map.values() if lt.kind == "array"]
        assert len(array_types) > 0

    def test_typedef_name_populated(self):
        """Typedefs should have typedef_name set (e.g., resultStatus--enum.ctl)."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        named_typedefs = [
            lt
            for lt in type_map.values()
            if lt.typedef_name and lt.typedef_name.endswith(".ctl")
        ]
        assert len(named_typedefs) > 0

    def test_typedef_wrapping_cluster_has_fields(self):
        """resultStatusChanged--Cluster.ctl wraps a cluster with fields."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        cluster_typedefs = [
            lt
            for lt in type_map.values()
            if lt.typedef_name and "Cluster" in (lt.typedef_name or "") and lt.fields
        ]
        assert len(cluster_typedefs) > 0
        # Should have parseable field names
        td = cluster_typedefs[0]
        assert td.fields is not None
        field_names = [f.name for f in td.fields]
        assert len(field_names) >= 2


@pytest.mark.skipif(
    not RUN_VI.exists(),
    reason="JKI-VI-Tester samples not available",
)
class TestEnumTypedefFromRun:
    def test_enum_typedef_with_qualified_name(self):
        """run.vi has method--Enum.ctl typedef — should have typedef_name set."""
        main_xml = _get_main_xml(RUN_VI)
        type_map = parse_type_map_rich(main_xml)

        method_enums = [
            lt
            for lt in type_map.values()
            if lt.typedef_name and "method" in lt.typedef_name.lower()
        ]
        assert len(method_enums) > 0


class TestCycleDetection:
    """parse_vctp_types with self-referencing TypeDesc should not infinite-loop."""

    def test_self_referencing_type_returns_recursive(self, tmp_path):
        """Self-referencing TypeDesc resolves to 'Recursive'."""
        # Construct minimal XML with a self-referencing Cluster
        xml_content = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <RSRC>
                <VCTP>
                    <Section>
                        <TypeDesc Type="Cluster">
                            <TypeDesc TypeID="0" />
                        </TypeDesc>
                    </Section>
                </VCTP>
            </RSRC>
        """)
        xml_path = tmp_path / "cycle.xml"
        xml_path.write_text(xml_content)

        # Should not hang — cycle detection kicks in
        result = parse_vctp_types(xml_path)
        assert 0 in result

        lv_type = result[0]
        assert lv_type.kind == "cluster"
        # The self-referencing field should have resolved to "Recursive"
        assert lv_type.fields is not None
        assert len(lv_type.fields) == 1
        assert lv_type.fields[0].type is not None
        assert lv_type.fields[0].type.underlying_type == "Recursive"


class TestRefnumElementType:
    """A parametrized refnum (Queue/Notifier/DVR) carries its element/payload
    type as a single nested TypeDesc; a class refnum keeps its class name; an
    EventReg with several nested types has no single element."""

    def _vctp(self, tmp_path, body: str):
        xml = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <RSRC><VCTP><Section>
            {body}
            </Section></VCTP></RSRC>
        """)
        p = tmp_path / "refnum.xml"
        p.write_text(xml)
        return parse_vctp_types(p)

    def test_queue_of_dbl_carries_element_and_renders_in_braces(self, tmp_path):
        # FlatTypeID 0 = element (DBL); FlatTypeID 1 = Queue refnum of it.
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="NumFloat64" />
            <TypeDesc Type="Refnum" RefType="Queue">
                <TypeDesc TypeID="0" />
            </TypeDesc>""",
        )
        q = result[1]
        assert q.underlying_type == "Refnum"
        assert q.ref_type == "Queue"
        assert q.element_type is not None
        assert q.element_type.underlying_type == "NumFloat64"
        assert q.type_descriptor() == "Queue refnum{DBL}"

    def test_eventreg_with_multiple_nested_has_no_single_element(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="NumFloat64" />
            <TypeDesc Type="Boolean" />
            <TypeDesc Type="Refnum" RefType="EventReg">
                <TypeDesc TypeID="0" />
                <TypeDesc TypeID="1" />
            </TypeDesc>""",
        )
        ev = result[2]
        assert ev.ref_type == "EventReg"
        assert ev.element_type is None
        assert ev.type_descriptor() == "EventReg refnum"

    def test_class_refnum_keeps_class_name_not_element(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="Refnum" RefType="UDClassInst">
                <Item Text="TestCase.lvclass" />
            </TypeDesc>""",
        )
        c = result[0]
        assert c.classname == "TestCase.lvclass"
        assert c.element_type is None
        assert c.type_descriptor() == "TestCase.lvclass"


class TestUserEventName:
    """A User Event's name is the label of its data type; a type-definition
    data type carries the constant's own label, not the .ctl file name."""

    def _vctp(self, tmp_path, body: str):
        xml = textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <RSRC><VCTP><Section>
            {body}
            </Section></VCTP></RSRC>
        """)
        p = tmp_path / "uev.xml"
        p.write_text(xml)
        return parse_vctp_types(p)

    def test_plain_data_type_label_is_the_event_name(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="Boolean" Label="stopped" />
            <TypeDesc Type="Refnum" RefType="UserEvent">
                <TypeDesc TypeID="0" />
            </TypeDesc>""",
        )
        assert result[1].user_event_name == "stopped"
        assert result[1].type_descriptor() == "UserEvent refnum{stopped: TF}"

    def test_typedef_instance_label_wins_over_ctl_name(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="TypeDef">
                <TypeDesc Type="Cluster" Nested="True" Label="Drive Status" />
                <Label Text="Lib.lvlib" />
                <Label Text="Network Endpoint.ctl" />
            </TypeDesc>
            <TypeDesc Type="Refnum" RefType="UserEvent">
                <TypeDesc TypeID="0" />
            </TypeDesc>""",
        )
        ev = result[1]
        assert ev.user_event_name == "Drive Status"
        assert ev.element_type is not None
        assert ev.element_type.typedef_name == "Network Endpoint.ctl"
        assert ev.type_descriptor() == (
            "UserEvent refnum{Drive Status: Network Endpoint}"
        )

    def test_other_parametrized_refnums_carry_no_name(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="NumFloat64" Label="payload" />
            <TypeDesc Type="Refnum" RefType="Queue">
                <TypeDesc TypeID="0" />
            </TypeDesc>""",
        )
        assert result[1].user_event_name is None
        assert result[1].type_descriptor() == "Queue refnum{DBL}"

    def test_event_name_is_part_of_type_identity(self, tmp_path):
        result = self._vctp(
            tmp_path,
            """\
            <TypeDesc Type="Boolean" Label="a" />
            <TypeDesc Type="Boolean" Label="b" />
            <TypeDesc Type="Refnum" RefType="UserEvent">
                <TypeDesc TypeID="0" />
            </TypeDesc>
            <TypeDesc Type="Refnum" RefType="UserEvent">
                <TypeDesc TypeID="1" />
            </TypeDesc>""",
        )
        assert result[2] != result[3]


class TestSalvagedTypeDescriptors:
    """A descriptor the extraction could not export in full is marked, and the
    type says so."""

    def _vctp(self, tmp_path: Path, body: str) -> Path:
        xml = tmp_path / "vi.xml"
        xml.write_text(
            "<VI><VCTP><Section>" + body + "</Section></VCTP></VI>", encoding="utf-8"
        )
        return xml

    def test_a_corrupt_stub_is_an_unreadable_type(self, tmp_path):
        xml = self._vctp(
            tmp_path,
            '<TypeDesc Type="NumInt32" Format="inline" />'
            '<TypeDesc Type="Void" Format="corrupt-stub" />'
            '<TypeDesc Type="Refnum" RefType="Queue" Format="corrupt-stub" />',
        )
        types = parse_vctp_types(xml)
        assert types[0].unreadable is None
        assert types[1].unreadable is not None
        assert types[2].unreadable is not None
        assert types[2].ref_type == "Queue"
        assert "unreadable" in types[1].type_descriptor()
        assert types[0].type_descriptor() == "I32"

    def test_a_type_id_the_pool_does_not_name_carries_why_the_pool_failed(self):
        from lvkit.models import LVType, LVTypeKind
        from lvkit.parser.type_mapping import TypeMap
        from lvkit.parser.type_resolution import resolve_type_rich

        type_map = TypeMap({1: LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="X")})
        type_map.pool_failure = "the heap type pool is unreadable: cut off"
        assert resolve_type_rich("TypeID(1)", type_map).unreadable is None
        missing = resolve_type_rich("TypeID(2)", type_map)
        assert missing.unreadable == "the heap type pool is unreadable: cut off"
        assert resolve_type_rich("TypeID(2)", TypeMap()).unreadable is None
