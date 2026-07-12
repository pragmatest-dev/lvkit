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
from typing import Any

from lvkit.models import LVType, Terminal
from lvkit.parser.node_types import CtlRefConstNode, GRefNode, StatVIRefNode

from ..models import ConstantNode, LocalVariableNode, WireEnd
from .context import GraphBuildContext

logger = logging.getLogger(__name__)


class RefBuildHandler(ABC):
    """Fully handles a reference-style node (mutating the graph in place) and
    returns True; returns False to fall through to normal node building."""

    @abstractmethod
    def handle(
        self, node: Any, q_node_uid: str, ctx: GraphBuildContext,
    ) -> bool:
        ...


class CtlRefConstHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, CtlRefConstNode):
            return False
        # Alias this node's output terminal to the referenced FP terminal so
        # downstream wires connect to the FP variable. No graph node created.
        # Built-in refs (ddo_uid absent) are deferred — skip silently.
        if node.ddo_uid:
            fpdco_uid = ctx.ddo_to_fpdco.get(node.ddo_uid)
            if fpdco_uid:
                fp_wire_end = None
                for fp_term in ctx.bd.fp_terminals:
                    if fp_term.fp_dco_uid == fpdco_uid:
                        fp_wire_end = ctx.term_lookup.get(fp_term.uid)
                        break
                if fp_wire_end:
                    for term_uid, t_info in ctx.bd.terminal_info.items():
                        if t_info.parent_uid == node.uid:
                            ctx.term_lookup[term_uid] = fp_wire_end
                            break
        return True


class GRefHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, GRefNode):
            return False
        fp = ctx.fp
        fp_wire_end = (
            ctx.param_wire_ends.get(node.param_idx)
            if node.param_idx is not None
            else None
        )
        control_name: str | None = None
        if (
            fp is not None
            and node.param_idx is not None
            and 0 <= node.param_idx < len(fp.controls)
        ):
            control_name = fp.controls[node.param_idx].name

        if fp_wire_end is None:
            logger.debug(
                "VI %s: gRef %s param_idx=%s did not resolve to a "
                "front-panel control — creating with fallback name",
                ctx.vi_name, node.uid, node.param_idx,
            )

        gref_term_uid: str | None = None
        gref_t_info = None
        for term_uid, t_info in ctx.bd.terminal_info.items():
            if t_info.parent_uid == node.uid:
                gref_term_uid = term_uid
                gref_t_info = t_info
                break

        is_write = bool(gref_t_info) and not gref_t_info.is_output
        direction = "input" if is_write else "output"
        lv_type = None
        if gref_t_info and gref_t_info.parsed_type:
            lv_type = ctx.enrich_type(gref_t_info.parsed_type)

        q_gref_term_uid = ctx.qid(gref_term_uid) if gref_term_uid else q_node_uid
        gref_terminal = Terminal(
            id=q_gref_term_uid, index=0, direction=direction,
            name=control_name, lv_type=lv_type,
        )
        local_var_node = LocalVariableNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=control_name or node.name or "Local Variable",
            node_type=node.node_type,
            terminals=[gref_terminal],
            control_name=control_name,
            control_terminal_id=(
                fp_wire_end.terminal_id if fp_wire_end else None
            ),
            is_write=is_write,
        )
        ctx.graph.add_node(q_node_uid, node=local_var_node)
        ctx.vi_node_uids.add(q_node_uid)

        if gref_term_uid:
            my_wire_end = WireEnd(
                terminal_id=q_gref_term_uid, node_id=q_node_uid,
                index=0, name=control_name,
            )
            ctx.term_lookup[gref_term_uid] = my_wire_end
            # Synthetic (non-drawn) dataflow edge to the referenced control,
            # direction-matched to read vs write (write not yet consumed by
            # codegen — see history).
            if fp_wire_end is not None:
                if is_write:
                    ctx.graph.add_edge(
                        q_node_uid, ctx.vi_name,
                        source=my_wire_end, dest=fp_wire_end,
                    )
                else:
                    ctx.graph.add_edge(
                        ctx.vi_name, q_node_uid,
                        source=fp_wire_end, dest=my_wire_end,
                    )
        return True


class StatVIRefHandler(RefBuildHandler):
    def handle(self, node, q_node_uid, ctx):
        if not isinstance(node, StatVIRefNode):
            return False
        vi_ref_name = node.name
        if not vi_ref_name or vi_ref_name == "Static VI Reference":
            logger.warning(
                "VI %s: statVIRef %s has no label — skipping",
                ctx.vi_name, node.uid,
            )
            return True
        vi_ref_type = LVType(kind="primitive", underlying_type="VIRefnum")
        const_node = ConstantNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            value=vi_ref_name,
            lv_type=vi_ref_type,
            raw_value=vi_ref_name,
            label=vi_ref_name,
            terminals=[Terminal(
                id=q_node_uid, index=0,
                direction="output", lv_type=vi_ref_type,
            )],
        )
        ctx.graph.add_node(q_node_uid, node=const_node)
        ctx.vi_node_uids.add(q_node_uid)
        for term_uid, t_info in ctx.bd.terminal_info.items():
            if t_info.parent_uid == node.uid:
                ctx.term_lookup[term_uid] = WireEnd(
                    terminal_id=q_node_uid, node_id=q_node_uid,
                    index=0, name=vi_ref_name,
                )
                break
        return True


REF_BUILD_HANDLERS: dict[str, RefBuildHandler] = {
    "ctlRefConst": CtlRefConstHandler(),
    "gRef": GRefHandler(),
    "statVIRef": StatVIRefHandler(),
}
