"""LabVIEW error code descriptions.

Provides lookup from numeric error code to human-readable description,
mirroring LabVIEW's built-in error code database. Used by Error Cluster
From Error Code and other error-creating operations.

Data extracted from the LabVIEW Programming Reference Manual.
"""

from __future__ import annotations

import json

from ._data import data_dir as _bundled_data_dir

_ERROR_CODES: dict[str, str] | None = None


def _load_codes() -> dict[str, str]:
    """Load error codes from JSON data file (lazy, one-time).

    Gracefully degrades to an empty dict if the data file is missing
    (matches the rest of the resolver layer — see also
    PrimitiveResolver._load_codegen and VILibResolver._load_vilib_data).
    A missing file means error descriptions are unavailable but the
    rest of lvkit keeps working.
    """
    global _ERROR_CODES
    if _ERROR_CODES is None:
        data_path = _bundled_data_dir() / "labview_error_codes.json"
        if data_path.exists():
            _ERROR_CODES = json.loads(data_path.read_text())
        else:
            _ERROR_CODES = {}
    return _ERROR_CODES or {}


def get_error_description(code: int) -> str:
    """Look up LabVIEW error description by code.

    Returns the description string if found, otherwise a generic
    fallback message.
    """
    codes = _load_codes()
    desc = codes.get(str(code))
    if desc:
        return desc
    return f"LabVIEW error {code}"
