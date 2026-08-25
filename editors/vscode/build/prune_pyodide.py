#!/usr/bin/env python3
"""Assemble the MINIMAL Pyodide dist into media/pyodide for the web VS Code target.

The npm `pyodide` package ships only the CORE (loader + wasm + stdlib + lock);
the ~250 package files live on the pinned CDN. The web extension only ever
``loadPackage()``s a handful — micropip, pydantic, Pillow — plus their transitive
deps; everything else (numpy, matplotlib, contourpy, fonttools, …) is dead weight
the browser never imports. This copies the CORE from the local npm package and
downloads ONLY the transitive closure of the requested packages, read from
``pyodide-lock.json`` so it survives a Pyodide version bump with no filename edits
here. The download is a BUILD-time fetch of the immutable pinned version; the
shipped extension serves every byte locally and never touches the CDN at runtime.

Usage: prune_pyodide.py <src (node_modules/pyodide)> <out_dir> <cdn_base> <pkg>...
"""

import json
import shutil
import sys
import urllib.request
from pathlib import Path

# Loader + runtime the browser must fetch first (before any package): pyodide.js
# (classic loader used via <script src>) imports pyodide.asm.mjs (wasm JS glue),
# which instantiates pyodide.asm.wasm; python_stdlib.zip + the lock complete it.
# `.map`/`.d.ts`/console HTML are debug-only and excluded to keep the VSIX small.
REQUIRED_CORE = [
    "pyodide.js",
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]
OPTIONAL_CORE: list[str] = []


def _canon(name: str) -> str:
    return name.lower().replace("_", "-")


def main() -> int:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    cdn_base = sys.argv[3].rstrip("/") + "/"
    roots = sys.argv[4:]
    lock = json.loads((src / "pyodide-lock.json").read_text())
    pkgs = lock["packages"]
    index = {_canon(n): n for n in pkgs}

    # Breadth-first transitive closure over each package's `depends`.
    seen: set[str] = set()
    queue = [_canon(r) for r in roots]
    while queue:
        k = queue.pop()
        if k in seen:
            continue
        if k not in index:
            print(f"  WARN: '{k}' not found in pyodide-lock.json", file=sys.stderr)
            continue
        seen.add(k)
        queue.extend(_canon(d) for d in pkgs[index[k]].get("depends", []))

    out.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_CORE:
        f = src / name
        if not f.exists():
            print(f"  ERROR: required Pyodide core missing: {name}", file=sys.stderr)
            return 1
        shutil.copy2(f, out / name)
    for name in OPTIONAL_CORE:
        f = src / name
        if f.exists():
            shutil.copy2(f, out / name)

    files = sorted(pkgs[index[k]]["file_name"] for k in seen)
    for fn in files:
        local = src / fn
        if local.exists():
            shutil.copy2(local, out / fn)
        else:
            urllib.request.urlretrieve(cdn_base + fn, out / fn)

    print(f"  pyodide core + {len(files)} package files: {', '.join(sorted(seen))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
