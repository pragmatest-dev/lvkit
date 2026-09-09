"""Front panel for Build Path, derived from its connector pane.

The connector pane gives two typed inputs (base path, name/relative path) and
one output (appended path). Each input's type picks its widget; widgets bind to
a plain dataclass, and the Run handler calls the pure logic off the event loop
via ``run.io_bound`` so a slow VI never freezes the UI. This is the shape the
NiceGUI generator will emit from any function-style VI's pane.
"""

from __future__ import annotations

from dataclasses import dataclass

from logic import build_path
from nicegui import run, ui


@dataclass
class Inputs:
    base_path: str = "/tmp/data"
    name_or_relative_path: str = "report.txt"


def build_panel() -> None:
    state = Inputs()

    ui.label("Build Path").classes("text-2xl font-bold")
    ui.label(
        "OpenG VI · front panel → NiceGUI, bound to generated logic"
    ).classes("text-sm text-gray-500")

    with ui.card().classes("w-96"):
        ui.input("base path").bind_value(state, "base_path").classes("w-full")
        ui.input("name or relative path").bind_value(
            state, "name_or_relative_path"
        ).classes("w-full")
        out = ui.input("appended path").props("readonly").classes("w-full")

        async def on_run() -> None:
            result = await run.io_bound(
                build_path, state.base_path, state.name_or_relative_path
            )
            out.value = str(result.appended_path)

        ui.button("Run", on_click=on_run).classes("mt-2")
