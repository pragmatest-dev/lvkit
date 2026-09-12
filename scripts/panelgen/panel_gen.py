"""Generates ``panel.py``: a NiceGUI panel whose LAYOUT is driven by the VI's
real front-panel geometry (``ParsedFPControl.bounds``), not a hand-picked
layout.

Top-level controls get an absolutely-positioned wrapper at
``(bounds.left, bounds.top)`` (normalized so the min left/top sit at a small
margin), width = ``bounds.right - bounds.left`` -- so the panel visually
echoes the VI's own arrangement. A ``stdClust`` control's CHILDREN are laid
out in a simple flowed column instead: the FPHb heap gives each cluster
child's ``bounds`` in the cluster's own nested-pane *document* coordinate
space (which can be negative and is offset by that pane's own ``origin`` --
see this generator's report for a worked example), and ``ParsedFPControl``
does not carry that pane's origin, so there isn't enough data here to place
cluster children absolutely without guessing. Flowing them is honest about
that gap instead of emitting plausible-looking but wrong pixel math.

Only ``bind_value`` targets are data-driven: which State attribute a widget
binds to. The one-shot Run handler match is also data-driven, by NAME
against the *real* signature of the generated logic entry function
(introspected via ``inspect``/``typing.get_type_hints`` on the freshly
written ``<vi>.py``), falling back to positional order when a name doesn't
match -- never a hand-picked mapping.

Widget/state-field/array-column emission for a given control_type lives in
``strategies.py`` (``strategy_for(control)``); this module owns layout
(container placement, sizing) and the logic-call wiring, and asks each
control's strategy for its emitted source and its runtime import needs.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .naming import unique_field_names
from .state_gen import build_state_classes
from .strategies import (
    CLUSTER_ROW_H,
    ArrayStrategy,
    BoolStrategy,
    ControlStrategy,
    strategy_for,
)

_MARGIN = 16
# An array control's caption sits above its data box (LabVIEW draws it there);
# the wrapper is lifted by this so the box itself keeps the full bounds height.
# Scalars get the same caption treatment for a consistent, faithful layout.
_CAPTION_H = 16


def _load_entry_function(logic_path: Path, func_name: str) -> typing.Callable:
    # A non-leaf VI's <vi>.py imports its VI-named SubVI sibling modules by name,
    # so the panel dir must be on sys.path for those to resolve during
    # introspection. Afterward, evict whatever this import pulled in from that
    # dir so a later VI's generation (the gallery does many in one process)
    # starts from a clean sys.modules.
    panel_dir = str(logic_path.parent)
    added = panel_dir not in sys.path
    if added:
        sys.path.insert(0, panel_dir)
    before = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "_panelgen_logic_probe", logic_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load generated logic module: {logic_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, func_name)
    finally:
        if added:
            sys.path.remove(panel_dir)
        for name in set(sys.modules) - before:
            mod = sys.modules.get(name)
            mod_file = getattr(mod, "__file__", None)
            if mod_file and str(Path(mod_file).parent) == panel_dir:
                del sys.modules[name]


def introspect_entry(
    logic_path: Path, func_name: str
) -> tuple[list[str], list[str] | None]:
    """Import the freshly-written <vi>.py and read the REAL signature of its
    entry function: (parameter names in order, return NamedTuple field names
    or None). Runtime introspection, not a guess -- `from __future__ import
    annotations` makes every annotation a string, so this resolves them via
    `typing.get_type_hints` rather than trusting `Signature.return_annotation`
    directly (which would just be the unresolved string).
    """
    func = _load_entry_function(logic_path, func_name)
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    result_fields: list[str] | None = None
    hints = typing.get_type_hints(func)
    return_type = hints.get("return")
    if return_type is not None:
        fields = getattr(return_type, "_fields", None)
        if fields:
            result_fields = list(fields)
    return param_names, result_fields


@dataclass
class _Placed:
    """One control's resolved on-panel geometry (px): css top/left, width, the
    real rendered height, and whether it's an AG Grid array."""

    control: ParsedFPControl
    strategy: ControlStrategy
    fname: str
    left: int
    right: int
    width: int
    top: int  # css top (arrays already lifted by the caption)
    render_h: int
    is_array: bool


