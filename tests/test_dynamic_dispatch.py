"""Tests for dynamic dispatch (dynIUse) support.

VIPI/DyOM extraction from XML. How a dispatch call is generated (``obj.method``,
``super().method``, ``Class.method``) is tested with real classes in
``test_class_codegen.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.vi import _extract_subvi_info, _resolve_qualified_name


class TestVIPIExtraction:
    """Test extraction of VIPI entries from XML."""

    def _make_xml(self, inner: str) -> ET.Element:
        """Build a minimal XML tree with LIvi section."""
        xml_str = f"<Root><LIvi>{inner}</LIvi></Root>"
        return ET.fromstring(xml_str)

    def test_vipi_with_class_and_method(self):
        """VIPI with class + method extracts qualified name."""
        root = self._make_xml("""
            <VIPI>
                <LinkSaveQualName>
                    <String>TestResult.lvclass</String>
                    <String>addSuccess.vi</String>
                </LinkSaveQualName>
            </VIPI>
        """)
        qnames, _, _ = _extract_subvi_info(root, None)
        assert "TestResult.lvclass:addSuccess.vi" in qnames

    def test_dyom_entries_extracted(self):
        """DyOM entries are also extracted."""
        root = self._make_xml("""
            <DyOM>
                <LinkSaveQualName>
                    <String>TestCase.lvclass</String>
                    <String>run.vi</String>
                </LinkSaveQualName>
            </DyOM>
        """)
        qnames, _, _ = _extract_subvi_info(root, None)
        assert "TestCase.lvclass:run.vi" in qnames

    def test_vipi_and_dyom_together(self):
        """Both VIPI and DyOM entries are extracted."""
        root = self._make_xml("""
            <VIPI>
                <LinkSaveQualName>
                    <String>TestResult.lvclass</String>
                    <String>addSuccess.vi</String>
                </LinkSaveQualName>
            </VIPI>
            <DyOM>
                <LinkSaveQualName>
                    <String>TestCase.lvclass</String>
                    <String>run.vi</String>
                </LinkSaveQualName>
            </DyOM>
        """)
        qnames, _, _ = _extract_subvi_info(root, None)
        assert "TestResult.lvclass:addSuccess.vi" in qnames
        assert "TestCase.lvclass:run.vi" in qnames

    def test_control_characters_stripped(self):
        """Control characters are stripped from qualified names."""
        # Build XML element programmatically to avoid XML parser rejecting
        # control chars in string literals
        vipi = ET.Element("VIPI")
        lsqn = ET.SubElement(vipi, "LinkSaveQualName")
        s1 = ET.SubElement(lsqn, "String")
        s1.text = "TestResult.lvclass"
        s2 = ET.SubElement(lsqn, "String")
        s2.text = "\x01\x0daddSuccess.vi"

        qname = _resolve_qualified_name(vipi, caller_library=None, encoding="cp1252")
        assert qname == "TestResult.lvclass:addSuccess.vi"

    def test_vipi_with_same_library_flag(self):
        """VIPI with LinkSaveFlag=2 qualifies with caller's library."""
        root = self._make_xml("""
            <VIPI LinkSaveFlag="2">
                <LinkSaveQualName>
                    <String>doStuff.vi</String>
                </LinkSaveQualName>
            </VIPI>
        """)
        qnames, _, _ = _extract_subvi_info(root, "MyLib.lvlib:Caller.vi")
        assert "MyLib.lvlib:doStuff.vi" in qnames

    def test_vipi_no_link_save_qual_name(self):
        """VIPI without LinkSaveQualName is skipped."""
        root = self._make_xml("<VIPI></VIPI>")
        qnames, _, _ = _extract_subvi_info(root, None)
        # Should not crash, just returns empty
        assert all("VIPI" not in q for q in qnames)
