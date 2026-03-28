"""Tests for type mapping and VCTP parsing (parser/type_mapping.py).

Covers parse_type_map_rich, parse_vctp_types (including recursive resolve_type
with cycle detection).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vipy.parser.type_mapping import parse_type_map_rich, parse_vctp_types

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TESTRESULT_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestResult"
TESTCASE_DIR = SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"
ADDERROR_VI = TESTRESULT_DIR / "addError.vi"
RUN_VI = TESTCASE_DIR / "run.vi"


def _get_main_xml(vi_path: Path) -> Path:
    """Extract VI and return path to main XML."""
    from vipy.extractor import extract_vi_xml

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
            lt for lt in type_map.values()
            if lt.kind == "cluster" and lt.fields
        ]
        assert len(cluster_types) > 0

        # At least one cluster should have the standard error fields
        error_clusters = [
            lt for lt in cluster_types
            if len(lt.fields) == 3
            and {f.name for f in lt.fields} == {"status", "code", "source"}
        ]
        assert len(error_clusters) > 0

    def test_enum_with_labels(self):
        """resultStatus enum has labels for test outcomes."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        enum_types = [
            lt for lt in type_map.values()
            if lt.values and "testPass" in lt.values
        ]
        assert len(enum_types) > 0

        enum = enum_types[0]
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

        array_types = [
            lt for lt in type_map.values()
            if lt.kind == "array"
        ]
        assert len(array_types) > 0

    def test_typedef_name_populated(self):
        """Typedefs should have typedef_name set (e.g., resultStatus--enum.ctl)."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        named_typedefs = [
            lt for lt in type_map.values()
            if lt.typedef_name and lt.typedef_name.endswith(".ctl")
        ]
        assert len(named_typedefs) > 0

    def test_typedef_wrapping_cluster_has_fields(self):
        """resultStatusChanged--Cluster.ctl wraps a cluster with fields."""
        main_xml = _get_main_xml(ADDERROR_VI)
        type_map = parse_type_map_rich(main_xml)

        cluster_typedefs = [
            lt for lt in type_map.values()
            if lt.typedef_name
            and "Cluster" in (lt.typedef_name or "")
            and lt.fields
        ]
        assert len(cluster_typedefs) > 0
        # Should have parseable field names
        td = cluster_typedefs[0]
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
            lt for lt in type_map.values()
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
