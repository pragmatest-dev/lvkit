"""Graph builders for STRUCTURE nodes (loop / case / sequence / IPES).

Each handler takes a parsed structure node + the shared GraphBuildContext and
returns the typed graph StructureNode — the body is lifted verbatim from the
old _add_vi_to_graph if/elif chain (so output is byte-identical). The shared
post-dispatch in construction.py (nMux enrichment, g.add_node) is unchanged.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from lvkit.models import CaseFrame, EventFrame, SequenceFrame, Terminal
from lvkit.parser.models import ParsedNode

from ..models import (
    CaseStructureNode,
    DisableStructureNode,
    EventStructureNode,
    InPlaceNode,
    LoopNode,
    SequenceNode,
    StructureNode,
    WireEnd,
)
from .context import GraphBuildContext

logger = logging.getLogger(__name__)


class StructureBuildHandler(ABC):
    """Builds one kind of structure graph node from a parsed node + context."""

    node_types: tuple[str, ...]

    @abstractmethod
    def build(
        self,
        node: ParsedNode,
        node_name: str | None,
        q_node_uid: str,
        ctx: GraphBuildContext,
    ) -> StructureNode: ...


class LoopBuildHandler(StructureBuildHandler):
    node_types = ("whileLoop", "forLoop")

    def build(self, node, node_name, q_node_uid, ctx):
        loop_struct = ctx.loop_by_uid.get(node.uid)
        stop_cond: str | None = None
        stop_cond_inverted = False
        parallel = False
        parallel_static_workers: int | None = None
        hidden_border_terminals: frozenset[str] = frozenset()

        parser_tunnels: list = []
        if loop_struct:
            parser_tunnels = loop_struct.tunnels
            if loop_struct.stop_condition_terminal_uid:
                stop_cond = ctx.qid(loop_struct.stop_condition_terminal_uid)
            stop_cond_inverted = loop_struct.stop_condition_inverted
            parallel = loop_struct.parallel
            parallel_static_workers = loop_struct.parallel_static_workers
            hidden_border_terminals = loop_struct.hidden_border_terminals

        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
        )
        return LoopNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
            loop_type=node.node_type,
            stop_condition_terminal=stop_cond,
            stop_condition_inverted=stop_cond_inverted,
            parallel=parallel,
            parallel_static_workers=parallel_static_workers,
            hidden_border_terminals=hidden_border_terminals,
        )


class CaseBuildHandler(StructureBuildHandler):
    node_types = ("caseStruct", "select")

    def build(self, node, node_name, q_node_uid, ctx):
        case_struct = ctx.case_by_uid.get(node.uid)
        case_frames: list[CaseFrame] = []
        selector_term: str | None = None

        parser_tunnels: list = []
        if case_struct:
            parser_tunnels = case_struct.tunnels
            if case_struct.selector_terminal_uid:
                selector_term = ctx.qid(case_struct.selector_terminal_uid)
            case_frames = list(case_struct.frames)

        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
            case_frames=case_frames,
        )

        # Mark the selector terminal (see _add_vi_to_graph history).
        sel_uid = case_struct.selector_terminal_uid if case_struct else None
        if selector_term and sel_uid:
            existing = next(
                (t for t in structure_terminals if t.id == selector_term),
                None,
            )
            if existing:
                existing.name = "selector"
                sel_index = existing.index
            else:
                sel_ti = ctx.bd.terminal_info.get(sel_uid)
                sel_index = sel_ti.index if sel_ti else 0
                structure_terminals.append(
                    Terminal(
                        id=selector_term,
                        index=sel_index,
                        direction="input",
                        name="selector",
                    )
                )
            if sel_uid not in ctx.term_lookup:
                ctx.term_lookup[sel_uid] = WireEnd(
                    terminal_id=selector_term,
                    node_id=q_node_uid,
                    index=sel_index,
                    name="selector",
                )

        return CaseStructureNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
            frames=case_frames,
            selector_terminal=selector_term,
            displayed_frame=(case_struct.displayed_frame if case_struct else None),
            case_insensitive=(case_struct.case_insensitive if case_struct else False),
        )


class EventBuildHandler(StructureBuildHandler):
    """Builds an Event Structure — same frame-tunnel wiring as
    CaseBuildHandler (border tunnels use the identical per-frame ``selTun``-
    style shape — see parser/nodes/event.py), but no selector terminal: the
    active frame is chosen at runtime by whichever event fires, not a wire.
    """

    node_types = ("eventStruct",)

    def build(self, node, node_name, q_node_uid, ctx):
        event_struct = ctx.event_by_uid.get(node.uid)
        event_frames: list[EventFrame] = []
        displayed_frame: int | None = None

        parser_tunnels: list = []
        if event_struct:
            parser_tunnels = event_struct.tunnels
            event_frames = list(event_struct.frames)
            displayed_frame = event_struct.displayed_frame

        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
        )

        filter_node_uids = frozenset(
            ctx.qid(u) for u in (event_struct.filter_node_uids if event_struct else ())
        )

        return EventStructureNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
            frames=event_frames,
            displayed_frame=displayed_frame,
            filter_node_uids=filter_node_uids,
        )


class SequenceBuildHandler(StructureBuildHandler):
    node_types = ("flatSequence", "seq", "sequence")

    def build(self, node, node_name, q_node_uid, ctx):
        flat_seq = ctx.flatseq_by_uid.get(node.uid)
        seq_frames: list[SequenceFrame] = []

        parser_tunnels: list = []
        if flat_seq:
            parser_tunnels = flat_seq.tunnels
            seq_frames = list(flat_seq.frames)

        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
        )
        return SequenceNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
            frames=seq_frames,
            displayed_frame=(flat_seq.displayed_frame if flat_seq else None),
        )


class InPlaceBuildHandler(StructureBuildHandler):
    node_types = ("decomposeRecomposeStructure",)

    def build(self, node, node_name, q_node_uid, ctx):
        decompose_struct = ctx.decompose_by_uid.get(node.uid)
        if not decompose_struct:
            logger.warning(
                "VI %s: IPES %s not in parser structures — no tunnels extracted",
                ctx.vi_name,
                node.uid,
            )
        parser_tunnels = decompose_struct.tunnels if decompose_struct else []
        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
        )
        # Non-tunnel IPES data I/O terminals (decomposeClusterDCO) — not in
        # parser_tunnels but needed so codegen resolves the data variable.
        already_captured = {t.id for t in structure_terminals}
        for t_uid, t_info in ctx.bd.terminal_info.items():
            if t_info.parent_uid != node.uid:
                continue
            q_t_uid = ctx.qid(t_uid)
            if q_t_uid in already_captured:
                continue
            extra_lv_type = None
            if t_info.parsed_type:
                extra_lv_type = ctx.enrich_type(t_info.parsed_type)
            structure_terminals.append(
                Terminal(
                    id=q_t_uid,
                    direction="output" if t_info.is_output else "input",
                    index=t_info.index,
                    lv_type=extra_lv_type,
                    name=t_info.name,
                )
            )
            if t_uid not in ctx.term_lookup:
                ctx.term_lookup[t_uid] = WireEnd(
                    terminal_id=q_t_uid,
                    node_id=q_node_uid,
                    index=t_info.index,
                    name=t_info.name,
                )
        return InPlaceNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
        )


class DisableBuildHandler(StructureBuildHandler):
    """Builds a Diagram/Conditional Disable structure (class="commentNode").

    Same frame-tunnel wiring as CaseBuildHandler (reuses
    ``build_structure_terminals``'s ``case_frames=`` correlation, which is
    frame-model-agnostic despite the name), but produces a DisableStructureNode
    rather than a CaseStructureNode — see that type's docstring for why it
    must stay a distinct type instead of a case.
    """

    node_types = ("commentNode",)

    def build(self, node, node_name, q_node_uid, ctx):
        disable_struct = ctx.disable_by_uid.get(node.uid)
        disable_frames: list[CaseFrame] = []
        active_frame: int | None = None
        displayed_frame: int | None = None

        parser_tunnels: list = []
        if disable_struct:
            parser_tunnels = disable_struct.tunnels
            disable_frames = list(disable_struct.frames)
            active_frame = disable_struct.active_frame
            displayed_frame = disable_struct.displayed_frame

        structure_terminals = ctx.build_structure_terminals(
            parser_tunnels,
            q_node_uid,
            case_frames=disable_frames,
        )

        return DisableStructureNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=structure_terminals,
            frames=disable_frames,
            active_frame=active_frame,
            displayed_frame=displayed_frame,
        )


_HANDLERS: list[StructureBuildHandler] = [
    LoopBuildHandler(),
    CaseBuildHandler(),
    SequenceBuildHandler(),
    InPlaceBuildHandler(),
    DisableBuildHandler(),
    EventBuildHandler(),
]

STRUCTURE_BUILD_HANDLERS: dict[str, StructureBuildHandler] = {
    nt: h for h in _HANDLERS for nt in h.node_types
}
