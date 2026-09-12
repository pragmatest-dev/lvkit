"""Pure logic for the waveform example -- written as if the UI does not exist
(no NiceGUI import), so it is directly testable / TesterKit-able. Stands in for
a VI's block diagram: given the front-panel inputs, return the outputs."""

from __future__ import annotations

import math


def sine_waveform(
    frequency: float, amplitude: float, phase: float = 0.0, samples: int = 200
) -> list[float]:
    """``amplitude * sin(2*pi*frequency*t + phase)`` over one normalized period,
    as ``samples`` y-values -- the waveform an indicator graphs."""
    return [
        amplitude * math.sin(2.0 * math.pi * frequency * i / samples + phase)
        for i in range(samples)
    ]