# Chrome above/around an array's grid: the caption row (label + add button) plus
# the grid's own border, on top of the data rows.
_ARRAY_CHROME_H = 32
_ANTI_OVERLAP_GAP = 8
# An array OF CLUSTERS is a multi-column table: each cluster is one horizontal
# ROW under a column-header row of this height. MUST match the runtime grid's
# own header_h literal (controls/arraygrid.py) -- generation reserves the
# container space this constant claims, so the two can't be unified across the
# generation/runtime boundary (the runtime can't import scripts/panelgen) but
# must be kept in step by hand.
_CLUSTER_HEADER_H = 22
# Width to give each cluster-field column, plus the index + delete gutters.
_CLUSTER_COL_W = 96
_CLUSTER_GUTTER_W = 60
# A NiceGUI outlined dense input/number/select box is ~44px tall; a switch ~28.
# The caption above adds _CAPTION_H. LV boxes are shorter, so scalars get this
# usable box height (plus caption) rather than being clipped to the tiny box.
_SCALAR_BOX_H = 44
_SWITCH_BOX_H = 28


def _render_container(
    front_panel: ParsedFrontPanel, field_names: dict[str, str]
) -> tuple[list[str], int, int]:
    """Build the absolutely-positioned top-level widget block plus the
    container's own (width, height). Positions come from ``ParsedFPControl.bounds``
    (the ONE place bounds -> pixels happens); arrays render as AG Grids taller
    than their tiny FP box, so a MODERN reflow pass pushes any control a taller
    array would collide with straight down, preserving order + horizontal
    position. ``field_names`` is the SINGLE naming shared with the State."""
    controls = front_panel.controls
    if not controls:
        return [], 400, 200

    min_top = min(c.bounds[0] for c in controls)
    min_left = min(c.bounds[1] for c in controls)

    # 1) Base geometry from bounds; arrays get their real rendered height.
    placed: list[_Placed] = []
    for control in controls:
        fname = field_names[control.uid]
        strategy = strategy_for(control)
        top = control.bounds[0] - min_top + _MARGIN
        left = control.bounds[1] - min_left + _MARGIN
        w = control.bounds[3] - control.bounds[1]
        is_array = isinstance(strategy, ArrayStrategy)
        if is_array and strategy.is_cluster_array():
            # A cluster array is a multi-column table; the LV array box (drawn for
            # the tall vertical cluster) is too narrow for it, so widen to fit one
            # column per field plus the index/delete gutters.
            w = max(
                w,
                len(control.children) * _CLUSTER_COL_W + _CLUSTER_GUTTER_W,
            )
        cap = _CAPTION_H if (control.name or fname) else 0
        if is_array:
            cell_h, visible, _element_type, _integer, _num_min, _num_max = (
                strategy.geometry()
            )
            if strategy.is_cluster_array():  # array of clusters: rows + a header
                render_h = (
                    _ARRAY_CHROME_H + _CLUSTER_HEADER_H + visible * CLUSTER_ROW_H
                )
            else:
                render_h = _ARRAY_CHROME_H + visible * cell_h
            top -= cap  # caption sits above the data box
        else:
            # Scalars get the same caption-above-box treatment as arrays (the
            # label is a caption, not inside the box): lift by the caption height
            # and size to caption + a usable box height (a NiceGUI outlined input
            # is taller than the tiny LV box, so don't clip to it).
            top -= cap
            box_h = (
                _SWITCH_BOX_H if isinstance(strategy, BoolStrategy) else _SCALAR_BOX_H
            )
            render_h = cap + box_h
        placed.append(
            _Placed(
                control, strategy, fname, left, left + w, w, top, render_h, is_array
            )
        )

    # 2) Anti-overlap: top-down, push a control below any earlier one it would
    #    overlap both horizontally and vertically (repeat until it clears them).
    placed.sort(key=lambda p: (p.top, p.left))
    for i, p in enumerate(placed):
        moved = True
        while moved:
            moved = False
            for q in placed[:i]:
                horizontal = p.left < q.right and p.right > q.left
                vertical = p.top < q.top + q.render_h and p.top + p.render_h > q.top
                if horizontal and vertical:
                    p.top = q.top + q.render_h + _ANTI_OVERLAP_GAP
                    moved = True

    # 3) Emit, and size the container to the resolved layout. Every control is
    #    positioned from bounds (never clipped to the tiny LV box) with its
    #    caption above; the reflow above spaced each by its render_h.
    lines: list[str] = []
    for p in placed:
        style = f"position:absolute;left:{p.left}px;top:{p.top}px;width:{p.width}px;"
        lines.append(f"        with ui.element('div').style({style!r}):")
        lines.extend(p.strategy.emit_widget("state", p.fname, 12, [p.fname]))

    width = max(p.right for p in placed) + _MARGIN
    height = max(p.top + p.render_h for p in placed) + _MARGIN
    return lines, width, height


