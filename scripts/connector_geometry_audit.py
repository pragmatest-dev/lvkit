#!/usr/bin/env python3
"""Corpus-driven connector-geometry terminal auditor — CLI (Phase 0/0.5/1).

For every ``primitives.json`` entry with corpus instances, pools up to
``--cap`` real corpus instances (grep-then-parse, never the whole corpus —
see ``lvkit.tools.connector_geometry_profile``) into a per-primResID
``PrimProfile``, then diffs the declared ``index -> {name, type, direction}``
mapping against what the corpus's own connector-pane geometry shows.

This catches the bug class fixed by hand in commit 2f71ff2 ("Clear Errors":
an I32 INPUT registered at an index geometry shows is a Boolean OUTPUT) —
corpus-wide, deterministically, no CV/image comparison involved.

LIMITATION (read before trusting a clean report): this CANNOT catch a
same-type swap — two terminals of the SAME type family trading names
(e.g. Insert Menu Items' ``item_names`` <-> ``item_tags``, both Array).
That class needs the doc-image half of the auditor, a later phase not
built here.

Usage::

    uv run python scripts/connector_geometry_audit.py
    uv run python scripts/connector_geometry_audit.py --cap 8 -o outputs/x.md
    uv run python scripts/connector_geometry_audit.py --only 9003
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from lvkit._data import data_dir  # noqa: E402
from lvkit.extractor import global_cache_root  # noqa: E402
from lvkit.tools.connector_geometry_profile import (  # noqa: E402
    FLAG_KINDS,
    Finding,
    PrimEntry,
    PrimProfile,
    audit_primitive,
    build_primresid_index,
    gather_profile,
    load_primitive_entries,
    rank_findings,
)


def _render_report(
    findings: list[Finding],
    profiles: dict[int, PrimProfile],
    entries: dict[int, PrimEntry],
    entries_with_corpus: int,
    entries_without_corpus: int,
) -> str:
    ranked = rank_findings(findings)
    flags = [f for f in ranked if f.kind in FLAG_KINDS]
    notes = [f for f in ranked if f.kind not in FLAG_KINDS]

    by_kind: dict[str, int] = {}
    for f in ranked:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    L: list[str] = []
    L.append("# Connector-Geometry Terminal Audit\n")
    L.append(
        "Corpus-driven, deterministic (no CV/image comparison). Diffs "
        "`primitives.json`'s declared `index -> {name, type, direction}` "
        "against real connector-pane geometry pooled from up to a capped "
        "number of corpus instances per primResID. See "
        "`src/lvkit/tools/connector_geometry_profile.py` for the full rule "
        "set.\n"
    )
    L.append(
        "**LIMITATION**: this audit CANNOT catch a same-type swap — two "
        "terminals of the SAME type family trading names/positions (e.g. "
        "Insert Menu Items' `item_names` <-> `item_tags`, both Array). A "
        "clean report for such a pair proves nothing; that class needs the "
        "doc-image half of the auditor (a later phase, not built here).\n"
    )

    L.append("## Summary\n")
    L.append(
        f"- primitives.json entries with >=1 corpus instance: "
        f"**{entries_with_corpus}**"
    )
    L.append(
        f"- primitives.json entries with NO corpus instance (skipped): "
        f"**{entries_without_corpus}**"
    )
    L.append(
        f"- total findings: **{len(ranked)}** (flags: **{len(flags)}**, "
        f"informational notes: **{len(notes)}**)"
    )
    for kind in (
        "DIRECTION_MISMATCH", "TYPE_MISMATCH", "DISAGREEMENT",
        "MISSING_FROM_ENTRY", "UNOBSERVED",
    ):
        L.append(f"  - {kind}: {by_kind.get(kind, 0)}")
    L.append("")

    L.append(
        "## Flagged findings (ranked: hard mismatches first, "
        "then by instance_count)\n"
    )
    if not flags:
        L.append("*(none)*\n")
    else:
        L.append(
            "| kind | primResID | name | index | JSON says | corpus shows | "
            "instances | confidence | detail |"
        )
        L.append("|---|---|---|---|---|---|---|---|---|")
        for f in flags:
            L.append(
                f"| {f.kind} | {f.prim_res_id} | {f.name} | {f.index} "
                f"| {f.json_says} | {f.corpus_shows} | {f.instance_count} "
                f"| {f.confidence} | {f.detail} |"
            )
    L.append("")

    L.append("## Appendix: unobserved / low-coverage / missing-from-entry\n")
    L.append(
        "| kind | primResID | name | index | JSON says | corpus shows | "
        "instances | confidence | detail |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|")
    for f in notes:
        L.append(
            f"| {f.kind} | {f.prim_res_id} | {f.name} | {f.index} "
            f"| {f.json_says} | {f.corpus_shows} | {f.instance_count} "
            f"| {f.confidence} | {f.detail} |"
        )
    L.append("")

    L.append("## Per-primitive corpus coverage\n")
    L.append(
        "| primResID | name | instances found (grep) | files parsed "
        "| node instances gathered |"
    )
    L.append("|---|---|---|---|---|")
    for pid in sorted(profiles):
        p = profiles[pid]
        name = entries[pid].name if pid in entries else "?"
        L.append(
            f"| {pid} | {name} | {p.instances_found} | {p.files_parsed_ok} "
            f"| {p.instance_count} |"
        )

    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--cap", type=int, default=5,
        help="max corpus FILES parsed per primResID (default: 5)",
    )
    ap.add_argument(
        "--only", type=int, default=None,
        help="audit a single primResID (for iterating on the tool itself)",
    )
    ap.add_argument(
        "-o", "--output", default=str(REPO / "outputs" / "connector_geometry_audit.md"),
        help="markdown report path (default: outputs/connector_geometry_audit.md)",
    )
    args = ap.parse_args()

    entries = load_primitive_entries(data_dir() / "primitives.json")
    target_ids = {args.only} if args.only is not None else set(entries)
    print(
        f"Loaded {len(entries)} primitives.json entries; "
        f"auditing {len(target_ids)}.",
        file=sys.stderr,
    )

    cache_root = global_cache_root() / "extract"
    print(
        f"Grepping cache once for <primResID> markers under {cache_root} ...",
        file=sys.stderr,
    )
    prim_index = build_primresid_index(cache_root, target_ids)

    profiles: dict[int, PrimProfile] = {}
    all_findings: list[Finding] = []
    entries_with_corpus = 0
    entries_without_corpus = 0

    for pid in sorted(target_ids):
        files = prim_index.get(pid, [])
        entry = entries[pid]
        if not files:
            entries_without_corpus += 1
            continue
        entries_with_corpus += 1
        profile = gather_profile(pid, files, cap=args.cap)
        profiles[pid] = profile
        print(
            f"  primResID {pid} ({entry.name}): {profile.instances_found} file(s) "
            f"found, {profile.files_parsed_ok}/{len(profile.files_considered)} "
            f"parsed OK, {profile.instance_count} node instance(s) gathered",
            file=sys.stderr,
        )
        all_findings.extend(audit_primitive(entry, profile))

    report = _render_report(
        all_findings, profiles, entries, entries_with_corpus, entries_without_corpus
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    ranked = rank_findings(all_findings)
    flags = [f for f in ranked if f.kind in FLAG_KINDS]
    print(f"\nReport written to {out}")
    print(
        f"entries with corpus instances: {entries_with_corpus}; "
        f"without: {entries_without_corpus}"
    )
    print(f"total findings: {len(ranked)} (flags: {len(flags)})")
    print("\nTop findings:")
    for f in ranked[:15]:
        print(
            f"  {f.kind:20s} prim {f.prim_res_id:5d} {f.name[:30]:32s} idx{f.index} "
            f"json={f.json_says!r} corpus={f.corpus_shows!r} "
            f"n={f.instance_count} conf={f.confidence}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
