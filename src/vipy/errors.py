"""LabVIEW error types for Python conversion."""

from __future__ import annotations


class LabVIEWError(Exception):
    """LabVIEW error cluster represented as Python exception.

    In LabVIEW, errors propagate through error cluster wires.
    In Python, we use exceptions instead.
    """

    def __init__(self, code: int, message: str = "", source: str = ""):
        self.code = code
        self.message = message
        self.source = source
        super().__init__(f"LabVIEW Error {code}: {message}" if message else f"LabVIEW Error {code}")

    def to_cluster(self) -> dict:
        """Convert to LabVIEW-style error cluster dict."""
        return {
            "status": True,
            "code": self.code,
            "source": self.source or self.message,
        }
