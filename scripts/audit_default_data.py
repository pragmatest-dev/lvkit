#!/usr/bin/env python3
"""Audit that every stored front-panel default and diagram constant is read to
exactly its stored byte count.

For each distinct extracted VI/control in the per-user extract cache
(``~/.lvkit/cache``, see ``lvkit.extractor.global_cache_root``) this decodes every
front-panel ``DefaultData`` and every diagram constant with ``_decode_element``
and compares the bytes consumed with the stored size. Results go to one JSON-lines
file per batch under ``--out``; ``--summary`` groups every mismatch by the type at
which reading first went wrong.

MEMORY SAFETY -- same rule as ``extract_corpus.py``. Parsing thousands of VIs in
one interpreter accumulates memory and can OOM the machine, so the parent only
enumerates and dispatches: each bounded batch runs in a fresh worker subprocess
that parses ONE VI at a time, writes its records and exits.

Usage:
    uv run python scripts/audit_default_data.py --out .tmp/audit            # run
    uv run python scripts/audit_default_data.py --out .tmp/audit --summary  # report
    uv run python scripts/audit_default_data.py --out .tmp/audit --limit 200
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

WORKER_FLAG = "--_worker"
LIST_NAME = "vis.txt"


def enumerate_extracts(cache_root: Path) -> list[str]:
    """One ``main|bd|fp|source`` row per distinct VI/control content in the
    extract cache (deduplicated by the VI's own sha256, keeping the most recent
    extraction), in sorted order."""
    newest: dict[str, tuple[float, str]] = {}
    for meta_path in sorted(cache_root.rglob("vi.meta.json")):
        folder = meta_path.parent
        main, bd, fp = folder / "vi.xml", folder / "vi_BDHb.xml", folder / "vi_FPHb.xml"
        if not (main.exists() and fp.exists()):
            continue
        meta = json.loads(meta_path.read_text())
        digest = meta.get("sha256", "")
        if not digest:
            continue
        bd_part = str(bd) if bd.exists() else ""
        row = f"{main}|{bd_part}|{fp}|{meta.get('source', '')}"
        stamp = float(meta.get("extracted_at", 0))
        if digest not in newest or stamp > newest[digest][0]:
            newest[digest] = (stamp, row)
    return sorted(row for _stamp, row in newest.values())


def _worker(out_path: str, rows: list[str]) -> int:
    """Audit the given rows in THIS process, one VI at a time, then exit."""
    import logging
    import resource
    import warnings

    # A decoder fault that tries to allocate without bound must fail this one
    # worker with MemoryError, not take the machine down.
    limit = 3 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    from lvkit.parser.audit import audit_vi

    with open(out_path, "w") as out:
        for row in rows:
            main, bd, fp, source = row.split("|", 3)
            try:
                records = audit_vi(main, bd or None, fp)
            except (Exception, MemoryError) as exc:  # noqa: BLE001 -- log and go on
                out.write(
                    json.dumps({"source": source, "error": str(exc)[:200]}) + "\n"
                )
                continue
            for record in records:
                record["source"] = source
                out.write(json.dumps(record) + "\n")
            out.flush()
    return 0


def _summarize(out_dir: Path) -> None:
    records: list[dict] = []
    errors = 0
    for path in sorted(out_dir.glob("batch_*.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if "error" in record:
                errors += 1
            else:
                records.append(record)
    exact = [r for r in records if r["exact"]]
    bad = [r for r in records if r not in exact]
    print(
        f"values {len(records)} in {len({r['source'] for r in records})} files; "
        f"exact {len(exact)}; mismatched {len(bad)}; files that failed to parse "
        f"{errors}"
    )
    print(Counter(r["kind"] for r in records))
    partial = [r for r in records if r["exact"] and r["undecoded"]]
    parts: Counter[str] = Counter(p for r in partial for p in set(r["undecoded"]))
    print(f"exact values with parts read but not decoded: {len(partial)} {dict(parts)}")
    groups: Counter[str] = Counter()
    example: dict[str, dict] = {}
    for r in bad:
        groups[r["cause"]] += 1
        example.setdefault(r["cause"], r)
    for cause, count in groups.most_common():
        r = example[cause]
        print(f"{count:6d}  {cause}")
        print(
            f"          e.g. {r['source'][-70:]} {r['kind']} size={r['size']} "
            f"consumed={r['consumed']} type={r['type'][:60]} tail={r['tail']}"
        )


def main() -> None:
    argv = sys.argv[1:]
    if WORKER_FLAG in argv:
        argv.remove(WORKER_FLAG)
        sys.exit(_worker(argv[0], argv[1:]))

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".tmp/audit")
    ap.add_argument("--batch", type=int, default=25, help="VIs per worker (25)")
    ap.add_argument("--limit", type=int, default=0, help="audit only the first N")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.summary:
        _summarize(out_dir)
        return

    from lvkit.cache_paths import global_cache_root

    rows = enumerate_extracts(global_cache_root())
    if args.limit:
        rows = rows[: args.limit]
    (out_dir / LIST_NAME).write_text("\n".join(rows))
    for old in out_dir.glob("batch_*.jsonl"):
        old.unlink()
    print(f"{len(rows)} distinct VIs, batch={args.batch}", flush=True)
    me = str(Path(__file__).resolve())
    for start in range(0, len(rows), args.batch):
        batch = rows[start : start + args.batch]
        out_path = out_dir / f"batch_{start:06d}.jsonl"
        proc = subprocess.run(
            [sys.executable, me, WORKER_FLAG, str(out_path), *batch],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(f"  WORKER DIED at {start} rc={proc.returncode} {proc.stderr[-200:]}")
        print(f"  ...{min(start + args.batch, len(rows))}/{len(rows)}", flush=True)
    _summarize(out_dir)


if __name__ == "__main__":
    main()
