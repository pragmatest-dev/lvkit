"""The module a generated package holds for errors a LabVIEW diagram builds.

Where a VI raises an error itself (Error Cluster From Error Code, a Bundle that
sets an error's status), the generated code raises ``LabVIEWError``; where a VI
handles one, it catches it. The class is written once into the package root as
``errors.py`` and imported from there, so a generated package imports nothing
from lvkit.
"""

from __future__ import annotations

ERRORS_MODULE_NAME = "errors"

ERRORS_MODULE_SOURCE = '''"""The error a VI raises where its diagram built an error."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LabVIEWError(Exception):
    """An error with a LabVIEW error code, a source and a message."""

    code: int = 0
    source: str = ""
    message: str = ""

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
