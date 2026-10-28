"""A translator function that is both called by code generation and written into
the generated module, from one source."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


def function_source(function: Callable[..., Any], name: str) -> str:
    """The source of ``function`` defined under ``name``."""
    return inspect.getsource(function).replace(
        f"def {function.__name__}(", f"def {name}(", 1
    )
