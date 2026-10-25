"""Graph builders for the early REFERENCE nodes (ctlRefConst / gRef / statVIRef).

Unlike the node PRODUCERS (structures/operations), these are handled BEFORE the
shared terminal-building block and fully resolve the node — aliasing an FP
terminal, adding a LocalVariable/Constant node, mutating term_lookup/the graph —
then the loop skips normal building. So the handler returns ``True`` when it has
taken the node (the caller ``continue``s), ``False`` to fall through.

Bodies lifted verbatim from _add_vi_to_graph's ctlRefConst/gRef/statVIRef blocks
(byte-identical output).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.parser.node_types import CtlRefConstNode, GRefNode, StatVIRefNode

from ..models import (
    ConstantNode,
    GlobalVariableNode,
    GlobalVIRef,
    LocalVariableNode,
    WireEnd,
)
from .context import GraphBuildContext

logger = logging.getLogger(__name__)


class RefBuildHandler(ABC):
    """Fully handles a reference-style node (mutating the graph in place) and
    returns True; returns False to fall through to normal node building."""

    @abstractmethod
    def handle(
        self,
        node: Any,
        q_node_uid: str,
        ctx: GraphBuildContext,
    ) -> bool: ...


class CtlRefConstHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, CtlRefConstNode):
            return False
        # A control-reference constant is a REAL on-diagram object drawn at its
        # own position, wired locally to whatever consumes it. Model it as a
        # LocalVariableNode (read) at that position — the SAME shape gRef uses —
        # rather than aliasing its terminal onto the referenced FP terminal.
        #
        # The old alias (term_lookup[own] = fp_wire_end) collapsed the constant
        # away and re-homed its wire onto the FP terminal, which is drawn
        # wherever that terminal sits (often inside an unrelated structure) — so
        # a wire local to one diagram appeared to cross structure borders with
        # no tunnel. Faithful surfaces (render/describe/diff) must see the
        # constant where it lives; the FP-value dataflow is preserved for
        # codegen by the synthetic (non-drawn) edge below, exactly as for gRef.
        #
        # A built-in reference constant ("This VI", "This Application", …) has no
        # ddo_uid: it references a built-in server, not an FP control. It is
        # STILL a real on-diagram object with an output terminal wired to a
        # consumer, so it must be modelled and drawn — otherwise its wire (drawn
        # from the compressed wire table) lands on a terminal with no node box, a
        # render "void" with a wire floating into empty space. Only the
        # FP-control resolution is ddo-gated; the node is built exactly like a
        # control-ref constant, minus the synthetic FP-value edge (guarded by
        # ``fp_wire_end is not None`` below, which stays None here).
        fp_wire_end = None
        if node.ddo_uid:
            fpdco_uid = ctx.ddo_to_fpdco.get(node.ddo_uid)
            if fpdco_uid:
                for fp_term in ctx.bd.fp_terminals:
                    if fp_term.fp_dco_uid == fpdco_uid:
                        fp_wire_end = ctx.term_lookup.get(fp_term.uid)
                        break

        # This node's own terminal (its reference output).
        own_term_uid = None
        own_t_info = None
        for term_uid, t_info in ctx.bd.terminal_info.items():
            if t_info.parent_uid == node.uid:
                own_term_uid = term_uid
                own_t_info = t_info
                break

        control_name = node.name or "Control Reference"
        is_write = bool(own_t_info) and not own_t_info.is_output
        direction = "input" if is_write else "output"
        lv_type = None
        if own_t_info and own_t_info.parsed_type:
            lv_type = ctx.enrich_type(own_t_info.parsed_type)

        q_own_term_uid = ctx.qid(own_term_uid) if own_term_uid else q_node_uid
        own_terminal = Terminal(
            id=q_own_term_uid,
            index=0,
            direction=direction,
            name=control_name,
            lv_type=lv_type,
        )
        local_var_node = LocalVariableNode(
            id=q_node_uid,
            vi_path=ctx.vi_name,
            name=control_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=[own_terminal],
            control_name=control_name,
            control_terminal_id=(fp_wire_end.terminal_id if fp_wire_end else None),
            is_write=is_write,
        )
        ctx.graph.add_node(q_node_uid, node=local_var_node)
        ctx.vi_node_uids.add(q_node_uid)

        if own_term_uid:
            my_wire_end = WireEnd(
                terminal_id=q_own_term_uid,
                node_id=q_node_uid,
                index=0,
                name=control_name,
            )
            ctx.term_lookup[own_term_uid] = my_wire_end
            # Synthetic (non-drawn) dataflow edge to the referenced control,
            # direction-matched to read vs write — preserves the FP-value
            # dataflow codegen consumed under the old alias.
            if fp_wire_end is not None:
                if is_write:
                    ctx.graph.add_edge(
                        q_node_uid,
                        ctx.vi_name,
                        source=my_wire_end,
                        dest=fp_wire_end,
                    )
                else:
                    ctx.graph.add_edge(
                        ctx.vi_name,
                        q_node_uid,
                        source=fp_wire_end,
                        dest=my_wire_end,
                    )
        return True


@dataclass(frozen=True)
class _VariableTerminal:
    """The one terminal of a local or global variable node: its heap uid (None
    when the node has no terminal element), whether it writes the control (an
    input terminal) and the type its wire carries."""

    term_uid: str | None
    is_write: bool
    lv_type: LVType | None


def _variable_terminal(node: GRefNode, ctx: GraphBuildContext) -> _VariableTerminal:
    for term_uid, t_info in ctx.bd.terminal_info.items():
        if t_info.parent_uid == node.uid:
            lv_type = (
                ctx.enrich_type(t_info.parsed_type) if t_info.parsed_type else None
            )
            return _VariableTerminal(term_uid, not t_info.is_output, lv_type)
    return _VariableTerminal(None, False, None)


class LocalVariableHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, GRefNode) or node.is_global_variable:
            return False
        fp = ctx.fp
        fp_wire_end = (
            ctx.param_wire_ends.get(node.param_idx)
            if node.param_idx is not None
            else None
        )
        control_name: str | None = None
        if fp is not None and node.param_idx is not None:
            uid = fp.control_uid_at_param_idx(node.param_idx)
            control_name = next(
                (ctrl.name for ctrl in fp.controls if ctrl.uid == uid), None
            )

        if fp_wire_end is None:
            logger.debug(
                "VI %s: gRef %s param_idx=%s did not resolve to a "
                "front-panel control — creating with fallback name",
                ctx.vi_name,
                node.uid,
                node.param_idx,
            )

        var_term = _variable_terminal(node, ctx)
        gref_term_uid = var_term.term_uid
        is_write = var_term.is_write
        direction = "input" if is_write else "output"

        q_gref_term_uid = ctx.qid(gref_term_uid) if gref_term_uid else q_node_uid
        gref_terminal = Terminal(
            id=q_gref_term_uid,
            index=0,
            direction=direction,
            name=control_name,
            lv_type=var_term.lv_type,
        )
        local_var_node = LocalVariableNode(
            id=q_node_uid,
            vi_path=ctx.vi_name,
            name=control_name or node.name or "Local Variable",
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=[gref_terminal],
            control_name=control_name,
            control_terminal_id=(fp_wire_end.terminal_id if fp_wire_end else None),
            is_write=is_write,
        )
        ctx.graph.add_node(q_node_uid, node=local_var_node)
        ctx.vi_node_uids.add(q_node_uid)

        if gref_term_uid:
            my_wire_end = WireEnd(
                terminal_id=q_gref_term_uid,
                node_id=q_node_uid,
                index=0,
                name=control_name,
            )
            ctx.term_lookup[gref_term_uid] = my_wire_end
            # Synthetic (non-drawn) dataflow edge to the referenced control,
            # direction-matched to read vs write.
            if fp_wire_end is not None:
                if is_write:
                    ctx.graph.add_edge(
                        q_node_uid,
                        ctx.vi_name,
                        source=my_wire_end,
                        dest=fp_wire_end,
                    )
                else:
                    ctx.graph.add_edge(
                        ctx.vi_name,
                        q_node_uid,
                        source=fp_wire_end,
                        dest=my_wire_end,
                    )
        return True


class GlobalVariableResolutionError(ValueError):
    """A global variable node whose Global VI or control cannot be read from the
    calling VI's own records."""


