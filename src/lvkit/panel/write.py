"""Writes the front panel of a VI: ``<vi>_panel.py`` next to the VI's module, and
the ``controls`` package at the package root (once)."""

from __future__ import annotations

import shutil
from pathlib import Path

from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.gaps import UNSUPPORTED_CONTROL, Gap
from lvkit.codegen.panel_hooks import PanelHooks
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext

from .kinds import PanelControl
from .layout import place
from .model import panel_controls
from .module import PanelSource, panel_source

CONTROLS_PACKAGE = "controls"
_RUNTIME = Path(__file__).parent / "runtime"


def _write_controls(package_root: Path) -> None:
    """The helper package of the panels, written once into the package root, and
    the package's ``errors`` module it imports when the package has none yet."""
    errors = package_root / f"{ERRORS_MODULE_NAME}.py"
    if not errors.exists():
        errors.write_text(ERRORS_MODULE_SOURCE, encoding="utf-8")
    target = package_root / CONTROLS_PACKAGE
    if not target.exists():
        shutil.copytree(_RUNTIME, target, ignore=shutil.ignore_patterns("__pycache__"))


def _gap_of(vi_name: str, control: PanelControl) -> Gap:
    return Gap(
        vi=vi_name,
        node_id=None,
        node_name=control.label,
        kind=UNSUPPORTED_CONTROL,
        identifier=control.label,
        name=control.label,
        reason=str(control.gap),
        detail="",
    )


def write_panel(
    graph: InMemoryVIGraph,
    vi_name: str,
    vi_context: VIContext,
    hooks: PanelHooks | None,
    function: str,
    logic_path: Path,
    package_root: Path,
    gaps: list[Gap],
) -> Path:
    """Write the panel of ``vi_name`` next to its module ``logic_path`` (the
    function ``function``) in the package ``package_root``, and return its path.
    Each control the panel has no widget for is added to ``gaps``; the panel
    raises ``NotImplementedError`` where it would build that control."""
    controls = panel_controls(graph, vi_name, vi_context, hooks)
    for control in controls:
        if control.gap is not None:
            gaps.append(_gap_of(vi_name, control))
    front_panel = graph.get_vi_front_panel(vi_name)
    panel_bounds = front_panel.panel_bounds if front_panel else (0, 0, 400, 600)
    boxes, width, height = place(controls, panel_bounds)
    in_package = logic_path.relative_to(package_root)
    source = panel_source(
        PanelSource(
            vi_label=Path(vi_name).name,
            controls=controls,
            boxes=boxes,
            width=width,
            height=height,
            module=".".join(
                logic_path.relative_to(package_root.parent).with_suffix("").parts
            ),
            function=function,
            enum_name=hooks.enum_name if hooks is not None else None,
            root=package_root.name,
            depth=len(in_package.parts),
        )
    )
    panel_path = logic_path.with_name(f"{logic_path.stem}_panel.py")
    panel_path.write_text(source, encoding="utf-8")
    _write_controls(package_root)
    return panel_path
