#!/usr/bin/env python3
"""Extract block-diagram XML for every VI in a corpus into ``.lvkit/cache/``.

`grep <primResID> *_BDHb.xml` only sees VIs that have been extracted; a `.vi`
with no cached XML is invisible. So before trusting any "all locations of this
primitive" or "unknown-primitive" count, the corpus must be fully extracted.

MEMORY SAFETY — read this before changing the loop. ``extract_vi_xml`` runs
pylabview **in-process** (there is no per-VI subprocess anymore). Extracting
thousands of VIs in a single interpreter accumulates memory and will OOM the
machine (this killed WSL at ~100 VIs). So the parent process only *enumerates*
and dispatches: each batch of VIs is extracted in a **fresh worker subprocess**
that exits and returns all its memory. Keep it that way.

Extraction is resumable: ``extract_vi_xml`` self-skips VIs whose cached XML is
still fresh (mtime/size fast-path, else content hash), so re-running after an
interruption picks up where it left off.

Usage:
    uv run python scripts/extract_corpus.py                 # samples/, resume
    uv run python scripts/extract_corpus.py --root DIR
    uv run python scripts/extract_corpus.py --batch 25      # smaller batches
    uv run python scripts/extract_corpus.py --force         # re-extract all
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WORKER_FLAG = "--_worker"


def _worker(paths: list[str], force: bool) -> int:
    """Extract the given VIs in THIS process, then exit (freeing all memory)."""
    from lvkit.extractor import extract_vi_xml

    for raw in paths:
        try:
            extract_vi_xml(raw, force=force)
            print(f"OK {raw}", flush=True)
        except Exception as e:  # noqa: BLE001 — best-effort; log and continue
            print(f"FAIL {raw}: {str(e)[:120]}", flush=True)
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if WORKER_FLAG in argv:
        argv.remove(WORKER_FLAG)
        force = "--force" in argv
        if force:
            argv.remove("--force")
        sys.exit(_worker(argv, force))

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".lvkit/cache/samples")
    ap.add_argument("--batch", type=int, default=50,
                    help="VIs per worker subprocess (default 50)")
    ap.add_argument("--force", action="store_true",
                    help="re-extract even if the cached XML is fresh")
    args = ap.parse_args()

    vis = sorted(Path(args.root).rglob("*.vi"))
    print(f"{len(vis)} VIs under {args.root} -> .lvkit/cache/extracted/ "
          f"(batch={args.batch}, {'force' if args.force else 'resume'})",
          flush=True)

    me = str(Path(__file__).resolve())
    ok = fail = 0
    for start in range(0, len(vis), args.batch):
        batch = vis[start : start + args.batch]
        cmd = [sys.executable, me, WORKER_FLAG]
        if args.force:
            cmd.append("--force")
        cmd += [str(p) for p in batch]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            if line.startswith("OK "):
                ok += 1
            elif line.startswith("FAIL "):
                fail += 1
                print(f"  {line}", flush=True)
        if proc.returncode != 0 and not proc.stdout:
            # worker died outright (e.g. OOM-killed) — report and keep going
            fail += len(batch)
            print(f"  WORKER DIED on batch at {start} "
                  f"(rc={proc.returncode}) {proc.stderr[-200:]}", flush=True)
        print(f"  ...{min(start + args.batch, len(vis))}/{len(vis)} "
              f"(ok={ok} fail={fail})", flush=True)

    print(f"DONE: extracted {ok}, failed {fail}, of {len(vis)}", flush=True)


if __name__ == "__main__":
    main()
