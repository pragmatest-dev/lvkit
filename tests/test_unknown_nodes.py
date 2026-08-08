"""Generic capture of unhandled node classes (#83).

Node-shaped block-diagram elements whose class isn't in the handled allowlist
(decimate, interLeave, extFunc, exprNode, …) used to be silently dropped by
_extract_nodes, leaving dangling wires. They are now captured generically so
they render as a labelled box with wired terminals; codegen fails loudly.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.parser.vi import _extract_nodes, _is_generic_operation_node


def _node_elem(root, cls, uid, n_terms=2):
    e = ET.SubElement(root, "SL__arrayElement", attrib={"class": cls, "uid": uid})
    ET.SubElement(e, "bounds").text = "(0, 0, 30, 30)"
    tl = ET.SubElement(e, "termList")
    for i in range(n_terms):
        ET.SubElement(tl, "SL__arrayElement", attrib={"uid": f"{uid}_{i}"})
    return e


class TestGenericNodeCapture:
    def test_unknown_node_class_is_captured(self):
        root = ET.Element("root")
        _node_elem(root, "decimate", "939")
        nodes = _extract_nodes(root)
        assert [n.node_type for n in nodes] == ["decimate"]
        assert nodes[0].uid == "939"

    def test_sequence_frame_not_captured_as_node(self):
        """sequenceFrame is a structure frame, not a dataflow node."""
        root = ET.Element("root")
        _node_elem(root, "sequenceFrame", "1")
        assert _extract_nodes(root) == []

    def test_comment_node_not_captured(self):
        root = ET.Element("root")
        _node_elem(root, "commentNode", "2")
        assert _extract_nodes(root) == []

    def test_element_without_bounds_or_termlist_ignored(self):
        root = ET.Element("root")
        e = ET.SubElement(root, "SL__arrayElement",
                          attrib={"class": "term", "uid": "3"})
        ET.SubElement(e, "objFlags").text = "0"
        assert _is_generic_operation_node(e) is False

    def test_known_class_not_double_captured(self):
        """A known operation class is captured once (by the allowlist pass),
        not again by the generic pass."""
        root = ET.Element("root")
        _node_elem(root, "prim", "10")
        nodes = _extract_nodes(root)
        assert len(nodes) == 1


class TestUnknownNodeCodegenFailsLoudly:
    def _op(self, node_type):
        from lvkit.models import PrimitiveOperation
        return PrimitiveOperation(
            id="vi::n", node_type=node_type, primResID=None,
            name=node_type, kind="primitive", terminals=[],
        )

    def test_generic_unknown_raises(self):
        from lvkit.codegen.context import CodeGenContext
        from lvkit.codegen.nodes import primitive
        from lvkit.primitive_resolver import PrimitiveResolutionNeeded
        ctx = CodeGenContext(graph=None, vi_name="vi")
        with pytest.raises(PrimitiveResolutionNeeded):
            primitive.generate(self._op("decimate"), ctx)

    def test_known_unimplemented_class_stays_empty(self):
        """concat is a known class; its missing handler is a separate gap and
        must NOT start raising from this change."""
        from lvkit.codegen.context import CodeGenContext
        from lvkit.codegen.nodes import primitive
        ctx = CodeGenContext(graph=None, vi_name="vi")
        frag = primitive.generate(self._op("concat"), ctx)
        assert frag.statements == []
