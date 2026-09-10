"""Top-level orchestration: one VI -> logic.py + state.py + panel.py + app.py
in ``output_dir``. See the module docstrings of ``logic_gen``/``state_gen``/
``panel_gen``/``app_gen`` for what each file contains and how.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.parser import parse_vi

from .app_gen import build_app_module
from .logic_gen import generate_logic
from .panel_gen import build_panel_module, introspect_entry
from .state_gen import build_state_module


def generate_panel(
    vi_path: Path | str,
    output_dir: Path | str,
    search_paths: list[Path] | None = None,
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
) -> Path:
    """Generate a NiceGUI front panel for ``vi_path`` into ``output_dir``.

    Prints any polymorphic-fallback note to stdout (see
    ``logic_gen.generate_logic``). Returns ``output_dir``.
    """
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

    state_src = build_state_module(front_panel)
    (output_dir / "state.py").write_text(state_src, encoding="utf-8")

    logic_path = output_dir / f"{logic_result.entry_module_stem}.py"
    param_names, result_fields = introspect_entry(
        logic_path, logic_result.entry_func_name
    )

    panel_src = build_panel_module(
        front_panel,
        logic_result.entry_module_stem,
        logic_result.entry_func_name,
        param_names,
        result_fields,
    )
    (output_dir / "panel.py").write_text(panel_src, encoding="utf-8")

    # Ship the composed-native control runtime alongside the panel so the
    # generated dir is self-contained (the gallery loads panels standalone).
    controls_src = Path(__file__).with_name("controls_runtime.py")
    shutil.copyfile(controls_src, output_dir / "controls.py")

    title = parsed.metadata.qualified_name or logic_result.entry_vi_path.stem
    (output_dir / "app.py").write_text(build_app_module(title), encoding="utf-8")

    return output_dir
