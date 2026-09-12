"""The UI wrapper (front panel) bound to the pure logic — the same building
blocks a generated panel uses: a bindable ``State``, the VI toolbar +
``RunController``, and the control library's ``path_control`` (an outlined field
with a real server-filesystem Browse). Run latches the two path inputs, calls the
pure logic, and writes the appended path.
"""

from __future__ import annotations

from controls import RunController, path_control, toolbar
from logic import build_path
from nicegui import binding, run, ui


@binding.bindable_dataclass
class State:
    base_path: str = "/tmp/data"
    name_or_relative_path: str = "report.txt"
    appended_path: str = ""


def build_panel() -> None:
    state = State()

    async def compute() -> None:
        # Latch the inputs, call the pure logic, write the appended path.
        result = await run.io_bound(
            build_path, state.base_path, state.name_or_relative_path
        )
        state.appended_path = str(result.appended_path)

    controller = RunController(compute)
    toolbar(controller)

    with ui.column().classes("gap-3 p-3").style("width:460px"):
        path_control(state, "base_path", label="base path")
        ui.input("name or relative path").props("outlined dense").classes(
            "w-full"
        ).bind_value(state, "name_or_relative_path")
        path_control(state, "appended_path", readonly=True, label="appended path")
