#!/usr/bin/env python3
"""Audit primitives and vilib VIs referenced in samples but not in our data.

Walks samples/, parses each VI via XML pairs, cross-references primitives and
vilib dependencies against the JSON data files, and emits a markdown report
to .tmp/audit-unresolved.md sorted by usage count (highest first).

Usage: uv run python scripts/audit_unresolved.py
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from lvkit.parser import parse_vi
from lvkit.parser.node_types import PrimitiveNode
from lvkit.primitive_resolver import PrimitiveResolver
from lvkit.vilib_resolver import VIEntry, VILibResolver

REPO_ROOT = Path(__file__).parent.parent
SAMPLES = REPO_ROOT / "samples"
REPORT_PATH = REPO_ROOT / ".tmp" / "audit-unresolved.md"

# Status buckets
CAPTURED = "captured"
PARTIAL = "partial"
MISSING = "missing"


def find_bd_xml_pairs(samples_root: Path) -> list[tuple[Path, Path | None]]:
    """Find all (bd_xml, main_xml) pairs under samples/.

    Returns a list of (bd_xml, main_xml) tuples. main_xml is optional.
    Dedups by bd_xml absolute path.
    """
    seen: set[Path] = set()
    pairs: list[tuple[Path, Path | None]] = []
    for bd_xml in samples_root.rglob("*_BDHb.xml"):
        resolved = bd_xml.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        # Derive main_xml path: same stem minus _BDHb
        main_xml_name = bd_xml.name.replace("_BDHb.xml", ".xml")
        main_xml = bd_xml.parent / main_xml_name
        if not main_xml.exists():
            main_xml = None
        pairs.append((bd_xml, main_xml))
    return pairs


def classify_primitive(entry: dict | None) -> str:
    """Classify a primitive entry as captured/partial/missing."""
    if entry is None:
        return MISSING
    if entry.get("placeholder"):
        return PARTIAL
    python_code = entry.get("python_code")
    if not python_code:
        return PARTIAL
    terminals = entry.get("terminals") or []
    if not terminals:
        return PARTIAL
    for t in terminals:
        if t.get("index") is None:
            return PARTIAL
    return CAPTURED


def classify_vilib(entry: VIEntry | None) -> str:
    """Classify a vilib VI entry as captured/partial/missing."""
    if entry is None:
        return MISSING
    if entry.status == "needs_terminals":
        return PARTIAL
    for t in entry.terminals:
        if t.index is None:
            return PARTIAL
    return CAPTURED


def main() -> None:
    prim_resolver = PrimitiveResolver()
    vilib_resolver = VILibResolver()

    # prim_id -> {"count": int, "name": str, "vis": set[str]}
    prim_usage: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "name": None, "vis": set()}
    )
    # vilib_path -> {"count": int, "name": str, "vis": set[str]}
    vilib_usage: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "name": None, "vis": set()}
    )

    pairs = find_bd_xml_pairs(SAMPLES)
    print(f"Scanning {len(pairs)} VIs from {SAMPLES}...")

    parse_failures: list[tuple[Path, str]] = []
    for bd_xml, main_xml in pairs:
        try:
            parsed = parse_vi(bd_xml=bd_xml, main_xml=main_xml)
        except Exception as e:
            parse_failures.append((bd_xml, f"{type(e).__name__}: {e}"))
            continue

        vi_label = bd_xml.name.replace("_BDHb.xml", "")

        # Primitives pass
        for node in parsed.block_diagram.nodes:
            if not isinstance(node, PrimitiveNode):
                continue
            if node.prim_res_id is None:
                continue
            key = str(node.prim_res_id)
            prim_usage[key]["count"] += 1
            prim_usage[key]["vis"].add(vi_label)
            if node.name and prim_usage[key]["name"] is None:
                prim_usage[key]["name"] = node.name

        # VIlib pass (requires main_xml)
        for ref in parsed.metadata.dependency_refs:
            if not ref.path_tokens or ref.path_tokens[0] != "<vilib>":
                continue
            vilib_path = "/".join(ref.path_tokens[1:])
            vilib_usage[vilib_path]["count"] += 1
            vilib_usage[vilib_path]["vis"].add(vi_label)
            if vilib_usage[vilib_path]["name"] is None:
                vilib_usage[vilib_path]["name"] = ref.name

    # Classify primitives
    prim_buckets: dict[str, list] = {CAPTURED: [], PARTIAL: [], MISSING: []}
    for prim_id, info in prim_usage.items():
        entry = prim_resolver.get_by_id(prim_id)
        status = classify_primitive(entry)
        name = (entry or {}).get("name") or info["name"] or "Unknown"
        prim_buckets[status].append({
            "id": prim_id,
            "name": name,
            "count": info["count"],
            "vis": sorted(info["vis"]),
        })

    # Classify vilib
    vilib_buckets: dict[str, list] = {CAPTURED: [], PARTIAL: [], MISSING: []}
    for path, info in vilib_usage.items():
        entry = vilib_resolver.resolve(path)
        if entry is None and info["name"]:
            entry = vilib_resolver.resolve_by_name(info["name"])
        status = classify_vilib(entry)
        name = (entry.name if entry else info["name"]) or "Unknown"
        vilib_buckets[status].append({
            "path": path,
            "name": name,
            "count": info["count"],
            "vis": sorted(info["vis"]),
            "status": entry.status if entry else None,
        })

    # Sort by count desc
    for bucket in prim_buckets.values():
        bucket.sort(key=lambda x: -x["count"])
    for bucket in vilib_buckets.values():
        bucket.sort(key=lambda x: -x["count"])

    # JSON-level audit: count all entries in vilib JSON (not just sample refs).
    # Each entry appears under multiple keys in _by_name — dedup by object id.
    seen_ids: set[int] = set()
    json_vilib_total = 0
    json_vilib_captured = 0
    json_vilib_needs_terminals = 0
    json_vilib_missing_idx = 0
    for entry in vilib_resolver._by_name.values():
        if id(entry) in seen_ids:
            continue
        seen_ids.add(id(entry))
        json_vilib_total += 1
        if entry.status == "needs_terminals":
            json_vilib_needs_terminals += 1
        elif any(t.index is None for t in entry.terminals):
            json_vilib_missing_idx += 1
        else:
            json_vilib_captured += 1

    json_prim_total = len(prim_resolver.get_all_ids())
    json_prim_captured = sum(
        1 for pid in prim_resolver.get_all_ids()
        if classify_primitive(prim_resolver.get_by_id(pid)) == CAPTURED
    )
    json_prim_partial = json_prim_total - json_prim_captured

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Unresolved Audit — {date.today().isoformat()}\n")
    lines.append(f"Scanned {len(pairs)} VI block diagrams from `samples/`.\n")

    lines.append("## Summary (sample usage)\n")
    lines.append(
        f"- **Primitives**: "
        f"{len(prim_buckets[CAPTURED])} captured / "
        f"{len(prim_buckets[PARTIAL])} partial / "
        f"{len(prim_buckets[MISSING])} missing "
        f"({len(prim_usage)} unique primResIDs seen)"
    )
    lines.append(
        f"- **VIlib**: "
        f"{len(vilib_buckets[CAPTURED])} captured / "
        f"{len(vilib_buckets[PARTIAL])} partial / "
        f"{len(vilib_buckets[MISSING])} missing "
        f"({len(vilib_usage)} unique paths seen)"
    )
    lines.append("")
    lines.append("## JSON data state (all entries, regardless of samples)\n")
    lines.append(
        f"- **Primitives JSON**: {json_prim_total} total — "
        f"{json_prim_captured} captured / {json_prim_partial} partial"
    )
    lines.append(
        f"- **VIlib JSON**: {json_vilib_total} total — "
        f"{json_vilib_captured} captured / "
        f"{json_vilib_needs_terminals} needs_terminals / "
        f"{json_vilib_missing_idx} missing indices"
    )
    if parse_failures:
        lines.append(f"- **Parse failures**: {len(parse_failures)}")
    lines.append("")

    def _sample_vis(vis: list[str], limit: int = 3) -> str:
        if len(vis) <= limit:
            return ", ".join(vis)
        return ", ".join(vis[:limit]) + f" (+{len(vis) - limit} more)"

    # Missing primitives
    lines.append("## Missing Primitives\n")
    lines.append("| primResID | Name | Count | Sample VIs |")
    lines.append("|-----------|------|-------|------------|")
    for p in prim_buckets[MISSING]:
        lines.append(
            f"| {p['id']} | {p['name']} | {p['count']} | {_sample_vis(p['vis'])} |"
        )
    if not prim_buckets[MISSING]:
        lines.append("| _(none)_ |  |  |  |")
    lines.append("")

    # Partial primitives
    lines.append("## Partial Primitives (needs terminals or python_code)\n")
    lines.append("| primResID | Name | Count | Sample VIs |")
    lines.append("|-----------|------|-------|------------|")
    for p in prim_buckets[PARTIAL]:
        lines.append(
            f"| {p['id']} | {p['name']} | {p['count']} | {_sample_vis(p['vis'])} |"
        )
    if not prim_buckets[PARTIAL]:
        lines.append("| _(none)_ |  |  |  |")
    lines.append("")

    # Missing vilib
    lines.append("## Missing VIlib VIs\n")
    lines.append("| Path | Count | Sample VIs |")
    lines.append("|------|-------|------------|")
    for v in vilib_buckets[MISSING]:
        lines.append(
            f"| `{v['path']}` | {v['count']} | {_sample_vis(v['vis'])} |"
        )
    if not vilib_buckets[MISSING]:
        lines.append("| _(none)_ |  |  |")
    lines.append("")

    # Partial vilib
    lines.append("## Partial VIlib VIs (needs terminal indices)\n")
    lines.append("| Path | Status | Count | Sample VIs |")
    lines.append("|------|--------|-------|------------|")
    for v in vilib_buckets[PARTIAL]:
        lines.append(
            f"| `{v['path']}` | {v['status']} | {v['count']} | "
            f"{_sample_vis(v['vis'])} |"
        )
    if not vilib_buckets[PARTIAL]:
        lines.append("| _(none)_ |  |  |  |")
    lines.append("")

    # Parse failures (if any)
    if parse_failures:
        lines.append("## Parse Failures\n")
        for path, err in parse_failures[:30]:
            rel = path.relative_to(REPO_ROOT)
            lines.append(f"- `{rel}` — {err}")
        if len(parse_failures) > 30:
            lines.append(f"- _(+{len(parse_failures) - 30} more)_")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report written to {REPORT_PATH.relative_to(REPO_ROOT)}")
    print(
        f"  Primitives: {len(prim_buckets[CAPTURED])} captured / "
        f"{len(prim_buckets[PARTIAL])} partial / "
        f"{len(prim_buckets[MISSING])} missing"
    )
    print(
        f"  VIlib: {len(vilib_buckets[CAPTURED])} captured / "
        f"{len(vilib_buckets[PARTIAL])} partial / "
        f"{len(vilib_buckets[MISSING])} missing"
    )
    if parse_failures:
        print(f"  Parse failures: {len(parse_failures)}")


if __name__ == "__main__":
    main()
