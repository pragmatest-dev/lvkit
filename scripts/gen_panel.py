#!/usr/bin/env python3
"""Generate a NiceGUI front panel from a LabVIEW VI's real front-panel
geometry.

Emits two VI-named files into the output directory, plus one shared runtime:
  <vi>.py        - the pure block-diagram logic (via lvkit's build_module),
                   headless, no UI imports
  <vi>_panel.py  - the NiceGUI UI (State + panel laid out from the VI's control
                   bounds + a __main__ runner), bound to <vi>.py
  controls.py    - the shared control runtime (one per directory)

Many VIs can share one directory (the files are VI-named). nicegui is not a
lvkit dependency; run a generated panel with:
  uv run --with nicegui python <output>/<vi>_panel.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from panelgen import generate_panel  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="VI file to build a panel for")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Additional search paths for SubVI resolution",
    )
    parser.add_argument(
        "--vilib",
        default=None,
        metavar="DIR",
        help="Path to LabVIEW vi.lib on disk for <vilib> resolution.",
    )
    parser.add_argument(
        "--userlib",
        default=None,
        metavar="DIR",
        help="Path to LabVIEW user.lib on disk for <userlib> resolution.",
    )
    args = parser.parse_args()

    result = generate_panel(
        args.input,
        args.output,
        search_paths=[Path(p) for p in args.search_paths] or None,
        vilib_root=Path(args.vilib) if args.vilib else None,
        userlib_root=Path(args.userlib) if args.userlib else None,
    )
    panel_file = result.output_dir / f"{result.panel_stem}.py"
    print(f"\nGenerated panel in: {result.output_dir}")
    print(f"  logic:  {result.logic_stem}.py")
    print(f"  panel:  {result.panel_stem}.py")
    print(f"Run:  uv run --with nicegui python {panel_file}")


if __name__ == "__main__":
    main()
