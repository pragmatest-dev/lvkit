#!/usr/bin/env python3
"""Layer-1 primitive audit — declared vs OBSERVED terminals, corpus-wide.

For every entry in ``primitives.json``, compare the terminals the entry DECLARES
against the terminals actually observed at EVERY call-site in the fully-extracted
corpus. This mechanically catches the class of error that comes from resolving a
primitive by shape (or from too few call-sites):

  DIRECTION      an index declared ``in`` but observed as ``out`` (or vice versa)
  UNDECLARED     an index wired in real VIs that the entry never declares
  PHANTOM        an index the entry declares that is never observed anywhere
  TYPE           declared type family contradicts every observed type family
                 (e.g. declared String, always observed Array — the 1163 bug)

Run ``scripts/extract_corpus.py`` first: this reads the extraction cache, and an
un-extracted VI is invisible (a partially-extracted corpus gives false counts).

MEMORY SAFETY: parsing thousands of VIs in one interpreter OOMs the host, so the
parent only enumerates and aggregates — each batch is parsed in a fresh worker
subprocess that exits and returns its memory (same pattern as extract_corpus.py).

Usage:
    uv run python scripts/audit_primitive_consistency.py
    uv run python scripts/audit_primitive_consistency.py --batch 25 --only 1163
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIMS = REPO / "src" / "lvkit" / "data" / "primitives.json"
CACHE = REPO / ".lvkit" / "cache" / "extracted"
WORKER_FLAG = "--_worker"


def _family(t: str | None) -> str | None:
    """Collapse a concrete type name to a comparable family (None = wildcard)."""
    if not t:
        return None
    tl = t.lower()
    if tl in ("polymorphic", "any"):
        return None
    if tl.startswith("num"):
        return "numeric"
    if tl in ("array", "subarray"):
        return "array"
    if tl.startswith("unit") or tl == "enum":
        return "enum"
    for fam in ("string", "boolean", "cluster", "refnum", "path"):
        if tl == fam:
            return fam
    return tl


def _worker(paths: list[str]) -> int:
    from lvkit.parser.node_types import PrimitiveNode
    from lvkit.parser.vi import parse_vi

    for bd in paths:
        main = bd.replace("_BDHb.xml", ".xml")
        try:
            diagram = parse_vi(
                bd_xml=bd, main_xml=main if Path(main).exists() else None
            ).block_diagram
        except Exception:  # noqa: BLE001 — unparseable VI: skip, not an audit finding
            continue
        by_parent: dict = collections.defaultdict(list)
        for ti in diagram.terminal_info.values():
            by_parent[ti.parent_uid].append(ti)
        for node in diagram.nodes:
            pid = getattr(node, "prim_res_id", None)
            if not isinstance(node, PrimitiveNode) or not pid:
                continue
            terms = [
                [
                    ti.index,
                    "out" if ti.is_output else "in",
                    ti.parsed_type.type_name if ti.parsed_type else None,
                ]
                for ti in by_parent.get(node.uid, [])
            ]
            print(
                json.dumps({"pid": pid, "vi": Path(bd).name, "terms": terms}),
                flush=True,
            )
        del diagram
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if WORKER_FLAG in argv:
        argv.remove(WORKER_FLAG)
        sys.exit(_worker(argv))

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--only", type=int, default=None, help="audit one primResID")
    args = ap.parse_args()

    known = json.loads(PRIMS.read_text())["primitives"]
    bds = sorted(str(p) for p in CACHE.rglob("*_BDHb.xml"))
    print(f"auditing {len(known)} entries against {len(bds)} extracted VIs", flush=True)

    # observed[pid][index] = {"dirs": set, "types": set, "vis": set}
    observed: dict[int, dict[int, dict]] = collections.defaultdict(
        lambda: collections.defaultdict(
            lambda: {"dirs": set(), "types": set(), "vis": set()}
        )
    )
    me = str(Path(__file__).resolve())
    for start in range(0, len(bds), args.batch):
        batch = bds[start : start + args.batch]
        proc = subprocess.run(
            [sys.executable, me, WORKER_FLAG, *batch], capture_output=True, text=True
        )
        for line in proc.stdout.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            slot = observed[rec["pid"]]
            for idx, direction, tname in rec["terms"]:
                slot[idx]["dirs"].add(direction)
                slot[idx]["types"].add(tname)
                slot[idx]["vis"].add(rec["vi"])
        if (start // args.batch) % 10 == 0:
            print(f"  ...{min(start + args.batch, len(bds))}/{len(bds)}", flush=True)

    findings: list[tuple[str, int, str, str]] = []
    for pid_s, entry in sorted(known.items(), key=lambda kv: int(kv[0])):
        pid = int(pid_s)
        if args.only and pid != args.only:
            continue
        obs = observed.get(pid)
        if not obs:
            continue  # not present in this corpus — nothing to check
        name = entry.get("name", "?")
        decl = {t["index"]: t for t in entry.get("terminals", [])}
        for idx, t in decl.items():
            o = obs.get(idx)
            if o is None:
                findings.append(
                    (
                        "PHANTOM",
                        pid,
                        name,
                        f"declares idx{idx} ({t.get('name')}) — never observed",
                    )
                )  # noqa: E501
                continue
            if o["dirs"] and t.get("direction") not in o["dirs"]:
                findings.append(
                    (
                        "DIRECTION",
                        pid,
                        name,
                        f"idx{idx} declared {t.get('direction')} but observed "
                        f"{'/'.join(sorted(o['dirs']))}",
                    )
                )
            dfam = _family(t.get("type"))
            ofams = {_family(x) for x in o["types"]}
            if dfam and ofams and dfam not in ofams and None not in ofams:
                findings.append(
                    (
                        "TYPE",
                        pid,
                        name,
                        f"idx{idx} declared {t.get('type')} ({dfam}) but observed "  # noqa: E501
                        f"{'/'.join(sorted(str(f) for f in ofams))}",
                    )
                )
        for idx in sorted(set(obs) - set(decl)):
            n_vis = len(obs[idx]["vis"])
            findings.append(
                (
                    "UNDECLARED",
                    pid,
                    name,
                    f"idx{idx} wired in {n_vis} VI(s) but not declared",
                )
            )

    order = {"DIRECTION": 0, "TYPE": 1, "UNDECLARED": 2, "PHANTOM": 3}
    findings.sort(key=lambda f: (order.get(f[0], 9), -f[1]))
    counts = collections.Counter(f[0] for f in findings)
    print(
        f"\n=== {len(findings)} findings: "
        + "  ".join(f"{k}={v}" for k, v in counts.most_common())
        + " ===\n"
    )
    for kind, pid, name, detail in findings:
        print(f"{kind:11s} {pid:5d} {name[:34]:36s} {detail}")


if __name__ == "__main__":
    main()
