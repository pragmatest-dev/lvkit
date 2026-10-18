"""Single-process Shared Variables for generated code.

A single-process Shared Variable holds one value shared by everything in the
application that reads or writes it, as a global variable does. It belongs to a
project library, so it is named by the library and the variable. Every generated
module that uses the same pair reaches the same value.

The timestamp is the time of the last write, in seconds since the Unix epoch (the
form ``Get Date/Time In Seconds`` generates); a variable nothing has written yet
has timestamp ``0.0``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Cell:
    value: Any
    timestamp: float = 0.0


_cells: dict[tuple[str, str], _Cell] = {}
_lock = threading.Lock()


def read(library: str, variable: str, default: Any) -> Any:
    """The variable's current value; ``default`` (the variable's type default)
    when nothing has written it."""
    with _lock:
        return _cells.setdefault((library, variable), _Cell(default)).value


def write(library: str, variable: str, value: Any) -> None:
    """Set the variable's value and record the time of the write."""
    with _lock:
        _cells[(library, variable)] = _Cell(value, time.time())


def timestamp(library: str, variable: str) -> float:
    """The time of the variable's last write."""
    with _lock:
        cell = _cells.get((library, variable))
        return 0.0 if cell is None else cell.timestamp
