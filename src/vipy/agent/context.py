"""VISignature dataclass for SubVI imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VISignature:
    """Signature info for a VI (for SubVI imports)."""

    name: str
    module_name: str
    function_name: str
    signature: str  # e.g., "def calculate(a: float) -> float"
    import_statement: str  # e.g., "from .calculate import calculate"
