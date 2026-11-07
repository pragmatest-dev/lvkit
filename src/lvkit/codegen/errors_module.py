"""The module a generated package holds for the value of a LabVIEW error wire.

An error wire is ``None`` when no error occurred, else a ``LabVIEWError``: an
error when its ``status`` is True, a warning when it is False. The class is
written once into the package root as ``errors.py`` and imported from there, so
a generated package imports nothing from lvkit.
"""

from __future__ import annotations

ERRORS_MODULE_NAME = "errors"

ERRORS_MODULE_SOURCE = '''"""The value of a LabVIEW error wire."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LabVIEWError(Exception):
    """A LabVIEW error cluster that holds an error or a warning: ``status`` is
    True when an error occurred, False for a warning; ``code``, ``source`` and
    ``message`` say which. No error at all is ``None``, not a LabVIEWError."""

    code: int = 0
    source: str = ""
    message: str = ""
    status: bool = True

    def __post_init__(self) -> None:
        super().__init__(self.code, self.source, self.message)

    def __str__(self) -> str:
        if self.message:
            return f"Error {self.code}: {self.message} (source: {self.source})"
        if self.source:
            return f"Error {self.code} at {self.source}"
        return f"Error {self.code}"
'''

LABVIEW_ERROR_NAME = "LabVIEWError"
