"""Diff two versions of a LabVIEW VI by comparing their graph representations."""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models import (
    CaseOperation,
    LoopOperation,
    Operation,
    SequenceOperation,
    Terminal,
)
from .describe import describe_vi
from .models import Constant, Wire

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


# ── Report dataclasses ────────────────────────────────────────────────


@dataclass
class SignatureChange:
    category: str  # "added", "removed", "type_changed"
    direction: str  # "input" or "output"
    name: str
    old_type: str | None = None
    new_type: str | None = None


@dataclass
class OperationChange:
    category: str  # "added", "removed"
    name: str
    node_type: str | None = None


@dataclass
class ConstantChange:
    category: str  # "added", "removed", "value_changed"
    name: str
    old_value: str | None = None
    new_value: str | None = None


@dataclass
class WiringChange:
    category: str  # "added", "removed"
    description: str  # "NodeA -> NodeB"


@dataclass
class StructureChange:
    category: str  # "added", "removed", "changed"
    name: str
    details: str | None = None


@dataclass
class DiffReport:
    signature: list[SignatureChange] = field(default_factory=list)
    operations: list[OperationChange] = field(default_factory=list)
    constants: list[ConstantChange] = field(default_factory=list)
    wiring: list[WiringChange] = field(default_factory=list)
    structures: list[StructureChange] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.signature, self.operations, self.constants,
            self.wiring, self.structures,
        ])

    def format(self) -> str:
        sections: list[str] = []

        if self.signature:
            lines = ["Signature:"]
            for sig in self.signature:
                if sig.category == "added":
                    lines.append(f"  + {sig.direction}: {sig.name} ({sig.new_type})")
                elif sig.category == "removed":
                    lines.append(f"  - {sig.direction}: {sig.name} ({sig.old_type})")
                elif sig.category == "type_changed":
                    lines.append(
                        f"  ~ {sig.direction}: {sig.name}:"
                        f" {sig.old_type} -> {sig.new_type}"
                    )
            sections.append("\n".join(lines))

        if self.operations:
            lines = ["Operations:"]
            for op in self.operations:
                tag = "+" if op.category == "added" else "-"
                node_label = f" [{op.node_type}]" if op.node_type else ""
                lines.append(f"  {tag} {op.name}{node_label}")
            sections.append("\n".join(lines))

        if self.constants:
            lines = ["Constants:"]
            for con in self.constants:
                if con.category == "added":
                    lines.append(f"  + {con.name} = {con.new_value}")
                elif con.category == "removed":
                    lines.append(f"  - {con.name} = {con.old_value}")
                elif con.category == "value_changed":
                    lines.append(f"  ~ {con.name}: {con.old_value} -> {con.new_value}")
            sections.append("\n".join(lines))

        if self.wiring:
            lines = ["Wiring:"]
            for w in self.wiring:
                tag = "+" if w.category == "added" else "-"
                lines.append(f"  {tag} {w.description}")
            sections.append("\n".join(lines))

        if self.structures:
            lines = ["Structures:"]
            for s in self.structures:
                if s.category in ("added", "removed"):
                    tag = "+" if s.category == "added" else "-"
                    lines.append(f"  {tag} {s.name}")
                else:
                    lines.append(f"  ~ {s.name}: {s.details}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)


# ── Short form: text diff ─────────────────────────────────────────────


def diff_text(
    graph_a: InMemoryVIGraph,
    graph_b: InMemoryVIGraph,
    vi_name_a: str,
    vi_name_b: str,
    *,
    label_a: str = "a",
    label_b: str = "b",
) -> str:
    """Unified text diff of two VI descriptions."""
    text_a = describe_vi(graph_a, vi_name_a)
    text_b = describe_vi(graph_b, vi_name_b)

    diff_lines = list(difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile=label_a,
        tofile=label_b,
    ))
    return "".join(diff_lines)


# ── Long form: structured diff ────────────────────────────────────────


