"""Tests for dynamic dispatch (dynIUse) support.

Part 1: VIPI/DyOM extraction from XML
Part 2: Dynamic dispatch code generation (obj.method pattern)
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET

from vipy.agent.codegen.context import CodeGenContext
from vipy.agent.codegen.nodes.subvi import SubVICodeGen
from vipy.graph_types import LVType, Operation, Terminal, Wire
from vipy.parser.vi import _extract_subvi_info, _resolve_qualified_name


def _unparse(stmt: ast.stmt) -> str:
    """Unparse an AST statement, fixing missing locations first."""
    ast.fix_missing_locations(stmt)
    return ast.unparse(stmt)


# === Part 1: VIPI/DyOM extraction ===


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
        s2.text = "\x01\x0DaddSuccess.vi"

        qname = _resolve_qualified_name(vipi, caller_library=None)
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


# === Part 2: Dynamic dispatch code generation ===


def _make_ctx(
    bindings: dict[str, str] | None = None,
    wires: list[Wire] | None = None,
) -> CodeGenContext:
    """Create a CodeGenContext with pre-set bindings."""
    ctx = CodeGenContext(data_flow=wires or [])
    if bindings:
        for term_id, value in bindings.items():
            ctx.bind(term_id, value)
    return ctx


def _make_dynIUse_node(
    name: str = "addSuccess.vi",
    inputs: list[Terminal] | None = None,
    outputs: list[Terminal] | None = None,
) -> Operation:
    """Create a dynIUse Operation node."""
    terminals = list(inputs or []) + list(outputs or [])
    return Operation(
        id="node_1",
        name=name,
        labels=["SubVI"],
        node_type="dynIUse",
        terminals=terminals,
    )


class TestDynamicDispatchCodegen:
    """Test dynamic dispatch code generation."""

    def test_method_call_shape(self):
        """dynIUse generates obj.method(args) not func(obj, args)."""
        node = _make_dynIUse_node(
            name="addSuccess.vi",
            inputs=[
                Terminal(id="t_in_0", index=0, direction="input",
                         lv_type=LVType(kind="primitive", ref_type="UDClassInst")),
                Terminal(id="t_in_1", index=1, direction="input"),
            ],
            outputs=[
                Terminal(id="t_out_0", index=2, direction="output",
                         lv_type=LVType(kind="primitive", ref_type="UDClassInst")),
            ],
        )
        ctx = _make_ctx({"t_in_0": "test_result", "t_in_1": "test_name"})
        gen = SubVICodeGen()
        frag = gen.generate(node, ctx)

        assert len(frag.statements) >= 1
        code = _unparse(frag.statements[0])
        # to_function_name("addSuccess.vi") → "addsuccess"
        assert "test_result.addsuccess" in code
        assert "test_name" in code

    def test_receiver_by_lv_type(self):
        """UDClassInst terminal is used as receiver."""
        node = _make_dynIUse_node(
            name="doWork.vi",
            inputs=[
                Terminal(id="t_str", index=0, direction="input"),
                Terminal(id="t_obj", index=1, direction="input",
                         lv_type=LVType(kind="primitive", ref_type="UDClassInst")),
            ],
        )
        ctx = _make_ctx({"t_str": "my_string", "t_obj": "my_object"})
        gen = SubVICodeGen()
        frag = gen.generate(node, ctx)

        code = _unparse(frag.statements[0])
        # Object should be receiver, string should be arg
        assert "my_object.dowork" in code
        assert "my_string" in code

    def test_receiver_fallback_first_input(self):
        """When no UDClassInst lv_type, first input is used as receiver."""
        node = _make_dynIUse_node(
            name="doWork.vi",
            inputs=[
                Terminal(id="t_0", index=0, direction="input"),
                Terminal(id="t_1", index=1, direction="input"),
            ],
        )
        ctx = _make_ctx({"t_0": "first_input", "t_1": "second_input"})
        gen = SubVICodeGen()
        frag = gen.generate(node, ctx)

        code = _unparse(frag.statements[0])
        assert "first_input.dowork" in code
        assert "second_input" in code

    def test_class_output_passthrough(self):
        """Class-typed output binds to receiver variable (passthrough)."""
        node = _make_dynIUse_node(
            name="addSuccess.vi",
            inputs=[
                Terminal(id="t_in", index=0, direction="input",
                         lv_type=LVType(kind="primitive", ref_type="UDClassInst")),
            ],
            outputs=[
                Terminal(id="t_out", index=1, direction="output",
                         lv_type=LVType(kind="primitive", ref_type="UDClassInst")),
                Terminal(id="t_out2", index=2, direction="output",
                         name="count"),
            ],
        )
        ctx = _make_ctx({"t_in": "test_result"})
        gen = SubVICodeGen()
        frag = gen.generate(node, ctx)

        # Class output should pass through to receiver
        assert frag.bindings["t_out"] == "test_result"
        # Non-class output should use result_var.field
        assert "addsuccess_result" in frag.bindings["t_out2"]

    def test_no_vilib_resolution_needed(self):
        """dynIUse never raises VILibResolutionNeeded."""
        node = _make_dynIUse_node(
            name="unknownMethod.vi",
            inputs=[
                Terminal(id="t_in", index=0, direction="input"),
            ],
            outputs=[
                Terminal(id="t_out", index=1, direction="output"),
            ],
        )
        ctx = _make_ctx({"t_in": "obj"})
        gen = SubVICodeGen()
        # Should NOT raise VILibResolutionNeeded
        frag = gen.generate(node, ctx)
        assert frag.statements

    def test_no_inputs_static_fallback(self):
        """dynIUse with no wired inputs falls back to static function call."""
        node = _make_dynIUse_node(
            name="getVersion.vi",
            outputs=[
                Terminal(id="t_out", index=0, direction="output", name="version"),
            ],
        )
        ctx = _make_ctx({})
        gen = SubVICodeGen()
        frag = gen.generate(node, ctx)

        code = _unparse(frag.statements[0])
        # Should be static call, not method call
        assert "getversion()" in code

    def test_is_class_terminal(self):
        """_is_class_terminal correctly identifies UDClassInst."""
        gen = SubVICodeGen()
        class_term = Terminal(
            id="t1", index=0, direction="input",
            lv_type=LVType(kind="primitive", ref_type="UDClassInst"),
        )
        non_class_term = Terminal(
            id="t2", index=1, direction="input",
            lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
        )
        no_type_term = Terminal(id="t3", index=2, direction="input")

        assert gen._is_class_terminal(class_term) is True
        assert gen._is_class_terminal(non_class_term) is False
        assert gen._is_class_terminal(no_type_term) is False