def _match_args(
    param_names: list[str],
    input_controls: list[ParsedFPControl],
    field_names: dict[str, str],
) -> list[str]:
    """Map each logic-function parameter to a `state.<field>` expression: by
    NAME first (against the input controls' own field names), then by
    position for whatever's left -- never a hand-picked mapping."""
    ordered = [field_names[c.uid] for c in input_controls]
    remaining = list(ordered)
    name_set = set(ordered)

    args: list[str] = []
    for param in param_names:
        if param in name_set and param in remaining:
            fname = param
            remaining.remove(fname)
        elif remaining:
            fname = remaining.pop(0)
        else:
            fname = None
        if fname:
            args.append(f"state.{fname}")
        else:
            args.append("None  # TODO: no matching input control")
    return args


def _match_outputs(
    result_fields: list[str] | None,
    output_controls: list[ParsedFPControl],
    field_names: dict[str, str],
) -> list[tuple[str, str]]:
    """Map each output indicator's field name to how to pull it out of the
    logic call's result: by NAME against the result NamedTuple's fields
    first, then by position. Returns (state_field_name, result_access_expr)
    pairs."""
    ordered = [field_names[c.uid] for c in output_controls]

    if not result_fields:
        # No NamedTuple result -- a single bare return value maps to the
        # sole output indicator; more than one indicator has nothing to
        # disambiguate against, so only the first is wired.
        if ordered:
            return [(ordered[0], "result")]
        return []

    remaining_fields = list(result_fields)
    pairs: list[tuple[str, str]] = []
    for out_name in ordered:
        if out_name in remaining_fields:
            remaining_fields.remove(out_name)
            pairs.append((out_name, f"result.{out_name}"))
        elif remaining_fields:
            picked = remaining_fields.pop(0)
            pairs.append((out_name, f"result.{picked}"))
    return pairs


