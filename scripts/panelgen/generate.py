"""Top-level orchestration: one VI -> two VI-named files in ``output_dir`` --
``<vi>.py`` (pure headless logic) and ``<vi>_panel.py`` (the NiceGUI UI: State +
build_panel + a __main__ runner) -- plus one shared ``controls/`` runtime
package per directory. Many VIs can therefore share a directory. See the
module docstrings of ``logic_gen`` / ``state_gen`` / ``panel_gen`` for what
each part contains.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from lvkit.parser import parse_vi

from .logic_gen import generate_logic
from .panel_gen import build_panel_module, introspect_entry


@dataclass(frozen=True)
class PanelResult:
    """What one VI's generation produced, for a caller (CLI / gallery) to run or
    reference."""

    output_dir: Path
    logic_stem: str  # <vi>.py — the pure logic module
    panel_stem: str  # <vi>_panel.py — the UI module (has build_panel + runner)
    title: str


def _write_controls_runtime(output_dir: Path) -> None:
    """Copy the shared control runtime PACKAGE to ``output_dir/controls/`` once
    (every ``<vi>_panel.py`` in the directory does ``from controls import
    ...``). Idempotent: overwriting with identical bytes is fine when several
    VIs target the same directory."""
    controls_src = Path(__file__).with_name("controls_runtime")
    shutil.copytree(
        controls_src,
        output_dir / "controls",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def generate_panel(
    vi_path: Path | str,
    output_dir: Path | str,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> PanelResult:
    """Generate ``<vi>.py`` + ``<vi>_panel.py`` (+ shared ``controls/`` runtime
    package) for ``vi_path`` into ``output_dir``. Prints any polymorphic-fallback
    note to stdout (see ``logic_gen.generate_logic``)."""
    vi_path = Path(vi_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_search_paths = search_paths or [vi_path.parent]

    logic_result = generate_logic(
        vi_path,
        output_dir,
        resolved_search_paths,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
    )
    if logic_result.polymorphic_note:
        print(f"NOTE: {logic_result.polymorphic_note}")

    parsed = parse_vi(logic_result.entry_vi_path)
    front_panel = parsed.front_panel

    logic_path = output_dir / f"{logic_result.entry_module_stem}.py"
    param_names, result_fields = introspect_entry(
        logic_path, logic_result.entry_func_name
    )

    title = parsed.metadata.qualified_name or logic_result.entry_vi_path.stem
    panel_src = build_panel_module(
        front_panel,
        logic_result.entry_module_stem,
        logic_result.entry_func_name,
        param_names,
        result_fields,
        title=title,
    )
    panel_stem = f"{logic_result.entry_module_stem}_panel"
    (output_dir / f"{panel_stem}.py").write_text(panel_src, encoding="utf-8")

    _write_controls_runtime(output_dir)

    return PanelResult(
        output_dir=output_dir,
        logic_stem=logic_result.entry_module_stem,
        panel_stem=panel_stem,
        title=title,
    )
