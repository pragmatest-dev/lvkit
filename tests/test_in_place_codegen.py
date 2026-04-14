"""Tests for In Place Element Structure (IPES) code generation.

IPES (decomposeRecomposeStructure) decomposes a cluster into fields, lets
inner operations modify them, then recomposes. In Python this is transparent:
decompose → field access bindings, recompose → write-back assignments.

decompose_ops and recompose_ops are boundary operations stored on
InPlaceOperation directly (not in inner_nodes). Classification happens at
the operations layer (_classify_ipes_ops in graph/operations.py).
"""

from __future__ import annotations

import ast

from lvkit.codegen.nodes import in_place
from lvkit.graph.operations import _classify_ipes_ops
from lvkit.models import (
    ClusterField,
    InPlaceOperation,
    LVType,
    PrimitiveOperation,
    Terminal,
    Tunnel,
)
from tests.helpers import make_ctx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster_type(*field_names: str) -> LVType:
    """Build a cluster LVType with named fields."""
    return LVType(kind="cluster", fields=[ClusterField(name=n) for n in field_names])


def _dec_op(
    poser_uid: str,
    agg_id: str,
    *field_out_ids: str,
    lv_type: LVType | None = None,
) -> PrimitiveOperation:
    """Build a decompose op: agg input + list outputs (input boundary)."""
    terminals = [
        Terminal(
            id=agg_id, index=0, direction="input", nmux_role="agg", lv_type=lv_type,
        ),
    ]
    for i, fid in enumerate(field_out_ids):
        terminals.append(
            Terminal(
                id=fid, index=i + 1, direction="output",
                nmux_role="list", nmux_field_index=i,
            )
        )
    return PrimitiveOperation(
        id=f"dec_{poser_uid}", name="Decompose", labels=[], poser_uid=poser_uid,
        terminals=terminals,
    )


def _rec_op(
    poser_uid: str,
    agg_out_id: str,
    *field_in_ids: str,
    lv_type: LVType | None = None,
) -> PrimitiveOperation:
    """Build a recompose op: list inputs + agg output (output boundary)."""
    terminals: list[Terminal] = []
    for i, fid in enumerate(field_in_ids):
        terminals.append(
            Terminal(
                id=fid, index=i, direction="input",
                nmux_role="list", nmux_field_index=i,
            )
        )
    terminals.append(
        Terminal(
            id=agg_out_id, index=len(field_in_ids), direction="output",
            nmux_role="agg", lv_type=lv_type,
        )
    )
    return PrimitiveOperation(
        id=f"rec_{poser_uid}", name="Recompose", labels=[], poser_uid=poser_uid,
        terminals=terminals,
    )


# ---------------------------------------------------------------------------
# Input boundary: tunnel propagation
# ---------------------------------------------------------------------------


class TestIPESInputTunnels:
    """Input tunnels propagate outer value to inner terminal (input boundary)."""

    def test_input_tunnel_propagates_outer_to_inner(self):
        ctx = make_ctx("outer_in", "inner_in")
        ctx.bind("outer_in", "my_var")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="outer_in", index=0, direction="input")],
            tunnels=[Tunnel(
                outer_terminal_uid="outer_in", inner_terminal_uid="inner_in",
                tunnel_type="lpTun",
            )],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("inner_in") == "my_var"

    def test_input_tunnel_outer_not_in_fragment_bindings(self):
        """Input tunnel outers must NOT appear in CodeFragment.bindings.

        The output boundary only exports output-direction tunnels. Input
        tunnels are already propagated inward and must not be re-exported.
        """
        ctx = make_ctx("outer_in", "inner_in")
        ctx.bind("outer_in", "my_var")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="outer_in", index=0, direction="input")],
            tunnels=[Tunnel(
                outer_terminal_uid="outer_in", inner_terminal_uid="inner_in",
                tunnel_type="lpTun",
            )],
        )
        frag = in_place.generate(node, ctx)

        assert "outer_in" not in frag.bindings

    def test_unbound_input_tunnel_leaves_inner_unbound(self):
        """If outer is not bound, inner stays unbound — no phantom bindings."""
        ctx = make_ctx("outer_in", "inner_in")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="outer_in", index=0, direction="input")],
            tunnels=[Tunnel(
                outer_terminal_uid="outer_in", inner_terminal_uid="inner_in",
                tunnel_type="lpTun",
            )],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("inner_in") is None