def _connector_field(
    node: GRefNode, ctx: GraphBuildContext
) -> tuple[int, ClusterField]:
    """The control index and the field of the node's connector cluster (the
    Global VI's controls, as this VI last saw them) that ``param_idx`` selects."""
    where = f"VI {ctx.vi_name}: global variable node {node.uid}"
    assert node.connector_tm_type_id is not None
    cluster = ctx.type_map.get(node.connector_tm_type_id)
    if cluster is None or cluster.kind != LVTypeKind.CLUSTER or not cluster.fields:
        raise GlobalVariableResolutionError(
            f"{where}: connector type TypeID({node.connector_tm_type_id}) is not "
            "a cluster of the Global VI's controls"
        )
    if node.param_idx is None or not 0 <= node.param_idx < len(cluster.fields):
        raise GlobalVariableResolutionError(
            f"{where}: control index {node.param_idx} is outside the "
            f"{len(cluster.fields)} controls its connector type lists"
        )
    return node.param_idx, cluster.fields[node.param_idx]


def _global_vi_ref(node: GRefNode, ctx: GraphBuildContext) -> GlobalVIRef:
    """The Global VI the calling VI's block-diagram link table names for ``node``."""
    ref = ctx.iuse_refs.get(node.uid)
    if ref is None or ref.qualified_name is None:
        raise GlobalVariableResolutionError(
            f"VI {ctx.vi_name}: global variable node {node.uid} has no record in "
            "the block-diagram link table, so its Global VI is unknown"
        )
    return GlobalVIRef(
        qualified_name=ref.qualified_name, path_tokens=tuple(ref.path_tokens)
    )


