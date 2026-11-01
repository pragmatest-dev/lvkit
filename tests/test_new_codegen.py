"""Tests for codegen features: error handling, nMux roles, property dedup,
passthrough elimination, expression inlining, enum sanitization."""

from __future__ import annotations

import ast

from lvkit.codegen.builder import topological_sort_tiered
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_path import is_error_selector
from lvkit.codegen.nodes import case, invoke_node, nmux, property_node
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import CaseStructureNode, PrimitiveNode, Wire, WireEnd
from lvkit.models import (
    CaseFrame,
    ClusterField,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
from tests.helpers import (
    error_cluster_type,
    make_graph_with_edge,
    make_graph_with_terminals,
    make_node,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _error_cluster_type() -> LVType:
    """LVType representing a LabVIEW error cluster."""
    return error_cluster_type()


def _make_case_op(
    selector_id: str,
    selector_type: LVType | None = None,
    frames: list[CaseFrame] | None = None,
) -> CaseStructureNode:
    """Build a case structure graph node with a selector terminal."""
    terminals = [
        Terminal(
            id=selector_id,
            index=0,
            direction="input",
            name="selector",
            lv_type=selector_type,
        ),
    ]
    return CaseStructureNode(
        id="case_1",
        vi_path="test.vi",
        name="Case Structure",
        node_type="caseStruct",
        terminals=terminals,
        selector_terminal=selector_id,
        frames=frames
        or [
            CaseFrame(selector_value="True"),
            CaseFrame(selector_value="False"),
        ],
    )


def _make_ctx_with_binding(terminal_id: str, var_name: str) -> CodeGenContext:
    graph = make_graph_with_terminals(terminal_id)
    ctx = CodeGenContext(graph=graph)
    ctx.bind(terminal_id, var_name)
    return ctx


# ── Error selector detection ────────────────────────────────────────


class TestErrorSelectorByType:
    """is_error_selector detects error clusters via LVType fields."""

    def test_detects_error_cluster(self):
        op = _make_case_op("sel_1", _error_cluster_type())
        assert is_error_selector(op) is True

    def test_rejects_boolean(self):
        bool_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
        op = _make_case_op("sel_1", bool_type)
        assert is_error_selector(op) is False

    def test_rejects_no_type(self):
        op = _make_case_op("sel_1", None)
        assert is_error_selector(op) is False

    def test_rejects_cluster_without_error_fields(self):
        cluster_type = LVType(
            kind=LVTypeKind.CLUSTER,
            underlying_type="Cluster",
            fields=[ClusterField(name="x"), ClusterField(name="y")],
        )
        op = _make_case_op("sel_1", cluster_type)
        assert is_error_selector(op) is False


# ── Error case unwrap ───────────────────────────────────────────────


class TestErrorCaseUnwrap:
    """Error-cluster case structures emit only the no-error frame body."""

    def test_empty_error_frame_emits_no_error_body(self):
        op = _make_case_op(
            "sel_1",
            _error_cluster_type(),
            frames=[
                CaseFrame(selector_value="False", operations=[]),
                CaseFrame(selector_value="True", operations=[]),
            ],
        )
        ctx = _make_ctx_with_binding("sel_1", "err")
        fragment = case.generate(op, ctx)
        # No statements for empty frames
        assert fragment.statements == []

    def test_nonempty_error_frame_is_not_generated(self):
        from lvkit.graph.models import VINode
        from tests.helpers import register_nodes

        inner_op = VINode(
            id="cleanup",
            vi_path="test.vi",
            name="cleanup.vi",
            node_type="iUse",
            terminals=[],
            parent="case_1",
            frame="True",
        )
        op = _make_case_op(
            "sel_1",
            _error_cluster_type(),
            frames=[
                CaseFrame(selector_value="False"),
                CaseFrame(selector_value="True"),
            ],
        )
        ctx = _make_ctx_with_binding("sel_1", "err")
        register_nodes(ctx.graph, [op, inner_op])
        assert case.generate(op, ctx).statements == []


# ── Error input not bound ──────────────────────────────────────────


class TestErrorInputNotBound:
    """from_vi_context should NOT bind error cluster inputs."""

    def test_error_input_skipped(self):
        from lvkit.graph.models import VIContext

        error_term = Terminal(
            id="err_in",
            index=0,
            direction="input",
            name="error in (no error)",
            lv_type=_error_cluster_type(),
        )
        normal_term = Terminal(
            id="data_in",
            index=1,
            direction="input",
            name="data",
            lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
        )
        vi_ctx = VIContext(
            name="test.vi",
            inputs=[error_term, normal_term],
            outputs=[],
            operations=[],
            constants=[],
        )
        ctx = CodeGenContext.from_vi_context(vi_ctx)
        assert ctx.resolve("err_in") is None  # NOT bound
        assert ctx.resolve("data_in") == "data"  # bound


# ── nMux role-based passthrough ────────────────────────────────────


class TestNMuxRoles:
    """nMux codegen uses terminal roles (agg/list) instead of index guessing."""

    def _make_nmux_op(self, terminals: list[Terminal]) -> PrimitiveNode:
        return PrimitiveNode(
            id="nmux_1",
            vi_path="test.vi",
            name="Node Multiplexer",
            node_type="nMux",
            terminals=terminals,
        )

    def test_agg_passthrough(self):
        """AGG in + AGG out = pure passthrough."""
        op = self._make_nmux_op(
            [
                Terminal(id="agg_in", index=0, direction="input", nmux_role="agg"),
                Terminal(id="agg_out", index=1, direction="output", nmux_role="agg"),
            ]
        )
        ctx = _make_ctx_with_binding("agg_in", "my_cluster")
        # Wire agg_in → agg_out
        assert ctx.graph is not None
        ctx.graph._graph.add_edge(
            ctx.graph._term_to_node["agg_in"],
            "nmux_node",
            source=WireEnd(
                terminal_id="agg_in", node_id=ctx.graph._term_to_node["agg_in"]
            ),
            dest=WireEnd(terminal_id="agg_out", node_id="nmux_node"),
        )
        fragment = nmux.generate(op, ctx)
        assert fragment.statements == []  # Pure binding
        assert fragment.bindings.get("agg_out") == "my_cluster"

    def test_list_passthrough(self):
        """LIST in + LIST out = field value passthrough."""
        op = self._make_nmux_op(
            [
                Terminal(id="agg_in", index=0, direction="input", nmux_role="agg"),
                Terminal(id="list_in", index=1, direction="input", nmux_role="list"),
                Terminal(id="list_out", index=2, direction="output", nmux_role="list"),
            ]
        )
        graph = make_graph_with_terminals("agg_in", "list_in", "list_out")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("agg_in", "cluster")
        ctx.bind("list_in", "field_val")
        fragment = nmux.generate(op, ctx)
        assert fragment.bindings.get("list_out") == "field_val"

    def test_no_roles_produces_no_bindings(self):
        """Without roles, nMux produces no bindings (roles set by construction)."""
        op = self._make_nmux_op(
            [
                Terminal(id="in_0", index=0, direction="input"),
                Terminal(id="out_0", index=1, direction="output"),
            ]
        )
        ctx = _make_ctx_with_binding("in_0", "value")
        fragment = nmux.generate(op, ctx)
        assert fragment.bindings == {}


# ── Property node dedup ────────────────────────────────────────────


class TestPropertyReads:
    """Each saved property row executes exactly once."""

    def test_three_rows_with_one_wired_result(self):
        """Unwired read results do not remove their property accesses."""
        graph = make_graph_with_terminals("ref_in", "out_1", "out_2", "out_3")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("ref_in", "my_ref")

        # Wire ref_in → out_1 so out_1 is wired
        assert ctx.graph is not None
        nid_ref = ctx.graph._term_to_node["ref_in"]
        nid_out = ctx.graph._term_to_node["out_1"]
        ctx.graph._graph.add_edge(
            nid_ref,
            nid_out,
            source=WireEnd(terminal_id="ref_in", node_id=nid_ref),
            dest=WireEnd(terminal_id="out_1", node_id=nid_out),
        )

        op = PrimitiveNode(
            id="prop_1",
            vi_path="test.vi",
            name="Property Node",
            node_type="propNode",
            terminals=[
                Terminal(id="ref_in", index=0, direction="input"),
                Terminal(id="out_1", index=4, direction="output"),
                Terminal(id="out_2", index=5, direction="output"),
                Terminal(id="out_3", index=6, direction="output"),
            ],
            properties=[
                PropertyDef(segments=[PropertySegment(name="controls", code=0)]),
                PropertyDef(segments=[PropertySegment(name="indicator", code=0)]),
                PropertyDef(segments=[PropertySegment(name="value", code=0)]),
            ],
            property_value_terminal_ids=["out_1", "out_2", "out_3"],
        )
        fragment = property_node.generate(op, ctx)
        assigns = [s for s in fragment.statements if isinstance(s, ast.Assign)]
        assert len(assigns) == 3
        assert [
            s.value.attr for s in assigns if isinstance(s.value, ast.Attribute)
        ] == ["controls", "indicator", "value"]

    def test_each_property_uses_its_own_terminal(self):
        """Write ActiveDev, then read DevIsSimulated / ProductType / BusType
        with only the last two wired (DCAF Config.vi's DAQmx Device node):
        each wired terminal gets ITS property, not the next one in line."""
        ctx = CodeGenContext.from_wires(
            [
                Wire.from_terminals(
                    from_terminal_id="ref_src", to_terminal_id="ref_in"
                ),
                Wire.from_terminals(from_terminal_id="name_src", to_terminal_id="dev"),
                Wire.from_terminals(from_terminal_id="prod", to_terminal_id="sink_1"),
                Wire.from_terminals(from_terminal_id="bus", to_terminal_id="sink_2"),
            ]
        )
        ctx.bind("ref_src", "device")
        ctx.bind("name_src", "device_name")
        op = PrimitiveNode(
            id="prop_1",
            vi_path="test.vi",
            name="Property Node",
            node_type="propNode",
            terminals=[
                Terminal(id="ref_in", index=0, direction="input"),
                Terminal(id="dev", index=4, direction="input"),
                Terminal(id="sim", index=5, direction="output"),
                Terminal(id="prod", index=6, direction="output"),
                Terminal(id="bus", index=7, direction="output"),
            ],
            properties=[
                PropertyDef(segments=[PropertySegment(name=name, code=0)])
                for name in ("ActiveDev", "DevIsSimulated", "ProductType", "BusType")
            ],
            property_value_terminal_ids=["dev", "sim", "prod", "bus"],
        )
        fragment = property_node.generate(op, ctx)
        module = ast.Module(body=fragment.statements, type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(module)).splitlines()
        assert code == [
            "device.activedev = device_name",
            "device_devissimulated = device.devissimulated",
            "device_producttype = device.producttype",
            "device_bustype = device.bustype",
        ]
        assert fragment.bindings["prod"] == "device_producttype"
        assert fragment.bindings["bus"] == "device_bustype"


class TestNestedPropertyChain:
    """A nested property (``Label.Text``) is an attribute chain."""

    def _node_and_ctx(self, direction: str):
        graph = make_graph_with_terminals("ref_in", "val")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("ref_in", "ref")
        ctx.bind("val", "'hello'")
        assert ctx.graph is not None
        nid_ref = ctx.graph._term_to_node["ref_in"]
        nid_val = ctx.graph._term_to_node["val"]
        src, dst = ("ref_in", "val") if direction == "output" else ("val", "ref_in")
        nid_src, nid_dst = (nid_ref, nid_val) if src == "ref_in" else (nid_val, nid_ref)
        ctx.graph._graph.add_edge(
            nid_src,
            nid_dst,
            source=WireEnd(terminal_id=src, node_id=nid_src),
            dest=WireEnd(terminal_id=dst, node_id=nid_dst),
        )
        op = PrimitiveNode(
            id="prop_1",
            vi_path="test.vi",
            name="Property Node",
            node_type="propNode",
            terminals=[
                Terminal(id="ref_in", index=0, direction="input"),
                Terminal(
                    id="val",
                    index=1,
                    direction="input" if direction == "input" else "output",
                ),
            ],
            properties=[
                PropertyDef(
                    segments=[
                        PropertySegment(name="Label", code=104013829),
                        PropertySegment(name="Text", code=103995392),
                    ]
                )
            ],
            property_value_terminal_ids=["val"],
        )
        return op, ctx

    def test_read_emits_attribute_chain(self):
        op, ctx = self._node_and_ctx("output")
        fragment = property_node.generate(op, ctx)
        source = ast.unparse(
            ast.fix_missing_locations(
                ast.Module(body=fragment.statements, type_ignores=[])
            )
        )
        assert source == "ref_label_text = ref.label.text"

    def test_write_emits_attribute_chain_and_executes(self):
        op, ctx = self._node_and_ctx("input")
        fragment = property_node.generate(op, ctx)
        source = ast.unparse(
            ast.fix_missing_locations(
                ast.Module(body=fragment.statements, type_ignores=[])
            )
        )
        assert "ref.label.text = " in source

        class _Label:
            text = ""

        class _Ref:
            label = _Label()

        ref = _Ref()
        exec(source, {"ref": ref})
        assert ref.label.text == "hello"


# ── Passthrough elimination ────────────────────────────────────────


class TestPassthroughElimination:
    """Primitive passthroughs (in_N → out) create bindings, not assignments."""

    def test_passthrough_detected(self):
        from lvkit.codegen.nodes import primitive

        # Primitive with python_code: {"output": "in_0"} — pure passthrough
        graph = make_graph_with_edge("src_t", "in_t")
        # Add output terminal
        out_node = make_node("out_node", ["out_t"])
        graph._graph.add_node("out_node", node=out_node)
        graph._term_to_node["out_t"] = "out_node"
        graph._graph.add_edge(
            "p2",
            "out_node",
            source=WireEnd(terminal_id="in_t", node_id="p2"),
            dest=WireEnd(terminal_id="out_t", node_id="out_node"),
        )

        ctx = CodeGenContext(graph=graph)
        ctx.bind("src_t", "my_input")

        hint = {"output": "in_0"}
        input_map = {"in_0": "my_input"}

        op = PrimitiveNode(
            id="prim_1",
            vi_path="test.vi",
            name="Passthrough",
            node_type="prim",
            prim_id=9999,
            terminals=[
                Terminal(id="in_t", index=0, direction="input"),
                Terminal(id="out_t", index=0, direction="output"),
            ],
        )

        bindings, skip_ids = primitive._detect_passthroughs(
            op,
            hint,
            input_map,
            ctx,
            None,
        )
        assert "out_t" in bindings
        assert bindings["out_t"] == "my_input"
        assert "out_t" in skip_ids


# ── Enum name sanitization ─────────────────────────────────────────


class TestEnumSanitization:
    """derive_python_name handles hyphens and special characters."""

    def test_hyphens_become_camelcase(self):
        from lvkit.vilib_resolver import derive_python_name

        assert derive_python_name("Method--Type") == "MethodType"

    def test_underscores_become_camelcase(self):
        from lvkit.vilib_resolver import derive_python_name

        assert derive_python_name("file_mode") == "FileMode"

    def test_ctl_extension_stripped(self):
        from lvkit.vilib_resolver import derive_python_name

        assert derive_python_name("Method.ctl") == "Method"

    def test_empty_string(self):
        from lvkit.vilib_resolver import derive_python_name

        assert derive_python_name("") == "UnknownType"


# ── Selector terminal topo sort ────────────────────────────────────


class TestSelectorTopoSort:
    """Case structure selector terminal creates a topo sort dependency."""

    def test_selector_source_ordered_before_case(self):
        """The node producing the selector should be in an earlier tier."""
        graph = InMemoryVIGraph()

        # Producer: Equal? primitive with output terminal
        producer = PrimitiveNode(
            id="equal_1",
            vi_path="test.vi",
            name="Equal?",
            terminals=[
                Terminal(id="eq_in", index=0, direction="input"),
                Terminal(id="eq_out", index=0, direction="output"),
            ],
        )
        graph._graph.add_node("equal_1", node=producer)
        graph._term_to_node["eq_in"] = "equal_1"
        graph._term_to_node["eq_out"] = "equal_1"

        # Consumer: case structure with selector wired from Equal? output
        case = CaseStructureNode(
            id="case_1",
            vi_path="test.vi",
            name="Case",
            node_type="caseStruct",
            terminals=[
                Terminal(id="sel_in", index=0, direction="input", name="selector"),
            ],
            selector_terminal="sel_in",
        )
        graph._graph.add_node("case_1", node=case)
        graph._term_to_node["sel_in"] = "case_1"

        # Wire: eq_out → sel_in
        graph._graph.add_edge(
            "equal_1",
            "case_1",
            source=WireEnd(terminal_id="eq_out", node_id="equal_1"),
            dest=WireEnd(terminal_id="sel_in", node_id="case_1"),
        )

        # Build operations
        producer_op = PrimitiveNode(
            id="equal_1",
            vi_path="test.vi",
            name="Equal?",
            node_type="prim",
            prim_id=1091,
            terminals=[
                Terminal(id="eq_in", index=0, direction="input"),
                Terminal(id="eq_out", index=0, direction="output"),
            ],
        )
        case_op = CaseStructureNode(
            id="case_1",
            vi_path="test.vi",
            name="Case",
            node_type="caseStruct",
            terminals=[
                Terminal(id="sel_in", index=0, direction="input", name="selector"),
            ],
            selector_terminal="sel_in",
            frames=[
                CaseFrame(selector_value="True"),
                CaseFrame(selector_value="False"),
            ],
        )

        ctx = CodeGenContext(graph=graph)
        tiers = topological_sort_tiered([producer_op, case_op], ctx)

        # Producer should be in an earlier tier than the case
        producer_tier = next(
            i for i, tier in enumerate(tiers) if any(op.id == "equal_1" for op in tier)
        )
        case_tier = next(
            i for i, tier in enumerate(tiers) if any(op.id == "case_1" for op in tier)
        )
        assert producer_tier < case_tier


# ── Invoke node error skip ─────────────────────────────────────────


class TestInvokeErrorSkip:
    """Invoke node codegen skips error cluster terminal arguments."""

    def test_error_arg_not_in_call(self):

        graph = InMemoryVIGraph()
        # Create source nodes that wire into the invoke terminals
        for tid, nid in [
            ("ref_t", "src_ref"),
            ("err_t", "src_err"),
            ("data_t", "src_data"),
        ]:
            node = make_node(nid, [tid])
            graph._graph.add_node(nid, node=node)
            graph._term_to_node[tid] = nid
        # Create the invoke node with its terminals
        invoke_gnode = make_node("invoke_1", ["ref_t_in", "err_t_in", "data_t_in"])
        graph._graph.add_node("invoke_1", node=invoke_gnode)
        for tid in ("ref_t_in", "err_t_in", "data_t_in"):
            graph._term_to_node[tid] = "invoke_1"
        # Wire sources into invoke terminals
        for src, dst, src_nid in [
            ("ref_t", "ref_t_in", "src_ref"),
            ("err_t", "err_t_in", "src_err"),
            ("data_t", "data_t_in", "src_data"),
        ]:
            graph._graph.add_edge(
                src_nid,
                "invoke_1",
                source=WireEnd(terminal_id=src, node_id=src_nid),
                dest=WireEnd(terminal_id=dst, node_id="invoke_1"),
            )

        ctx = CodeGenContext(graph=graph)
        ctx.bind("ref_t", "my_ref")
        ctx.bind("err_t", "error_in")
        ctx.bind("data_t", "my_data")

        op = PrimitiveNode(
            id="invoke_1",
            vi_path="test.vi",
            name="Invoke",
            node_type="invokeNode",
            terminals=[
                Terminal(id="ref_t_in", index=0, direction="input"),
                Terminal(
                    id="err_t_in",
                    index=2,
                    direction="input",
                    lv_type=_error_cluster_type(),
                ),
                Terminal(
                    id="data_t_in",
                    index=4,
                    direction="input",
                    lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String"),
                ),
            ],
            method_name="Ctrl Val.Set",
        )

        fragment = invoke_node.generate(op, ctx)

        # Unparse the generated call
        code = ast.unparse(
            ast.fix_missing_locations(
                ast.Module(body=fragment.statements, type_ignores=[])
            )
        )
        assert "error_in" not in code
        assert "my_data" in code