# ---------------------------------------------------------------------------
# Output boundary: tunnel binding
# ---------------------------------------------------------------------------


class TestIPESOutputTunnels:
    """Output tunnels bind inner resolved value to outer terminal (output boundary)."""

    def test_output_tunnel_binds_inner_to_outer(self):
        ctx = make_ctx("outer_out", "inner_out")
        ctx.bind("inner_out", "computed_val")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="outer_out", index=0, direction="output")],
            tunnels=[Tunnel(
                outer_terminal_uid="outer_out", inner_terminal_uid="inner_out",
                tunnel_type="lpTun",
            )],
        )
        frag = in_place.generate(node, ctx)

        assert frag.bindings.get("outer_out") == "computed_val"

    def test_unresolved_output_tunnel_not_in_bindings(self):
        """If inner terminal has no value, outer is not added to bindings."""
        ctx = make_ctx("outer_out", "inner_out")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="outer_out", index=0, direction="output")],
            tunnels=[Tunnel(
                outer_terminal_uid="outer_out", inner_terminal_uid="inner_out",
                tunnel_type="lpTun",
            )],
        )
        frag = in_place.generate(node, ctx)

        assert "outer_out" not in frag.bindings

    def test_input_and_output_tunnels_independent(self):
        """Input and output tunnels on the same IPES are handled separately."""
        ctx = make_ctx("in_outer", "in_inner", "out_outer", "out_inner")
        ctx.bind("in_outer", "pass_val")
        ctx.bind("out_inner", "result_val")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[
                Terminal(id="in_outer", index=0, direction="input"),
                Terminal(id="out_outer", index=1, direction="output"),
            ],
            tunnels=[
                Tunnel(
                    outer_terminal_uid="in_outer", inner_terminal_uid="in_inner",
                    tunnel_type="lpTun",
                ),
                Tunnel(
                    outer_terminal_uid="out_outer", inner_terminal_uid="out_inner",
                    tunnel_type="lpTun",
                ),
            ],
        )
        frag = in_place.generate(node, ctx)

        assert "in_outer" not in frag.bindings
        assert frag.bindings.get("out_outer") == "result_val"
        assert ctx.resolve("in_inner") == "pass_val"


# ---------------------------------------------------------------------------
# Input boundary: decompose field binding
# ---------------------------------------------------------------------------


class TestIPESDecomposeFieldBinding:
    """Decompose ops bind field output terminals to cluster.field expressions."""

    def test_field_output_bound_to_cluster_field(self):
        lv_type = _cluster_type("x", "y")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_field_x")
        ctx.bind("cluster_in", "my_cluster")

        dec = _dec_op("p1", "dec_agg", "dec_field_x", lv_type=lv_type)
        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("dec_field_x") == "my_cluster.x"

    def test_multiple_fields_bound_by_index(self):
        lv_type = _cluster_type("alpha", "beta", "gamma")
        ctx = make_ctx("cluster_in", "dec_agg", "f0", "f1", "f2")
        ctx.bind("cluster_in", "obj")

        dec = _dec_op("p1", "dec_agg", "f0", "f1", "f2", lv_type=lv_type)
        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("f0") == "obj.alpha"
        assert ctx.resolve("f1") == "obj.beta"
        assert ctx.resolve("f2") == "obj.gamma"

    def test_missing_cluster_var_skips_decompose(self):
        """If the cluster can't be resolved, no bindings are set."""
        lv_type = _cluster_type("x")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_field_x")
        # cluster_in deliberately not bound

        dec = _dec_op("p1", "dec_agg", "dec_field_x", lv_type=lv_type)
        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("dec_field_x") is None


