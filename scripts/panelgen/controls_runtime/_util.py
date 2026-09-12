"""Small presentation-boundary helpers shared by more than one control."""

from __future__ import annotations

import traceback
from typing import Any


def _jsonable(value: Any) -> Any:
    """Coerce a value to something NiceGUI can JSON-serialize to a widget. JSON
    primitives pass through; anything else (a ``pathlib.Path``, a datetime, ...)
    becomes its ``str()``. This is the presentation boundary: the pure logic
    keeps its real types (it returns ``Path`` objects), and the widget shows text
    -- without it a ``Path`` reaches NiceGUI's socket serializer and the update
    fails silently outside the Run error boundary (nothing renders)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _format_error(exc: BaseException) -> tuple[str, str]:
    """(one-line title, full traceback) for a caught exception."""
    title = f"{type(exc).__name__}: {exc}"
    detail = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return title, detail
