#!/usr/bin/env python3
"""Generate a NiceGUI gallery of front panels for every VI in a corpus that
converts cleanly.

Recursively finds ``*.vi`` under a corpus directory, generates each one's
``<vi>.py`` + ``<vi>_panel.py`` via ``scripts/panelgen`` (see
``scripts/gen_panel.py``) into ONE shared ``panels`` directory, and keeps only
the VIs whose generation succeeds AND whose ``<vi>_panel.py`` imports (and whose
``build_panel()`` runs) without raising. It then emits a single
``gallery_app.py``: one NiceGUI page with a list of the converted VIs on the
left and a content area on the right that renders the selected VI's panel (see
``scripts/panelgen/loader.py`` for how it imports each VI-named panel module).

nicegui is not a lvkit dependency; run the generated app with:
  uv run --with nicegui python <output>/gallery_app.py
"""

from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from panelgen import generate_panel, load_build_panel  # noqa: E402

_SKIP_SUBSTRINGS = ("example", "vi tree")
_SCRIPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class GalleryEntry:
    """One VI that made it into the gallery."""

    panel_stem: str  # <vi>_panel — imported + run to render this VI's panel
    title: str


def _iter_candidate_vis(corpus_dir: Path) -> list[Path]:
    """Every ``*.vi`` under ``corpus_dir``, skipping example/tree VIs, in a
    deterministic (sorted) order."""
    candidates = []
    for vi_path in sorted(corpus_dir.rglob("*.vi")):
        name_lower = vi_path.name.lower()
        if any(skip in name_lower for skip in _SKIP_SUBSTRINGS):
            continue
        candidates.append(vi_path)
    return candidates


def _verify_panel(panels_dir: Path, panel_stem: str) -> tuple[bool, str]:
    """Import ``<vi>_panel.py`` (which pulls in its ``<vi>.py`` logic + shared
    ``controls``) and run ``build_panel()`` once against a scratch element that's
    discarded right after, to confirm this panel is actually usable -- not just
    that generation produced files."""
    try:
        build_panel = load_build_panel(panels_dir, panel_stem)
    except Exception:
        return False, f"{panel_stem}.py import failed:\n" + traceback.format_exc()

    from nicegui import ui

    probe = ui.element("div")
    try:
        with probe:
            build_panel()
    except Exception:
        return False, "build_panel() raised:\n" + traceback.format_exc()
    finally:
        probe.delete()
    return True, ""


def build_gallery(
    corpus_dirs: Path | list[Path],
    out_dir: Path,
    limit: int | None = None,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> tuple[list[GalleryEntry], list[tuple[Path, str]]]:
    """Generate + verify a panel for each candidate VI under ``corpus_dirs``
    (one directory, or several -- each scanned in order, candidates
    concatenated before ``limit`` is applied), all into ONE flat ``panels``
    directory of VI-named modules. Returns the entries that made it in and the
    (vi_path, reason) failures that didn't."""
    out_dir = Path(out_dir)
    panels_dir = out_dir / "panels"
    panels_dir.mkdir(parents=True, exist_ok=True)

    dirs = [corpus_dirs] if isinstance(corpus_dirs, Path) else corpus_dirs
    candidates = [vi for corpus_dir in dirs for vi in _iter_candidate_vis(corpus_dir)]
    if limit is not None:
        candidates = candidates[:limit]

    entries: list[GalleryEntry] = []
    failures: list[tuple[Path, str]] = []
    seen_stems: set[str] = set()

    for vi_path in candidates:
        try:
            result = generate_panel(
                vi_path,
                panels_dir,
                search_paths=search_paths or [vi_path.parent],
                vilib_root=vilib_root,
                userlib_root=userlib_root,
            )
        except Exception:
            reason = "generate_panel failed:\n" + traceback.format_exc()
            failures.append((vi_path, reason))
            continue

        # A VI reached as another entry's SubVI (same module) may recur as its
        # own candidate; list it once.
        if result.panel_stem in seen_stems:
            continue

        ok, reason = _verify_panel(panels_dir, result.panel_stem)
        if not ok:
            failures.append((vi_path, reason))
            continue

        seen_stems.add(result.panel_stem)
        entries.append(GalleryEntry(panel_stem=result.panel_stem, title=result.title))

    return entries, failures


def _write_gallery_app(out_dir: Path, entries: list[GalleryEntry], port: int) -> Path:
    """Emit ``gallery_app.py``: one page, a VI list on the left, a content
    area on the right that ``.clear()``s and re-``build_panel()``s on click.
    """
    rows = ",\n".join(f"    ({e.panel_stem!r}, {e.title!r})" for e in entries)
    app_src = f'''"""Gallery viewer for converted LabVIEW front panels.

Run it: uv run --with nicegui python gallery_app.py
Then open http://localhost:{port}
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, {str(_SCRIPTS_DIR)!r})

from nicegui import ui  # noqa: E402

from panelgen import load_build_panel  # noqa: E402

PANELS_DIR = Path(__file__).resolve().parent / "panels"

# (panel_stem, title) for every VI that generated and imported cleanly.
ENTRIES = [
{rows}
]


@ui.page("/")
def index() -> None:
    with ui.row().classes("w-full h-full no-wrap"):
        _sidebar_classes = "w-64 shrink-0 border-r p-2 gap-1 h-screen overflow-auto"
        with ui.column().classes(_sidebar_classes):
            ui.label("VIs").classes("text-lg font-semibold")
            for stem, title in ENTRIES:
                ui.button(
                    title,
                    on_click=lambda stem=stem, title=title: show(stem, title),
                ).props("flat align=left").classes("w-full justify-start")

        content = ui.column().classes("flex-grow p-4")

        def show(stem: str, title: str) -> None:
            content.clear()
            build_panel = load_build_panel(PANELS_DIR, stem)
            with content:
                ui.label(title).classes("text-xl font-bold mb-2")
                build_panel()


ui.run(title="lvkit Gallery", reload=False, port={port}, show=False)
'''
    app_path = out_dir / "gallery_app.py"
    app_path.write_text(app_src, encoding="utf-8")
    return app_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "corpus",
        nargs="+",
        help="Directory (or directories) to recursively search for .vi files",
    )
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument(
        "--limit", type=int, default=None, help="Attempt at most N candidate VIs"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port the gallery app serves on"
    )
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

    out_dir = Path(args.output)
    entries, failures = build_gallery(
        [Path(p) for p in args.corpus],
        out_dir,
        limit=args.limit,
        search_paths=[Path(p) for p in args.search_paths] or None,
        vilib_root=Path(args.vilib) if args.vilib else None,
        userlib_root=Path(args.userlib) if args.userlib else None,
    )
    app_path = _write_gallery_app(out_dir, entries, args.port)

    print(f"\nConverted: {len(entries)}  Skipped: {len(failures)}")
    for vi_path, reason in failures:
        first_line = reason.splitlines()[0] if reason else "unknown failure"
        print(f"  SKIP {vi_path}: {first_line}")
    print(f"\nGallery app: {app_path}")
    print(f"Run:  uv run --with nicegui python {app_path}")


if __name__ == "__main__":
    main()