# ---------------------------------------------------------------------------
# Output boundary: recompose agg pre-bind and write-back
# ---------------------------------------------------------------------------


class TestIPESRecomposeWriteBack:
    """Recompose pre-binds agg output and emits write-back (output boundary)."""

    def test_recompose_emits_field_assignment(self):
        lv_type = _cluster_type("count")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_f", "rec_f", "rec_agg_out")
        ctx.bind("cluster_in", "my_cluster")
        ctx.bind("rec_f", "new_count")

        dec = _dec_op("p1", "dec_agg", "dec_f", lv_type=lv_type)
        rec = _rec_op("p1", "rec_agg_out", "rec_f", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
            recompose_ops=[rec],
        )
        frag = in_place.generate(node, ctx)

        code = "\n".join(
            ast.unparse(ast.fix_missing_locations(s)) for s in frag.statements
        )
        assert "my_cluster.count = new_count" in code

    def test_recompose_agg_output_pre_bound_to_cluster(self):
        """Recompose agg output is pre-bound to cluster var (same data, no copy)."""
        lv_type = _cluster_type("value")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_f", "rec_f", "rec_agg_out")
        ctx.bind("cluster_in", "the_cluster")

        dec = _dec_op("p1", "dec_agg", "dec_f", lv_type=lv_type)
        rec = _rec_op("p1", "rec_agg_out", "rec_f", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
            recompose_ops=[rec],
        )
        in_place.generate(node, ctx)

        assert ctx.resolve("rec_agg_out") == "the_cluster"

    def test_recompose_skipped_if_no_matching_decompose(self):
        """Recompose with no matching poser_uid produces no write-back."""
        lv_type = _cluster_type("x")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_f", "rec_f", "rec_agg_out")
        ctx.bind("cluster_in", "my_cluster")
        ctx.bind("rec_f", "val")

        dec = _dec_op("p1", "dec_agg", "dec_f", lv_type=lv_type)
        # different poser_uid → no match → no write-back
        rec = _rec_op("p2", "rec_agg_out", "rec_f", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
            recompose_ops=[rec],
        )
        frag = in_place.generate(node, ctx)

        assert not frag.statements

    def test_multiple_fields_written_back(self):
        lv_type = _cluster_type("a", "b")
        ctx = make_ctx(
            "cluster_in", "dec_agg",
            "dec_a", "dec_b", "rec_a", "rec_b", "rec_agg_out",
        )
        ctx.bind("cluster_in", "obj")
        ctx.bind("rec_a", "new_a")
        ctx.bind("rec_b", "new_b")

        dec = _dec_op("p1", "dec_agg", "dec_a", "dec_b", lv_type=lv_type)
        rec = _rec_op("p1", "rec_agg_out", "rec_a", "rec_b", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_in", index=0, direction="input")],
            tunnels=[],
            decompose_ops=[dec],
            recompose_ops=[rec],
        )
        frag = in_place.generate(node, ctx)

        code = "\n".join(
            ast.unparse(ast.fix_missing_locations(s)) for s in frag.statements
        )
        assert "obj.a = new_a" in code
        assert "obj.b = new_b" in code


# ---------------------------------------------------------------------------
# Output boundary: cluster output terminal fallback
# ---------------------------------------------------------------------------


