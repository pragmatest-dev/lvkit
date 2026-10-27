"""LabVIEW error code descriptions, read at generation time.

A generated module never carries this table: where a VI builds an error from a
constant code, the description is written into the module as a string.

Data extracted from the LabVIEW Programming Reference Manual.
"""

from __future__ import annotations

import json

from .._data import data_dir as _bundled_data_dir

_ERROR_CODES: dict[str, str] | None = None


def _load_codes() -> dict[str, str]:
    """Load error codes from JSON data file (lazy, one-time).

    Gracefully degrades to an empty dict if the data file is missing
    (matches the rest of the resolver layer — see also
    PrimitiveResolver._load_codegen and VILibResolver._load_vilib_data).
    """
    global _ERROR_CODES
    if _ERROR_CODES is None:
        data_path = _bundled_data_dir() / "labview_error_codes.json"
        if data_path.exists():
            _ERROR_CODES = json.loads(data_path.read_text(encoding="utf-8"))
        else:
            _ERROR_CODES = {}
    return _ERROR_CODES or {}


def describe_error_code(code: int) -> str | None:
    """NI's description of an error code, or None when the code is not listed."""
    return _load_codes().get(str(code)) or None
