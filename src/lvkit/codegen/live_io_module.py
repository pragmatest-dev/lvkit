"""The module a generated package holds for the live values of a running VI.

A function converted from a VI that touches its front panel while it runs takes
``live_io: LiveIO = NO_LIVE_IO`` (``codegen/panel_hooks.py``). The class is
written once into the package root as ``live_io.py``, the text of
``lvkit/panel/live_io.py``, and imported from there, so a generated package
imports nothing from lvkit. Every package has it, because ``PanelIO`` of
``panels.py`` subclasses ``LiveIO``.
"""

from __future__ import annotations

from pathlib import Path

LIVE_IO_MODULE_NAME = "live_io"
LIVE_IO_NAME = "LiveIO"
NO_LIVE_IO_NAME = "NO_LIVE_IO"

LIVE_IO_MODULE_SOURCE = (
    Path(__file__).resolve().parent.parent / "panel" / f"{LIVE_IO_MODULE_NAME}.py"
).read_text(encoding="utf-8")
