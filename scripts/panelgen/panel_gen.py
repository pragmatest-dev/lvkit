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
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .control_types import control_type_info, is_known_control_type
from .naming import unique_field_names
from .state_gen import build_state_classes

_MARGIN = 16
# An array control's caption sits above its data box (LabVIEW draws it there);
# the wrapper is lifted by this so the box itself keeps the full bounds height.
# Scalars get the same caption treatment for a consistent, faithful layout.
_CAPTION_H = 16
# The caption label's classes -- kept in step with controls.array_control's
# caption so a scalar's label and an array's caption render identically.
_CAPTION_CLS = "text-xs font-semibold text-gray-500 truncate leading-none"
# A usable minimum of visible array rows, so a tiny FP box isn't a 1-row
# peephole (the modern AG Grid presentation; the rest scroll).
_MIN_ARRAY_ROWS = 4


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


@dataclass(frozen=True)
class ArrayGeometry:
    """An array control's layout + element spec, read from the VI's front panel
    (never guessed): ``cell_h`` = one element cell's height, ``visible`` = how
    many WHOLE cells the box the developer drew shows, ``element_type`` = the
    cell's control class (``stdNum``/``stdString``/...), and for a numeric
    element ``integer`` (representation) plus the ``num_min``/``num_max`` data
    range straight from ``StdNumMin``/``StdNumMax``."""

    cell_h: int
    visible: int
    element_type: str
    integer: bool
    num_min: float | None
    num_max: float | None


