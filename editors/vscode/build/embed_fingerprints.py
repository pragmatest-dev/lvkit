#!/usr/bin/env python3
"""Capture lvkit's cache fingerprints from the LIVE sources for a frozen build.

A PyInstaller binary ships compiled code, not the ``.py`` sources that
``cache_paths.source_fingerprint`` / ``extraction_fingerprint`` hash — so at
runtime the binary cannot compute them (source_fingerprint would see only the
bundled data files, blind to code; extraction_fingerprint's fixed source list
would not exist at all). This runs at BUILD time, where the sources ARE present,
and writes both fingerprints to a JSON file that build-binary.sh bundles next to
the package. At runtime the fingerprint functions return these embedded values,
so the binary's caches stay correct AND code-aware — the same keys a source or
wheel install computes — instead of degrading or crashing.

Usage: embed_fingerprints.py <out.json>
"""

import json
import sys
from pathlib import Path

from lvkit import cache_paths


def main() -> int:
    out = Path(sys.argv[1])
    # Computed from the live source tree (no _build_fingerprints.json exists yet,
    # so these equal what a source/wheel install computes for this same code).
    fingerprints = {
        "source": cache_paths.source_fingerprint(),
        "extraction": cache_paths.extraction_fingerprint(),
    }
    out.write_text(json.dumps(fingerprints, indent=2) + "\n", encoding="utf-8")
    print(f"embedded fingerprints -> {out}")
    for k, v in fingerprints.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
