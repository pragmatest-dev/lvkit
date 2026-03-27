"""LabVIEW error handling types.

Provides a LabVIEWError exception class that preserves LabVIEW error cluster
semantics (code, source, message) while integrating with Python's exception model.

Error Handling Model:
- LabVIEW uses error clusters: {status: bool, code: int, source: str}
- Error wires guarantee execution order (data dependency)
- Error-checking case structures skip work if error is present
- Merge Errors combines errors at join points (first error wins)
- Clear Errors intentionally suppresses errors

Python Translation:
- Error clusters become LabVIEWError exceptions
- Error-checking case structures become implicit (exception propagation)
- Clear Errors becomes try/except with suppression
- Merge Errors becomes held error model for parallel branches
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LabVIEWError(Exception):
    """Exception representing a LabVIEW error cluster.

    Preserves LabVIEW error semantics:
    - code: Numeric error code (0 = no error, negative = error, positive = warning)
    - source: Call chain string showing where error occurred
    - message: Human-readable error description

    Usage:
        # Raising an error
        raise LabVIEWError(code=42, source="MyVI.vi", message="Something went wrong")

        # Creating from error cluster dict
        error = LabVIEWError.from_cluster({"status": True, "code": 42, "source": "VI"})

        # Converting back to cluster
        cluster = error.to_cluster()
    """

    code: int = 0
    source: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        """Initialize Exception base with formatted message."""
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the error for exception display."""
        if self.message:
            return f"Error {self.code}: {self.message} (source: {self.source})"
        elif self.source:
            return f"Error {self.code} at {self.source}"
        else:
            return f"Error {self.code}"

    def __str__(self) -> str:
        return self._format_message()

    def __repr__(self) -> str:
        return (
            f"LabVIEWError(code={self.code!r}, source={self.source!r}, "
            f"message={self.message!r})"
        )

    @classmethod
    def from_cluster(cls, cluster: dict[str, object] | None) -> LabVIEWError | None:
        """Create LabVIEWError from a LabVIEW error cluster dict.

        Args:
            cluster: Error cluster dict with keys 'status', 'code', 'source'
                    or None for no error

        Returns:
            LabVIEWError if cluster indicates error (status=True), else None
        """
        if cluster is None:
            return None

        status = cluster.get("status", False)
        if not status:
            return None  # No error

        raw_code = cluster.get("code", 0)
        code = raw_code if isinstance(raw_code, int) else int(str(raw_code))
        return cls(
            code=code,
            source=str(cluster.get("source", "")),
            message=str(cluster.get("message", "")),
        )

    def to_cluster(self) -> dict[str, bool | int | str]:
        """Convert back to LabVIEW error cluster dict.

        Returns:
            Dict with 'status', 'code', 'source' keys
        """
        return {
            "status": True,
            "code": self.code,
            "source": self.source,
        }

    @property
    def is_error(self) -> bool:
        """True if this represents an error (negative code)."""
        return self.code < 0

    @property
    def is_warning(self) -> bool:
        """True if this represents a warning (positive code)."""
        return self.code > 0


def no_error_cluster() -> dict[str, bool | int | str]:
    """Return a 'no error' cluster dict.

    This is the default value for unwired error inputs.
    """
    return {"status": False, "code": 0, "source": ""}


def merge_errors(
    error1: LabVIEWError | None,
    error2: LabVIEWError | None,
) -> LabVIEWError | None:
    """Merge two errors, returning the first one that is not None.

    This implements LabVIEW's "Merge Errors" primitive semantics:
    - If error1 exists, return error1
    - Else if error2 exists, return error2
    - Else return None

    Args:
        error1: First error (takes priority)
        error2: Second error

    Returns:
        The first non-None error, or None if both are None
    """
    return error1 if error1 is not None else error2