def _num_prop(props: dict[str, str], key: str) -> float | None:
    """A numeric element property (``StdNumMin``/``StdNumMax``) as a number, or
    None if absent/unparseable. int when integral so it serialises cleanly."""
    raw = props.get(key)
    if raw is None:
        return None
    try:
        f = float(raw)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def _array_geometry(control: ParsedFPControl) -> ArrayGeometry:
    """Derive an array control's layout + element spec from its parsed parts (the
    element cell's geometry and PROPERTIES). Falls back to conservative sizes
    only if the parts are absent (older cache / an odd control)."""
    element = next((p for p in control.parts if p.part_id is None), None)
    outer_h = control.bounds[2] - control.bounds[0]
    if element is None:
        cell_h = 24
        return ArrayGeometry(cell_h, max(1, outer_h // cell_h), "stdNum",
                             True, None, None)
    cell_h = max(1, element.bounds[2] - element.bounds[0])
    # Whole cells that fit the box the developer drew (LabVIEW never shows a
    # partial row) — the count falls out of the real bounds + real cell height —
    # but floored to a usable minimum so a small FP box isn't a 1-row peephole
    # (the modern grid presentation; scroll reveals the rest).
    visible = max(_MIN_ARRAY_ROWS, outer_h // cell_h)
    num_min = _num_prop(element.props, "StdNumMin")
    num_max = _num_prop(element.props, "StdNumMax")
    # Integer representation when the data range is integral (I32/U8/...); a
    # float representation (DBL/SGL) carries a fractional/scientific range.
    integer = isinstance(num_min, int) and isinstance(num_max, int)
    return ArrayGeometry(cell_h, visible, element.part_class, integer,
                         num_min, num_max)


def _render_widget(
    control: ParsedFPControl,
    owner_expr: str,
    field_name: str,
    indent: int,
    var_path: list[str],
) -> list[str]:
    prefix = " " * indent
    var = "_w_" + "_".join(var_path)
    label = control.name or field_name
    lines: list[str] = []

    if control.control_type == "stdClust":
        cluster_classes = "border rounded-md p-2 gap-1"
        lines.append(f"{prefix}with ui.column().classes({cluster_classes!r}):")
        lines.append(
            f"{prefix}    ui.label({label!r})"
            ".classes('text-xs font-semibold text-gray-500')"
        )
        child_owner = f"{owner_expr}.{field_name}"
        child_names = unique_field_names(control.children)
        for child in control.children:
            child_field = child_names[child.uid]
            child_path = [*var_path, child_field]
            lines.extend(
                _render_widget(child, child_owner, child_field, indent + 4, child_path)
            )
        return lines

    info = control_type_info(control.control_type)
    if not is_known_control_type(control.control_type):
        lines.append(
            f"{prefix}# TODO: unsupported control_type {control.control_type!r} "
            "-- rendered as a text input"
        )

    if info.widget == "array":
        # LabVIEW-style array control (index display + whole element cells; see
        # controls.array_control). Its geometry is READ FROM THE VI: the parser
        # exposes the index display (partID 8002) and element cell as parts, so
        # the index-column width, cell height, visible-cell count and element
        # widget all come from the real front panel, not invented constants.
        geom = _array_geometry(control)
        lines.append(
            f"{prefix}{var} = array_control({owner_expr}, {field_name!r}, "
            f"readonly={control.is_indicator}, label={label!r}, "
            f"cell_h={geom.cell_h}, visible={geom.visible}, "
            f"element_type={geom.element_type!r}, integer={geom.integer}, "
            f"num_min={geom.num_min}, num_max={geom.num_max})"
        )
        return lines

    # A scalar control renders its label as a CAPTION ABOVE the box -- consistent
    # with the array control's caption, and faithful to the VI, where the label
    # part (partID 16) sits above the control box, not inside it. The container
    # (see _render_container) lifts the box by _CAPTION_H so the caption occupies
    # the space above. The widget itself carries no internal label. _CAPTION_CLS
    # matches controls.array_control's caption so scalars and arrays align.
    inner = " " * (indent + 4)
    lines.append(f"{prefix}with ui.column().classes('w-full gap-1 no-wrap'):")
    lines.append(f"{inner}ui.label({label!r}).classes({_CAPTION_CLS!r})")

    if control.control_type == "stdPath":
        # Real path control: an outlined field with a filesystem Browse on an
        # input (controls.path_control), which does its own state binding.
        lines.append(
            f"{inner}path_control({owner_expr}, {field_name!r}, "
            f"readonly={control.is_indicator})"
        )
        return lines

    outlined = ".props('outlined dense').classes('w-full')"
    if info.widget == "select":  # enum or ring -> dropdown of its options
        lines.append(f"{inner}{var} = ui.select({control.enum_values!r}){outlined}")
    elif info.widget == "switch":
        lines.append(f"{inner}{var} = ui.switch()")
    elif info.widget == "number":
        lines.append(f"{inner}{var} = ui.number(){outlined}")
    else:
        lines.append(f"{inner}{var} = ui.input(){outlined}")

    if control.is_indicator:
        # Output: one-way state -> widget, so Run's results display reactively.
        lines.append(f"{inner}{var}.bind_value_from({owner_expr}, {field_name!r})")
        lines.append(f"{inner}{var}.disable()")
    else:
        # Input: two-way, so edits latch into State for the next Run.
        lines.append(f"{inner}{var}.bind_value({owner_expr}, {field_name!r})")
    return lines


@dataclass
class _Placed:
    """One control's resolved on-panel geometry (px): css top/left, width, the
    real rendered height, and whether it's an AG Grid array."""

    control: ParsedFPControl
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
        top = control.bounds[0] - min_top + _MARGIN
        left = control.bounds[1] - min_left + _MARGIN
        w = control.bounds[3] - control.bounds[1]
        widget = control_type_info(control.control_type).widget
        is_array = widget == "array"
        cap = _CAPTION_H if (control.name or fname) else 0
        if is_array:
            geom = _array_geometry(control)
            render_h = _ARRAY_CHROME_H + geom.visible * geom.cell_h
            top -= cap  # caption sits above the data box
        else:
            # Scalars get the same caption-above-box treatment as arrays (the
            # label is a caption, not inside the box): lift by the caption height
            # and size to caption + a usable box height (a NiceGUI outlined input
            # is taller than the tiny LV box, so don't clip to it).
            top -= cap
            box_h = _SWITCH_BOX_H if widget == "switch" else _SCALAR_BOX_H
            render_h = cap + box_h
        placed.append(
            _Placed(control, fname, left, left + w, w, top, render_h, is_array)
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
        lines.extend(_render_widget(p.control, "state", p.fname, 12, [p.fname]))

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


def _has_array(controls: list[ParsedFPControl]) -> bool:
    for c in controls:
        if control_type_info(c.control_type).widget == "array":
            return True
        if c.control_type == "stdClust" and _has_array(c.children):
            return True
    return False


def _has_path(controls: list[ParsedFPControl]) -> bool:
    for c in controls:
        if c.control_type == "stdPath":
            return True
        if c.control_type == "stdClust" and _has_path(c.children):
            return True
    return False


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

    has_array = _has_array(front_panel.controls)
    # Top-level array indicators need an explicit refresh after Run writes a new
    # list (the composed control isn't bind_value-driven). Var == _w_<field>.
    array_out_fields = {
        field_names[c.uid]
        for c in output_controls
        if control_type_info(c.control_type).widget == "array"
    }

    controls_imports = ["RunController", "toolbar"]
    if has_array:
        controls_imports.append("array_control")
    if _has_path(front_panel.controls):
        controls_imports.append("path_control")

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
            # the new list shows. The widget var (see _render_widget) is _w_<field>.
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
