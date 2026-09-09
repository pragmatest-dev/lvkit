#!/usr/bin/env python3
"""Generate a NiceGUI front panel from a LabVIEW VI's real front-panel
geometry.

Emits four files into the output directory:
  logic.py  - the block-diagram logic (via lvkit's build_module)
  state.py  - a plain dataclass, one field per control/indicator
  panel.py  - a NiceGUI panel laid out from the VI's control bounds
  app.py    - serves the panel

See scripts/panelgen/ for the generator itself. nicegui is not a lvkit
dependency; run the generated app with:
  uv run --with nicegui python <output>/app.py
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

    output_dir = generate_panel(
        args.input,
        args.output,
        search_paths=[Path(p) for p in args.search_paths] or None,
        vilib_root=Path(args.vilib) if args.vilib else None,
        userlib_root=Path(args.userlib) if args.userlib else None,
    )
    print(f"\nGenerated panel in: {output_dir}")
    print(f"Run:  uv run --with nicegui python {output_dir / 'app.py'}")


if __name__ == "__main__":
    main()