class TestIPESFallbackCluster:
    """decomposeClusterDCO output terminals get fallback cluster binding."""

    def test_non_tunnel_output_bound_to_cluster(self):
        """decomposeClusterDCO has no graph edge — must be explicitly bound."""
        lv_type = _cluster_type("value")
        ctx = make_ctx("cluster_in", "dec_agg", "dec_f", "cluster_out")
        ctx.bind("cluster_in", "my_cluster")

        dec = _dec_op("p1", "dec_agg", "dec_f", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[
                Terminal(id="cluster_in", index=0, direction="input"),
                Terminal(id="cluster_out", index=1, direction="output"),
            ],
            tunnels=[],
            decompose_ops=[dec],
        )
        frag = in_place.generate(node, ctx)

        assert frag.bindings.get("cluster_out") == "my_cluster"

    def test_tunnel_outer_not_overwritten_by_fallback(self):
        """Output tunnel outer must NOT be overwritten by the cluster fallback."""
        lv_type = _cluster_type("value")
        ctx = make_ctx(
            "cluster_in", "dec_agg", "dec_f",
            "out_outer", "out_inner", "cluster_out",
        )
        ctx.bind("cluster_in", "my_cluster")
        ctx.bind("out_inner", "field_value")

        dec = _dec_op("p1", "dec_agg", "dec_f", lv_type=lv_type)

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[
                Terminal(id="cluster_in", index=0, direction="input"),
                Terminal(id="out_outer", index=1, direction="output"),
                Terminal(id="cluster_out", index=2, direction="output"),
            ],
            tunnels=[Tunnel(
                outer_terminal_uid="out_outer", inner_terminal_uid="out_inner",
                tunnel_type="lpTun",
            )],
            decompose_ops=[dec],
        )
        frag = in_place.generate(node, ctx)

        assert frag.bindings.get("out_outer") == "field_value"
        assert frag.bindings.get("cluster_out") == "my_cluster"

    def test_no_fallback_when_no_decompose(self):
        """Without any decompose op, fallback_cluster is None — nothing bound."""
        ctx = make_ctx("cluster_out")

        node = InPlaceOperation(
            id="ipes", name="IPES", labels=[],
            terminals=[Terminal(id="cluster_out", index=0, direction="output")],
            tunnels=[],
        )
        frag = in_place.generate(node, ctx)

        assert "cluster_out" not in frag.bindings


# ---------------------------------------------------------------------------
# _classify_ipes_ops (operations layer, not codegen)
# ---------------------------------------------------------------------------


class TestClassifyIpesOps:
    """_classify_ipes_ops in operations.py separates decompose/recompose/regular."""

    def test_classify_decompose_has_list_out(self):
        dec = _dec_op("p1", "agg_in", "f_out")
        decompose, recompose, regular = _classify_ipes_ops([dec])
        assert len(decompose) == 1
        assert not recompose
        assert not regular

    def test_classify_recompose_has_list_in(self):
        rec = _rec_op("p1", "agg_out", "f_in")
        decompose, recompose, regular = _classify_ipes_ops([rec])
        assert not decompose
        assert len(recompose) == 1
        assert not regular

    def test_classify_no_poser_uid_is_regular(self):
        op = PrimitiveOperation(
            id="op1", name="Add", labels=[], poser_uid=None,
            terminals=[Terminal(id="t1", index=0, direction="input")],
        )
        decompose, recompose, regular = _classify_ipes_ops([op])
        assert not decompose
        assert not recompose
        assert len(regular) == 1

    def test_classify_passthrough_list_both_directions_is_regular(self):
        """Op with list_in AND list_out is ambiguous — treated as regular."""
        op = PrimitiveOperation(
            id="op1", name="PassThrough", labels=[], poser_uid="p1",
            terminals=[
                Terminal(id="t_in", index=0, direction="input", nmux_role="list"),
                Terminal(id="t_out", index=1, direction="output", nmux_role="list"),
            ],
        )
        decompose, recompose, regular = _classify_ipes_ops([op])
        assert not decompose
        assert not recompose
        assert len(regular) == 1

    def test_classify_mixed_set(self):
        dec = _dec_op("p1", "agg_in", "f_out")
        rec = _rec_op("p1", "agg_out", "f_in")
        regular_op = PrimitiveOperation(id="op1", name="Add", labels=[], terminals=[])
        decompose, recompose, regular = _classify_ipes_ops([dec, rec, regular_op])
        assert len(decompose) == 1
        assert len(recompose) == 1
        assert len(regular) == 1