def build_panel_module(
    front_panel: ParsedFrontPanel,
    logic_module_stem: str,
    logic_func_name: str,
    param_names: list[str],
    result_fields: list[str] | None,
    title: str = "Front panel",
    port: int = 8080,
) -> str:
    """Build the full ``<vi>_panel.py`` source: the bindable ``State`` view-model,
    ``build_panel()`` bound to the pure ``<vi>.py`` logic, and a ``__main__``
    runner so ``python <vi>_panel.py`` serves it. Importing the module (the
    gallery does) is side-effect-free -- the runner is guarded."""
    # ONE naming source, shared by the State fields, the widget vars, and the
    # arg/output matchers — so a widget's var and its state.<field> always agree.
    field_names = unique_field_names(front_panel.controls)
    widget_lines, width, height = _render_container(front_panel, field_names)

    input_controls = [c for c in front_panel.controls if not c.is_indicator]
    output_controls = [c for c in front_panel.controls if c.is_indicator]

    call_args = _match_args(param_names, input_controls, field_names)
    output_pairs = _match_outputs(result_fields, output_controls, field_names)

    # Top-level array indicators need an explicit refresh after Run writes a new
    # list (the composed control isn't bind_value-driven). Var == _w_<field>.
    # NOTE: top-level only -- an array indicator nested inside a cluster would not
    # get a _w_<field>() refresh here (its var is _w_<parent>_<child>). Arrays are
    # effectively always top-level on a LabVIEW FP, so this is an accepted edge.
    array_out_fields = {
        field_names[c.uid]
        for c in output_controls
        if isinstance(strategy_for(c), ArrayStrategy)
    }

    # Every control (recursing through clusters/arrays via each strategy's own
    # runtime_imports) reports what it needs from `controls` -- ONE walk
    # replaces the old separate has-array/has-path/has-cluster-array scans.
    needed: set[str] = set()
    for control in front_panel.controls:
        needed |= strategy_for(control).runtime_imports()

    controls_imports = ["RunController", "toolbar"]
    for name in ("CAPTION_CLS", "array_control", "ArrayField", "path_control"):
        if name in needed:
            controls_imports.append(name)

    state_src = build_state_classes(front_panel)

    lines: list[str] = [
        '"""Front panel reproduced from the VI\'s own front-panel geometry (see',
        'panelgen.panel_gen), bound to the pure logic in the sibling module. A',
        'LabVIEW-style toolbar (Run / Run Continuously / Abort / Pause) drives',
        'execution: Run latches the controls, calls the logic off the event loop',
        '(run.io_bound), then writes the outputs. The State view-model lives here',
        'with the UI; run this file directly to serve the panel."""',
        "",
        "from __future__ import annotations",
        "",
    ]
    if "field(" in state_src:
        lines.append("from dataclasses import field")
        lines.append("")
    lines += [
        "from nicegui import binding, run, ui",
        "",
        f"from controls import {', '.join(controls_imports)}",
        f"from {logic_module_stem} import {logic_func_name}",
        "",
        "",
        state_src,
        "",
        "",
        "def build_panel() -> None:",
        "    state = State()",
        "",
    ]
    # compute(): latch controls -> call pure logic -> write outputs. Handed to the
    # RunController, which the toolbar drives (Run once / Run Continuously / etc.).
    lines.append("    async def compute() -> None:")
    if call_args:
        lines.append("        result = await run.io_bound(")
        lines.append(f"            {logic_func_name},")
        for arg in call_args:
            lines.append(f"            {arg},")
        lines.append("        )")
    else:
        lines.append(f"        result = await run.io_bound({logic_func_name})")
    for state_field, result_expr in output_pairs:
        lines.append(f"        state.{state_field} = {result_expr}")
        if state_field in array_out_fields:
            # The composed array control isn't bind_value-driven; refresh it so
            # the new list shows. The widget var (see strategies.emit_widget) is
            # _w_<field>.
            lines.append(f"        _w_{state_field}()")
    if not output_pairs:
        lines.append("        _ = result  # no output indicator to write it into")
    lines.append("")
    lines.append("    controller = RunController(compute)")
    lines.append("    toolbar(controller)")
    lines.append("")
    lines.append(
        f"    with ui.element('div').style("
        f"'position:relative;width:{width}px;height:{height}px;'):"
    )
    if widget_lines:
        lines.extend(widget_lines)
    else:
        lines.append("        pass  # no front-panel controls")
    lines.append("")
    lines.append("")
    lines.append('if __name__ in {"__main__", "__mp_main__"}:')
    lines.append("    build_panel()")
    lines.append(
        f"    ui.run(title={title!r}, reload=False, port={port}, show=False)"
    )
    lines.append("")
    return "\n".join(lines)
