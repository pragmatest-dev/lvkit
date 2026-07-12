"""Shared state a graph-build handler needs, threaded from _add_vi_to_graph.

Carries the mutable per-VI build state (the graph, the terminal lookup, the
parser-structure indexes) plus a handle to the ConstructionMixin for its
helper methods. Typed as ``Any`` for the mixin to avoid a construction↔build
import cycle — build/ is imported BY construction, never the reverse.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lvkit.models import Terminal
from lvkit.parser.models import ParsedBlockDiagram


@dataclass
class GraphBuildContext:
    mixin: Any                    # ConstructionMixin (_qid/_build_*/_enrich_type)
    bd: ParsedBlockDiagram
    vi_name: str
    term_lookup: dict[str, Any]   # terminal_uid -> WireEnd (mutated in place)
    loop_by_uid: dict[str, Any]
    case_by_uid: dict[str, Any]
    flatseq_by_uid: dict[str, Any]
    decompose_by_uid: dict[str, Any]
    iuse_to_qname: dict[str, str]   # iUse uid -> qualified callee name
    iuse_to_qpath: dict[str, str]   # iUse uid -> qualified on-disk path

    # --- thin pass-throughs to the mixin helpers (keep call sites readable) ---
    def qid(self, uid: str) -> str:
        return self.mixin._qid(self.vi_name, uid)

    def resolve_vi_name(self, name: str) -> str:
        return self.mixin.resolve_vi_name(name)

    @property
    def graph(self) -> Any:
        return self.mixin._graph

    def build_structure_terminals(
        self, tunnels: list, q_node_uid: str, **kwargs: Any,
    ) -> list[Terminal]:
        return self.mixin._build_structure_terminals(
            self.bd, tunnels, q_node_uid, self.term_lookup, self.vi_name,
            **kwargs,
        )

    def enrich_type(self, parsed_type: Any) -> Any:
        return self.mixin._enrich_type(parsed_type)
