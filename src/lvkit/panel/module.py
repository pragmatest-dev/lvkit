"""The source of ``<vi>_panel.py``: the function ``build_panel`` that draws the
front panel of one VI and runs the VI from it, and the lines that serve it when
the file is run."""

from __future__ import annotations

from dataclasses import dataclass

from .kinds import PanelControl
from .layout import PanelRows, drawn
from .widget_call import builders_used, modules_used, widget_call


@dataclass(frozen=True)
class PanelSource:
    """What the panel module is written from. ``module`` is the dotted path of
    the VI's module and ``root`` that of the package; ``depth`` is how many
    folders up from the panel file the folder that holds the package is."""

    vi_label: str
    controls: list[PanelControl]
    rows: PanelRows  # the drawn controls, grouped
    module: str
    function: str
    enum_name: str | None
    event_enum_name: str | None
    root: str
    depth: int


def _key(control: PanelControl, enum_name: str | None) -> str:
    """The name of the control as the hooks pass it: the enum member, or the name
    string when the VI has no enum."""
    if enum_name is not None and control.member is not None:
        return f"{enum_name}.{control.member}"
    return repr(control.key)


def _gap_message(control: PanelControl, vi_label: str) -> str:
    return "\n".join(
        [
            str(control.gap),
            "gap kind: unsupported_control",
            f"VI: {vi_label}",
            f"control: {control.label}",
        ]
    )


def _widget_call(control: PanelControl, enum_name: str | None, vi_label: str) -> str:
    if control.kind is None:
        return f"raise NotImplementedError({_gap_message(control, vi_label)!r})"
    return widget_call(control, control.kind, _key(control, enum_name))


def _argument(control: PanelControl, enum_name: str | None) -> str | None:
    """How the function takes the control: the value on the panel. A control the
    panel does not draw gets the type's default (``None``), written only when its
    parameter has no default of its own."""
    if control.hidden:
        return f"{control.parameter}=None" if control.required else None
    return f"{control.parameter}=values.value({_key(control, enum_name)})"


def panel_source(spec: PanelSource) -> str:
    """The panel module."""
    enum = spec.enum_name
    used = sorted(
        {
            name
            for c in drawn(spec.controls)
            if c.kind is not None
            for name in builders_used(c, c.kind)
        }
    )
    imports = ", ".join(["LiveValues", "RunController", "caption", *used])
    stdlib = sorted(
        {"sys"}.union(
            *(modules_used(c) for c in drawn(spec.controls) if c.kind is not None)
        )
    )
    logic_names = ", ".join(
        [f"{spec.function} as vi_function", *filter(None, [enum, spec.event_enum_name])]
    )
    arguments = [
        argument
        for c in spec.controls
        if c.parameter is not None and not c.is_indicator and c.kind is not None
        if (argument := _argument(c, enum)) is not None
    ]
    if enum is not None:
        arguments.append("live_io=values")
    painted = [
        f"values.show({_key(c, enum)}, result.{c.field})"
        for c in spec.controls
        if c.field is not None and c.is_indicator and not c.hidden
        if c.kind is not None
    ]
    lines = [
        f'"""Front panel of {spec.vi_label}. Run it with ``python <this file>``."""',
        "",
        "from __future__ import annotations",
        "",
        *(f"import {name}" for name in stdlib),
        "from pathlib import Path",
        "from typing import Any",
        "",
        "from nicegui import ui",
        "",
        'if __package__ in (None, ""):',
        f"    sys.path.insert(0, str(Path(__file__).resolve().parents[{spec.depth}]))",
        "",
        f"from {spec.root}.controls import {imports}  # noqa: E402",
        f"from {spec.module} import {logic_names}  # noqa: E402",
        "",
        "",
        "def build_panel() -> None:",
        f"    values = LiveValues({spec.event_enum_name or ''})",
        "",
        "    def call() -> Any:",
        "        return vi_function(",
        *[f"            {argument}," for argument in arguments],
        "        )",
        "",
        "    def paint(result: Any) -> None:",
        *[f"        {line}" for line in painted or ["pass"]],
        "",
        "    controller = RunController(",
        f"        values, call, paint, abortable={enum is not None}",
        "    )",
        "    controller.toolbar()",
        "    ui.timer(0.1, values.paint)",
        "    with ui.column().classes('gap-6'):",
    ]
    for row in spec.rows:
        lines.append("        with ui.row().classes('items-start gap-6'):")
        for column in row:
            lines.append("            with ui.column():")
            for control in column:
                lines += [
                    f"                caption({control.label!r})",
                    f"                {_widget_call(control, enum, spec.vi_label)}",
                ]
    if not spec.rows:
        lines.append("        pass")
    lines += [
        "",
        "",
        '@ui.page("/")',
        "def index() -> None:",
        "    build_panel()",
        "",
        "",
        'if __name__ in {"__main__", "__mp_main__"}:',
        "    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080",
        f"    ui.run(title={spec.vi_label!r}, reload=False, show=False, port=port)",
        "",
    ]
    return "\n".join(lines)
