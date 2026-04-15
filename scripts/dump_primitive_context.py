#!/usr/bin/env python3
"""Dump PrimitiveResolutionNeeded-shaped context for missing primitives.

For each primResID seen in samples but not captured in primitives.json,
emit a markdown section matching the PrimitiveResolutionNeeded message
format (terminal indices, directions, types, usage count, sample VIs).

The output feeds the `lvkit-resolve-primitive` skill — one section per
primitive, sorted by usage count descending.

Usage:
    uv run python scripts/dump_primitive_context.py [--limit 25]
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lvkit.parser import parse_vi
from lvkit.parser.node_types import PrimitiveNode
from lvkit.primitive_resolver import PrimitiveResolver

REPO_ROOT = Path(__file__).parent.parent
SAMPLES = REPO_ROOT / "samples"
REPORT_PATH = REPO_ROOT / ".tmp" / "missing-primitives-context.md"

# primResID range → hint (copied from SKILL.md Step 4)
RANGE_HINTS: list[tuple[int, int, str]] = [
    (1044, 1064, "Array operations"),
    (1061, 1081, "Numeric/arithmetic"),
    (1083, 1128, "Path/comparison/boolean"),
    (1140, 1170, "Type conversion, variant, data manipulation"),
    (1300, 1340, "Timing, constants, clusters"),
    (1419, 1435, "Path operations"),
    (1500, 1540, "String operations"),
    (1600, 1610, "Flatten/unflatten"),
    (1809, 1911, "Array index/sort/delete"),
    (1999, 1999, "Path constant"),
    (2073, 2076, "Error handling"),
    (2401, 2401, "Merge Errors"),
    (8003, 8083, "File I/O"),
    (8100, 8101, "VI info"),
    (8201, 8205, "Variant operations"),
    (9000, 9114, "VI Server, references, scripting"),
]


def range_hint(prim_id: int) -> str:
    for lo, hi, hint in RANGE_HINTS:
        if lo <= prim_id <= hi:
            return f"{lo}-{hi}: {hint}"
    return "(no range hint)"


@dataclass
class TerminalObservation:
    index: int | None
    direction: str
    type_name: str
    name: str | None


@dataclass
class Invocation:
    vi_label: str
    bd_xml: Path
    terminals: list[TerminalObservation]


def find_bd_xml_pairs(samples_root: Path) -> list[tuple[Path, Path | None]]:
    seen: set[Path] = set()
    pairs: list[tuple[Path, Path | None]] = []
    for bd_xml in samples_root.rglob("*_BDHb.xml"):
        resolved = bd_xml.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        main_xml = bd_xml.parent / bd_xml.name.replace("_BDHb.xml", ".xml")
        if not main_xml.exists():
            main_xml = None
        pairs.append((bd_xml, main_xml))
    return pairs


def collect_invocations(
    pairs: list[tuple[Path, Path | None]],
    missing_ids: set[int],
    max_per_id: int,
) -> dict[int, tuple[int, list[Invocation]]]:
    """Scan all pairs, collect up to max_per_id invocations per primResID.

    Returns: prim_id -> (total_count, invocations)
    """
    counts: dict[int, int] = defaultdict(int)
    invocations: dict[int, list[Invocation]] = defaultdict(list)

    for bd_xml, main_xml in pairs:
        try:
            parsed = parse_vi(bd_xml=bd_xml, main_xml=main_xml)
        except Exception:
            continue

        vi_label = bd_xml.name.replace("_BDHb.xml", "")
        for node in parsed.block_diagram.nodes:
            if not isinstance(node, PrimitiveNode):
                continue
            if node.prim_res_id not in missing_ids:
                continue
            counts[node.prim_res_id] += 1

            # Only record detailed invocations up to max_per_id
            if len(invocations[node.prim_res_id]) >= max_per_id:
                continue

            terms: list[TerminalObservation] = []
            for _uid, t_info in parsed.block_diagram.terminal_info.items():
                if t_info.parent_uid != node.uid:
                    continue
                type_name = (
                    t_info.parsed_type.type_name
                    if t_info.parsed_type else "?"
                )
                terms.append(TerminalObservation(
                    index=t_info.index,
                    direction="output" if t_info.is_output else "input",
                    type_name=type_name,
                    name=t_info.name,
                ))
            terms.sort(key=lambda t: (t.index is None, t.index or 0))
            invocations[node.prim_res_id].append(Invocation(
                vi_label=vi_label,
                bd_xml=bd_xml,
                terminals=terms,
            ))

    return {pid: (counts[pid], invs) for pid, invs in invocations.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--limit", type=int, default=25,
        help="Number of missing primitives to include (top N by usage)",
    )
    ap.add_argument(
        "--invocations", type=int, default=3,
        help="Max sample invocations per primResID",
    )
    args = ap.parse_args()

    resolver = PrimitiveResolver()

    # First pass: count all primResIDs in samples to build the missing set
    # sorted by count. Reuse audit logic: classify missing vs captured via
    # resolver.get_by_id + python_code/terminals check.
    pairs = find_bd_xml_pairs(SAMPLES)
    print(f"Scanning {len(pairs)} VIs...")

    all_counts: dict[int, int] = defaultdict(int)
    for bd_xml, main_xml in pairs:
        try:
            parsed = parse_vi(bd_xml=bd_xml, main_xml=main_xml)
        except Exception:
            continue
        for node in parsed.block_diagram.nodes:
            if isinstance(node, PrimitiveNode) and node.prim_res_id is not None:
                all_counts[node.prim_res_id] += 1

    # Filter to missing primitives
    missing: list[tuple[int, int]] = []
    for pid, count in all_counts.items():
        entry = resolver.get_by_id(str(pid))
        if entry is None:
            missing.append((pid, count))
            continue
        # Partial: no python_code or any terminal missing index
        if not entry.get("python_code"):
            missing.append((pid, count))
            continue
        terms = entry.get("terminals") or []
        if not terms or any(t.get("index") is None for t in terms):
            missing.append((pid, count))

    missing.sort(key=lambda x: -x[1])
    top = missing[:args.limit]
    print(f"Found {len(missing)} missing/partial primitives, taking top {len(top)}")

    # Second pass: collect invocation details only for the top N
    top_ids = {pid for pid, _ in top}
    details = collect_invocations(pairs, top_ids, args.invocations)

    # Write the report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Missing primitives — resolution context\n")
    lines.append(
        f"Top {len(top)} of {len(missing)} missing/partial primitives "
        f"by usage count across {len(pairs)} sample VIs.\n"
    )
    lines.append(
        "Each section matches the `PrimitiveResolutionNeeded` diagnostic "
        "format. Feed one section at a time to the "
        "`lvkit-resolve-primitive` skill.\n"
    )

    for pid, count in top:
        invs = details.get(pid, (count, []))[1]
        lines.append(f"## primResID {pid} — {count} uses\n")
        lines.append(f"**Range hint**: {range_hint(pid)}\n")

        existing = resolver.get_by_id(str(pid))
        if existing is not None:
            status = "partial"
            reason = []
            if not existing.get("python_code"):
                reason.append("no python_code")
            terms = existing.get("terminals") or []
            if not terms:
                reason.append("no terminals")
            elif any(t.get("index") is None for t in terms):
                reason.append("missing terminal indices")
            lines.append(
                f"**Current entry**: {existing.get('name', '?')} "
                f"({status}: {', '.join(reason)})\n"
            )
        else:
            lines.append("**Current entry**: (none)\n")

        for i, inv in enumerate(invs, 1):
            lines.append(f"### Sample {i}: {inv.vi_label}")
            rel = inv.bd_xml.relative_to(REPO_ROOT)
            lines.append(f"File: `{rel}`")
            lines.append("Wired terminals from graph:")
            for t in inv.terminals:
                parts = [
                    f"index={t.index}",
                    f"direction={t.direction}",
                    f"type={t.type_name}",
                ]
                if t.name:
                    parts.append(f"name={t.name}")
                lines.append(f"- {' '.join(parts)}")
            lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report written to {REPORT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