def diff_structured(
    graph_a: InMemoryVIGraph,
    graph_b: InMemoryVIGraph,
    vi_name_a: str,
    vi_name_b: str,
) -> DiffReport:
    """Compare two VIs and return a categorized change report."""
    vi_name_a = graph_a.resolve_vi_name(vi_name_a)
    vi_name_b = graph_b.resolve_vi_name(vi_name_b)

    report = DiffReport()
    report.signature = _diff_signature(graph_a, graph_b, vi_name_a, vi_name_b)
    report.operations = _diff_operations(graph_a, graph_b, vi_name_a, vi_name_b)
    report.constants = _diff_constants(graph_a, graph_b, vi_name_a, vi_name_b)
    report.wiring = _diff_wiring(graph_a, graph_b, vi_name_a, vi_name_b)
    report.structures = _diff_structures(graph_a, graph_b, vi_name_a, vi_name_b)
    return report


# ── Comparison helpers ────────────────────────────────────────────────


def _diff_signature(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[SignatureChange]:
    changes: list[SignatureChange] = []
    for direction in ("input", "output"):
        if direction == "input":
            terms_a = ga.get_inputs(va)
            terms_b = gb.get_inputs(vb)
        else:
            terms_a = ga.get_outputs(va)
            terms_b = gb.get_outputs(vb)

        map_a = _terminal_map(terms_a)
        map_b = _terminal_map(terms_b)

        for name in sorted(set(map_a) | set(map_b)):
            if name not in map_a:
                changes.append(SignatureChange(
                    "added", direction, name,
                    new_type=map_b[name].python_type(),
                ))
            elif name not in map_b:
                changes.append(SignatureChange(
                    "removed", direction, name,
                    old_type=map_a[name].python_type(),
                ))
            else:
                type_a = map_a[name].python_type()
                type_b = map_b[name].python_type()
                if type_a != type_b:
                    changes.append(SignatureChange(
                        "type_changed", direction, name,
                        old_type=type_a, new_type=type_b,
                    ))
    return changes


def _diff_operations(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[OperationChange]:
    ops_a = ga.get_operations(va)
    ops_b = gb.get_operations(vb)

    counts_a = _op_counts(ops_a)
    counts_b = _op_counts(ops_b)

    changes: list[OperationChange] = []
    all_keys = sorted(set(counts_a) | set(counts_b))
    for key in all_keys:
        name, node_type = key
        ca = counts_a.get(key, 0)
        cb = counts_b.get(key, 0)
        display = name or f"(unnamed {node_type})"
        for _ in range(max(0, cb - ca)):
            changes.append(OperationChange("added", display, node_type))
        for _ in range(max(0, ca - cb)):
            changes.append(OperationChange("removed", display, node_type))
    return changes


def _diff_constants(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[ConstantChange]:
    consts_a = ga.get_constants(va)
    consts_b = gb.get_constants(vb)

    changes: list[ConstantChange] = []

    # Named constants — match by name.
    named_a = {c.name: c for c in consts_a if c.name}
    named_b = {c.name: c for c in consts_b if c.name}
    for name in sorted(set(named_a) | set(named_b)):
        if name not in named_a:
            changes.append(ConstantChange(
                "added", name, new_value=repr(named_b[name].value),
            ))
        elif name not in named_b:
            changes.append(ConstantChange(
                "removed", name, old_value=repr(named_a[name].value),
            ))
        else:
            va_val = repr(named_a[name].value)
            vb_val = repr(named_b[name].value)
            if va_val != vb_val:
                changes.append(ConstantChange(
                    "value_changed", name,
                    old_value=va_val, new_value=vb_val,
                ))

    # Unnamed constants — match by (value, type) multiset.
    unnamed_a = [c for c in consts_a if not c.name]
    unnamed_b = [c for c in consts_b if not c.name]
    keys_a = Counter(_const_key(c) for c in unnamed_a)
    keys_b = Counter(_const_key(c) for c in unnamed_b)
    for key in sorted(set(keys_a) | set(keys_b)):
        diff = keys_b.get(key, 0) - keys_a.get(key, 0)
        val_repr, type_str = key
        label = f"(unnamed {type_str})"
        for _ in range(max(0, diff)):
            changes.append(ConstantChange("added", label, new_value=val_repr))
        for _ in range(max(0, -diff)):
            changes.append(ConstantChange("removed", label, old_value=val_repr))

    return changes


def _diff_wiring(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[WiringChange]:
    wires_a = ga.get_wires(va)
    wires_b = gb.get_wires(vb)

    keys_a = Counter(_wire_key(w) for w in wires_a)
    keys_b = Counter(_wire_key(w) for w in wires_b)

    changes: list[WiringChange] = []
    for key in sorted(set(keys_a) | set(keys_b)):
        diff = keys_b.get(key, 0) - keys_a.get(key, 0)
        src, dst = key
        desc = f"{src} -> {dst}"
        for _ in range(max(0, diff)):
            changes.append(WiringChange("added", desc))
        for _ in range(max(0, -diff)):
            changes.append(WiringChange("removed", desc))
    return changes


def _diff_structures(
    ga: InMemoryVIGraph, gb: InMemoryVIGraph,
    va: str, vb: str,
) -> list[StructureChange]:
    structs_a = _collect_structures(ga.get_operations(va))
    structs_b = _collect_structures(gb.get_operations(vb))

    map_a = {(s.name, type(s).__name__): s for s in structs_a}
    map_b = {(s.name, type(s).__name__): s for s in structs_b}

    changes: list[StructureChange] = []
    for key in sorted(set(map_a) | set(map_b)):
        name, kind = key
        label = name or kind
        if key not in map_a:
            changes.append(StructureChange("added", label))
        elif key not in map_b:
            changes.append(StructureChange("removed", label))
        else:
            detail = _compare_structure(map_a[key], map_b[key])
            if detail:
                changes.append(StructureChange("changed", label, detail))
    return changes


# ── Utility functions ─────────────────────────────────────────────────


def _terminal_map(terminals: list[Terminal]) -> dict[str, Terminal]:
    """Map terminals by name, skipping error clusters."""
    return {
        t.name: t for t in terminals
        if t.name and not t.is_error_cluster
    }


def _op_key(op: Operation) -> tuple[str, str | None]:
    return (op.name or "?", op.node_type)


def _op_counts(ops: list[Operation]) -> Counter[tuple[str, str | None]]:
    return Counter(_op_key(op) for op in ops)


def _const_key(c: Constant) -> tuple[str, str]:
    type_str = c.lv_type.to_python() if c.lv_type else "unknown"
    return (repr(c.value), type_str)


def _wire_key(w: Wire) -> tuple[str, str]:
    src = w.source.name or w.source.node_id.split("::")[-1]
    dst = w.dest.name or w.dest.node_id.split("::")[-1]
    return (src, dst)


def _collect_structures(ops: list[Operation]) -> list[Operation]:
    return [
        op for op in ops
        if isinstance(op, CaseOperation | LoopOperation | SequenceOperation)
    ]


def _compare_structure(a: Operation, b: Operation) -> str | None:
    if isinstance(a, CaseOperation) and isinstance(b, CaseOperation):
        if len(a.frames) != len(b.frames):
            return f"{len(a.frames)} frames -> {len(b.frames)} frames"
    if isinstance(a, LoopOperation) and isinstance(b, LoopOperation):
        if a.loop_type != b.loop_type:
            return f"{a.loop_type} -> {b.loop_type}"
    if isinstance(a, SequenceOperation) and isinstance(b, SequenceOperation):
        if len(a.frames) != len(b.frames):
            return f"{len(a.frames)} frames -> {len(b.frames)} frames"
    return None
