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
written ``logic.py``), falling back to positional order when a name doesn't
match -- never a hand-picked mapping.
"""

from __future__ import annotations

import importlib.util
import inspect
import typing
from pathlib import Path

from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .control_types import control_type_info, is_known_control_type
from .naming import unique_field_names

_MARGIN = 16


def _load_entry_function(logic_path: Path, func_name: str) -> typing.Callable:
    spec = importlib.util.spec_from_file_location("_panelgen_logic_probe", logic_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load generated logic module: {logic_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, func_name)


def introspect_entry(
    logic_path: Path, func_name: str
) -> tuple[list[str], list[str] | None]:
    """Import the freshly-written logic.py and read the REAL signature of its
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
        # Editable/read-only 1D array, composed from native NiceGUI (controls.py).
        # Returns a refresh callable used to re-render an indicator after Run.
        lines.append(
            f"{prefix}{var} = array_control({owner_expr}, {field_name!r}, "
            f"readonly={control.is_indicator}, label={label!r})"
        )
        return lines

    if control.control_type == "stdEnum":
        lines.append(
            f"{prefix}{var} = ui.select({control.enum_values!r}, label={label!r})"
        )
    elif info.widget == "switch":
        lines.append(f"{prefix}{var} = ui.switch({label!r})")
    elif info.widget == "number":
        lines.append(f"{prefix}{var} = ui.number(label={label!r}).classes('w-full')")
    else:
        lines.append(f"{prefix}{var} = ui.input({label!r}).classes('w-full')")

    lines.append(f"{prefix}{var}.bind_value({owner_expr}, {field_name!r})")
    if control.is_indicator:
        lines.append(f"{prefix}{var}.disable()")
    return lines


def _render_container(
    front_panel: ParsedFrontPanel, field_names: dict[str, str]
) -> tuple[list[str], int, int]:
    """Build the absolutely-positioned top-level widget block plus the
    container's own (width, height), derived entirely from
    ``ParsedFPControl.bounds`` -- the ONE place bounds -> pixels happens.
    ``field_names`` is the SINGLE naming shared with state.py and the arg/output
    matchers, so a widget's var and its ``state.<field>`` always agree."""
    controls = front_panel.controls
    if not controls:
        return [], 400, 200

    min_top = min(c.bounds[0] for c in controls)
    min_left = min(c.bounds[1] for c in controls)
    max_bottom = max(c.bounds[2] for c in controls)
    max_right = max(c.bounds[3] for c in controls)
    width = (max_right - min_left) + 2 * _MARGIN
    height = (max_bottom - min_top) + 2 * _MARGIN

    lines: list[str] = []
    for control in controls:
        fname = field_names[control.uid]
        top = control.bounds[0] - min_top + _MARGIN
        left = control.bounds[1] - min_left + _MARGIN
        w = control.bounds[3] - control.bounds[1]
        h = control.bounds[2] - control.bounds[0]
        style = (
            f"position:absolute;left:{left}px;top:{top}px;"
            f"width:{w}px;min-height:{h}px;"
        )
        lines.append(f"        with ui.element('div').style({style!r}):")
        lines.extend(_render_widget(control, "state", fname, 12, [fname]))
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


def build_panel_module(
    front_panel: ParsedFrontPanel,
    logic_module_stem: str,
    logic_func_name: str,
    param_names: list[str],
    result_fields: list[str] | None,
) -> str:
    """Build the full ``panel.py`` source."""
    # ONE naming source, shared by state.py, the widget vars, and the arg/output
    # matchers — so a widget's var and its state.<field> always agree.
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

    lines: list[str] = [
        '"""Front panel, laid out from the VI\'s own front-panel geometry',
        '(control bounds) -- see panelgen.panel_gen for how bounds become',
        'pixels. Binds widgets to State; the Run handler calls the pure',
        'logic function off the event loop via run.io_bound."""',
        "",
        "from __future__ import annotations",
        "",
        f"from {logic_module_stem} import {logic_func_name}",
        "from nicegui import run, ui",
        "",
        "from state import State",
    ]
    if has_array:
        lines.append("from controls import array_control")
    lines += [
        "",
        "",
        "def build_panel() -> None:",
        "    state = State()",
        "",
        f"    with ui.element('div').style("
        f"'position:relative;width:{width}px;height:{height}px;'):",
    ]
    if widget_lines:
        lines.extend(widget_lines)
    else:
        lines.append("        pass  # no front-panel controls")
    lines.append("")
    lines.append("    async def on_run() -> None:")
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
    lines.append("    ui.button('Run', on_click=on_run).classes('mt-2')")
    lines.append("")
    return "\n".join(lines)
