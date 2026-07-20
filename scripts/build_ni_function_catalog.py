#!/usr/bin/env python3
"""Build the local, authoritative catalog of every LabVIEW function/VI NI publishes.

Primitive identification (see the `lvkit-resolve-primitive` skill) must match an
observed connector pane against the REAL, closed set of NI functions — never a name
guessed from memory. This script crawls NI's PUBLIC docs menu tree (clean-room: no
LabVIEW, no license) and caches every leaf page's title + doc slug + palette path.

The output is a LOCAL build artifact (git-ignored) so anyone with this repo can
reproduce it; we ship the script, not NI's content.

Usage:
    uv run python scripts/build_ni_function_catalog.py            # build if missing
    uv run python scripts/build_ni_function_catalog.py --force    # rebuild
    uv run python scripts/build_ni_function_catalog.py --out PATH

Then query it, e.g.:
    grep -i "file position" .cache/ni_function_catalog.json
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import time
import urllib.request
from pathlib import Path

API = "https://docs-be.ni.com/api/bundle/labview-api-ref/page/"
ROOT_MENU = "menus/default/root-mnu.html"
HREF = re.compile(r'href="([^"]+)"[^>]*>([^<]*)<')
DEFAULT_OUT = (Path(__file__).resolve().parents[1]
               / ".lvkit" / "cache" / "ni_function_catalog.json")


def fetch(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def norm(href: str) -> str | None:
    """A menu href is a full URL with ../ segments; return the normalized page path."""
    m = re.search(r"/page/(.+?\.html)", href)
    return posixpath.normpath(m.group(1)) if m else None


def crawl() -> dict:
    queue = [ROOT_MENU]
    visited: set[str] = set()
    functions: dict[str, dict] = {}
    menu_titles = {ROOT_MENU: "Functions"}
    n_menu = 0
    while queue:
        mp = queue.pop(0)
        if mp in visited:
            continue
        visited.add(mp)
        try:
            d = fetch(mp)
        except Exception as e:  # noqa: BLE001 — network best-effort; report and continue
            print(f"  skip menu {mp}: {e}")
            continue
        n_menu += 1
        cat = menu_titles.get(mp, mp)
        for raw, title in HREF.findall(d.get("topic_html", "")):
            p = norm(raw)
            if not p or p == mp:
                continue
            title = re.sub(r"\s+", " ", title).strip()
            if p.endswith("-mnu.html"):
                if p not in visited:
                    menu_titles.setdefault(p, title or p)
                    queue.append(p)
            elif p not in functions:
                functions[p] = {"title": title, "slug": p.split("/")[-1],
                                "path": p, "category": cat}
        if n_menu % 20 == 0:
            print(f"  ...{n_menu} menus, {len(functions)} leaves")
        time.sleep(0.05)
    return {"functions": functions, "menus": sorted(visited),
            "source": "docs-be.ni.com/api/bundle/labview-api-ref (public)"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if the file exists")
    args = ap.parse_args()

    if args.out.exists() and not args.force:
        data = json.loads(args.out.read_text())
        n = len(data.get("functions", {}))
        print(f"catalog present: {args.out} ({n} pages). Use --force to rebuild.")
        return

    print("crawling NI functions menu tree (public docs)...")
    data = crawl()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1))
    fns = data["functions"]
    prim = sum(1 for p in fns if p.startswith("functions/"))
    print(f"\nDONE: {len(data['menus'])} menus -> {len(fns)} pages "
          f"({prim} under functions/). Wrote {args.out}")


if __name__ == "__main__":
    main()
