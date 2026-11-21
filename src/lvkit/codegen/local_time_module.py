"""The module a generated package holds for the computer's time zone.

It is written once into the package root as ``local_time.py``, only when a generated
module imports it, and imports nothing from the package or from lvkit. Its source is
the text of ``labview_local_time.py``, which holds the one setting
``LABVIEW_DAYLIGHT_SAVING`` and the two functions every local-time conversion calls.
"""

from __future__ import annotations

import inspect
import re

from . import labview_local_time

LOCAL_TIME_MODULE_NAME = "local_time"
LOCAL_OFFSET_NAME = "local_offset"
LOCAL_SECONDS_NAME = "local_seconds"

# A relative import of the package's ``local_time`` module.
_LOCAL_TIME_IMPORT = re.compile(rf"^from \.+{LOCAL_TIME_MODULE_NAME} import ")


def imports_local_time(statement: str) -> bool:
    """Whether ``statement`` imports from the package's ``local_time`` module."""
    return _LOCAL_TIME_IMPORT.match(statement) is not None


def local_time_import(prefix: str, names: str) -> str:
    """``from <prefix>local_time import <names>``."""
    return f"from {prefix}{LOCAL_TIME_MODULE_NAME} import {names}"


LOCAL_TIME_MODULE_SOURCE = inspect.getsource(labview_local_time)
