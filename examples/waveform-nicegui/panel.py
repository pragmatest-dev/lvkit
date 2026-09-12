"""The UI wrapper (front panel) bound to the pure logic. Same building blocks a
generated panel uses: the VI toolbar + RunController, a bindable State, and the
control library's waveform_indicator (ui.echart). Demonstrates plan step 6
(waveform via a maintained charting lib) and the Run / Run-Continuous model:
Run draws one frame; Run Continuously animates it (phase advances each tick)."""

from __future__ import annotations

from controls import RunController, toolbar, waveform_indicator
from logic import sine_waveform
from nicegui import run, ui
from state import State


def build_panel() -> None:
    state = State()

    async def compute() -> None:
        # Latch the controls, call the pure logic, write the output; on a
        # continuous run the phase advances so the waveform animates.
        state.phase += 0.25
        state.waveform = await run.io_bound(
            sine_waveform, state.frequency, state.amplitude, state.phase
        )
        _w_wave()

    controller = RunController(compute)
    toolbar(controller)

    with ui.column().classes("gap-2 p-3").style("width:460px"):
        with ui.row().classes("gap-4 items-center"):
            ui.number("frequency", value=3.0, min=0.5, max=20, step=0.5).props(
                "dense"
            ).bind_value(state, "frequency")
            ui.number("amplitude", value=1.0, min=0.1, max=5, step=0.1).props(
                "dense"
            ).bind_value(state, "amplitude")
        _w_wave = waveform_indicator(state, "waveform", label="waveform")