def _check_global_control(
    ctx: GraphBuildContext, ref: GlobalVIRef, param_idx: int, control_name: str
) -> None:
    """Compare the control the node records with the control the loaded Global VI
    has at the same position of its front-panel control list, and report a
    mismatch: the Global VI changed since this VI last saw it."""
    key = ctx.mixin._dep_key_for_ref(ref.qualified_name, ctx.vi_name)
    if key is None or key not in ctx.graph:
        return
    global_node = ctx.graph.nodes[key].get("node")
    fp = getattr(global_node, "front_panel", None)
    if fp is None:
        return
    uid = fp.control_uid_at_param_idx(param_idx)
    if uid is None:
        logger.warning(
            "VI %s: global variable %s::%s (control %d) — %s has no control at "
            "that position; the Global VI changed since this VI was saved",
            ctx.vi_name,
            ref.qualified_name,
            control_name,
            param_idx,
            ref.qualified_name,
        )
        return
    actual = next((c.name for c in fp.controls if c.uid == uid), None)
    if actual != control_name:
        logger.warning(
            "VI %s: global variable %s::%s (control %d) — %s has control %r at "
            "that position; the Global VI changed since this VI was saved",
            ctx.vi_name,
            ref.qualified_name,
            control_name,
            param_idx,
            ref.qualified_name,
            actual,
        )


class GlobalVariableHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, GRefNode) or not node.is_global_variable:
            return False
        param_idx, field = _connector_field(node, ctx)
        global_vi = _global_vi_ref(node, ctx)
        _check_global_control(ctx, global_vi, param_idx, field.name)

        var_term = _variable_terminal(node, ctx)
        direction = "input" if var_term.is_write else "output"
        q_term_uid = ctx.qid(var_term.term_uid) if var_term.term_uid else q_node_uid
        global_node = GlobalVariableNode(
            id=q_node_uid,
            vi_path=ctx.vi_name,
            name=field.name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=[
                Terminal(
                    id=q_term_uid,
                    index=0,
                    direction=direction,
                    name=field.name,
                    lv_type=var_term.lv_type,
                )
            ],
            global_vi=global_vi,
            control_name=field.name,
            param_idx=param_idx,
            lv_type=field.type,
            is_write=var_term.is_write,
        )
        ctx.graph.add_node(q_node_uid, node=global_node)
        ctx.vi_node_uids.add(q_node_uid)
        if var_term.term_uid:
            ctx.term_lookup[var_term.term_uid] = WireEnd(
                terminal_id=q_term_uid,
                node_id=q_node_uid,
                index=0,
                name=field.name,
            )
        return True


class GRefHandler(RefBuildHandler):
    """A ``gRef`` node is a local or a global variable; each handler takes the
    nodes of its own kind."""

    _HANDLERS = (GlobalVariableHandler(), LocalVariableHandler())

    def handle(self, node, q_node_uid, ctx):
        return any(h.handle(node, q_node_uid, ctx) for h in self._HANDLERS)


class StatVIRefHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, StatVIRefNode):
            return False
        vi_ref_name = node.name
        if not vi_ref_name or vi_ref_name == "Static VI Reference":
            logger.warning(
                "VI %s: statVIRef %s has no label — skipping",
                ctx.vi_name,
                node.uid,
            )
            return True
        vi_ref_type = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="VIRefnum")
        const_node = ConstantNode(
            id=q_node_uid,
            vi_path=ctx.vi_name,
            value=vi_ref_name,
            lv_type=vi_ref_type,
            raw_value=vi_ref_name,
            referenced_vi_key=ctx.iuse_to_callee_key.get(node.uid),
            label=vi_ref_name,
            terminals=[
                Terminal(
                    id=q_node_uid,
                    index=0,
                    direction="output",
                    lv_type=vi_ref_type,
                )
            ],
        )
        ctx.graph.add_node(q_node_uid, node=const_node)
        ctx.vi_node_uids.add(q_node_uid)
        for term_uid, t_info in ctx.bd.terminal_info.items():
            if t_info.parent_uid == node.uid:
                ctx.term_lookup[term_uid] = WireEnd(
                    terminal_id=q_node_uid,
                    node_id=q_node_uid,
                    index=0,
                    name=vi_ref_name,
                )
                break
        return True


REF_BUILD_HANDLERS: dict[str, RefBuildHandler] = {
    "ctlRefConst": CtlRefConstHandler(),
    "gRef": GRefHandler(),
    "statVIRef": StatVIRefHandler(),
}
