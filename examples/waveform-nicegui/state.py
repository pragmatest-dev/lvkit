"""Front-panel view-model: bindable inputs + the waveform output. Same shape a
generated panel's state.py has (see scripts/panelgen/state_gen)."""

from __future__ import annotations

from dataclasses import field

from nicegui import binding


@binding.bindable_dataclass
class State:
    frequency: float = 3.0
    amplitude: float = 1.0
    phase: float = 0.0
    waveform: list = field(default_factory=list)
