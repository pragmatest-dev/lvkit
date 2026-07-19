#!/usr/bin/env python3
"""List every primResID used in the local corpus and filter out the ones lvkit
already knows — i.e. the primitive-resolution work list.

Clean-room + memory-flat: greps the already-dumped block-diagram XML
(`*_BDHb.xml`) for `<primResID>N</primResID>`; never parses whole VIs, never
loads subVIs, so it can't OOM the machine (see the no-corpus-parse-sweep rule).

Usage:
    uv run python scripts/list_unknown_primitives.py            # unknowns only
    uv run python scripts/list_unknown_primitives.py --all      # known + unknown
    uv run python scripts/list_unknown_primitives.py --root DIR  # corpus root
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIMS_JSON = REPO / "src" / "lvkit" / "data" / "primitives.json"
PRIM_RE = re.compile(r"<primResID>(\d+)</primResID>")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(REPO / ".lvkit" / "cache" / "extracted"),
                    help="corpus root to scan for *_BDHb.xml "
                         "(default: .lvkit/cache/extracted/ — run extract_corpus.py first)")
    ap.add_argument("--all", action="store_true",
                    help="also list the KNOWN primitives found")
    ap.add_argument("--min", type=int, default=1,
                    help="only show primResIDs with at least this many instances")
    args = ap.parse_args()

    data = json.loads(PRIMS_JSON.read_text())
    known = {pid: entry.get("name", pid)
             for pid, entry in data["primitives"].items()}

    counts: collections.Counter[str] = collections.Counter()
    vis: dict[str, set[str]] = collections.defaultdict(set)
    files = glob.glob(f"{args.root}/**/*_BDHb.xml", recursive=True)
    for f in files:
        try:
            txt = Path(f).read_text(errors="ignore")
        except OSError:
            continue
        vi = Path(f).name.replace("_BDHb.xml", "")
        for pid in PRIM_RE.findall(txt):
            if pid == "0":
                continue
            counts[pid] += 1
            vis[pid].add(vi)
        del txt  # keep memory flat across the corpus

    ranked = counts.most_common()
    unknown = [(p, c) for p, c in ranked if p not in known and c >= args.min]
    known_hit = [(p, c) for p, c in ranked if p in known and c >= args.min]

    print(f"scanned {len(files)} block-diagram XMLs under {args.root}")
    print(f"distinct primResID: {len(counts)}  "
          f"(known {len(known_hit)} / UNKNOWN {len(unknown)})")
    print(f"instances: {sum(counts.values())}  "
          f"(known {sum(c for _, c in known_hit)} / "
          f"unknown {sum(c for _, c in unknown)})")

    print(f"\n=== UNKNOWN primResIDs — resolve these ({len(unknown)}) ===")
    print(f"{'primResID':>9} {'count':>6}  example VIs")
    for pid, c in unknown:
        ex = ", ".join(sorted(vis[pid])[:3])
        print(f"{pid:>9} {c:>6}  {ex}")

    if args.all:
        print(f"\n=== KNOWN primResIDs in corpus ({len(known_hit)}) ===")
        for pid, c in known_hit:
            print(f"{pid:>9} {c:>6}  {known[pid]}")


if __name__ == "__main__":
    main()
