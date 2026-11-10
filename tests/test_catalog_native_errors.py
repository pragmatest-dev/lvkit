"""The catalog's ``errors`` field: the failures of a node's Python operation that
are its error out (``codegen/native_failure.py``), each with a code from NI's
public error-code list."""

from __future__ import annotations

import builtins
import importlib
import json
import sys
from pathlib import Path

from lvkit.codegen.error_codes import describe_error_code
from lvkit.codegen.native_failure import GENERIC_ERROR_CODE
from lvkit.primitive_resolver import NativeError

DATA = Path(__file__).parent.parent / "src" / "lvkit" / "data"

FILE_PATH_OPS = [("FileNotFoundError", 7), ("PermissionError", 8), ("OSError", 6)]
FILE_REFNUM_OPS = [("OSError", 6)]
COPY_MOVE = [
    ("FileNotFoundError", 7),
    ("PermissionError", 8),
    ("FileExistsError", 10),
    ("OSError", 6),
]
RELEASED_QUEUE = [("ShutDown", 1122)]
DELETE = [
    ("EmptyOrRelativePathError", 1430),
    ("InvalidPathError", 1),
    *COPY_MOVE[:2],
    ("OSError", 6),
]

CREATE_FOLDER = [
    ("EmptyOrRelativePathError", 1430),
    ("InvalidPathError", 1),
    ("FileNotFoundError", 7),
    ("PermissionError", 8),
    ("FileExistsError", 10),
    ("OSError", 6),
]

WRITE_TO_TEXT_FILE = [
    ("EmptyOrRelativePathError", 1430),
    ("InvalidPathError", 1),
    *FILE_PATH_OPS,
]

PRIMITIVES = {
    "file-io:Create Directory Recursive": [
        ("PermissionError", 8),
        ("FileExistsError", 10),
        ("OSError", 6),
    ],
    "8050:Open/Create/Replace File": FILE_PATH_OPS,
    "8051:Read from Binary File": FILE_REFNUM_OPS,
    "8052:Close File": FILE_REFNUM_OPS,
    "8053:Copy": COPY_MOVE,
    "8062:Get File Position": FILE_REFNUM_OPS,
    "8063:Get File Size": FILE_REFNUM_OPS,
    "8065:Get Permissions": FILE_PATH_OPS,
    "8068:Move": COPY_MOVE,
    "8070:Read from Text File": FILE_PATH_OPS,
    "8073:Set File Position": FILE_REFNUM_OPS,
    "8078:Write to Binary File": FILE_REFNUM_OPS,
    "8080:Write to Text File": WRITE_TO_TEXT_FILE,
    "8082:File/Directory Info": FILE_PATH_OPS,
    "8003:Variant To Data": [("TypeError", 91)],
    "1165:Unflatten From String": [("error", 116)],
    "8202:Flattened String To Variant": [("error", 116)],
    "8083:List Folder": FILE_PATH_OPS,
    "9111:Enqueue Element": RELEASED_QUEUE,
    "9112:Preview Queue Element": RELEASED_QUEUE,
    "9113:Dequeue Element": RELEASED_QUEUE,
    "9114:Flush Queue": [("ShutDown", None)],
    "9129:Enqueue Element At Opposite End": RELEASED_QUEUE,
    "9105:Wait on Notification": [("ReferenceError", 1122)],
    "3914:Search and Replace String": [
        ("NullCharacterError", -4702),
        ("error", None),
    ],
    "8055:Create Folder": CREATE_FOLDER,
    "8056:Delete": DELETE,
    "1417:Delete": DELETE,
    "8016:To More Specific Class": [("TypeError", 1448)],
    "8204:Set Variant Attribute": [("EmptyAttributeNameError", 1)],
    "2010:TDMS Write": FILE_REFNUM_OPS,
    "24110:TDMS Write": FILE_REFNUM_OPS,
    "2012:TDMS Open": COPY_MOVE,
    "24112:TDMS Open": COPY_MOVE,
    "2013:TDMS Close": FILE_REFNUM_OPS,
    "24113:TDMS Close": FILE_REFNUM_OPS,
    "2014:TDMS Set Properties": FILE_REFNUM_OPS,
    "24114:TDMS Set Properties": FILE_REFNUM_OPS,
}


def _declared() -> dict[str, list[NativeError]]:
    """``<primResID>:<name>`` / ``<file>:<name>`` of every catalog entry with
    ``errors``, and its parsed failures."""
    found: dict[str, list[NativeError]] = {}
    for path in sorted((DATA / "primitives").glob("*.json")):
        for prim_id, entry in (
            json.loads(path.read_text()).get("primitives") or {}
        ).items():
            if isinstance(entry, dict) and "errors" in entry:
                found[f"{prim_id}:{entry['name']}"] = [
                    NativeError.model_validate(e) for e in entry["errors"]
                ]
    for path in sorted((DATA / "vilib").glob("*.json")):
        data = json.loads(path.read_text())
        for entry in data.get("entries", []) if isinstance(data, dict) else []:
            if "errors" in entry:
                found[f"{path.stem}:{entry['name']}"] = [
                    NativeError.model_validate(e) for e in entry["errors"]
                ]
    return found


def _exception_class(failure: NativeError) -> type[BaseException] | None:
    """The class ``failure`` names; ``None`` when this Python is older than the
    version that has it."""
    if failure.since_python is not None and sys.version_info < tuple(
        int(part) for part in failure.since_python.split(".")
    ):
        return None
    if failure.base is not None:
        # A class of the operation's own, defined in the generated module.
        cls = type(failure.exception, (getattr(builtins, failure.base),), {})
    elif failure.module is None:
        cls = getattr(builtins, failure.exception)
    else:
        cls = getattr(importlib.import_module(failure.module), failure.exception)
    assert isinstance(cls, type) and issubclass(cls, Exception), failure
    return cls


def test_the_entries_that_declare_failures_are_the_pinned_ones() -> None:
    declared = {
        key: [(f.exception, f.code) for f in failures]
        for key, failures in _declared().items()
    }
    assert declared == PRIMITIVES


def test_every_declared_failure_is_an_exception_caught_in_a_reachable_order() -> None:
    """Each exception names a Python exception class, and no handler catches a
    class an earlier one already catches (it would never run)."""
    for key, failures in _declared().items():
        classes = [c for f in failures if (c := _exception_class(f)) is not None]
        for i, later in enumerate(classes):
            for earlier in classes[:i]:
                assert not issubclass(later, earlier), (key, later, earlier)


def test_every_declared_code_has_ni_s_description() -> None:
    codes = {f.code for failures in _declared().values() for f in failures}
    for code in {GENERIC_ERROR_CODE, *(c for c in codes if c is not None)}:
        assert describe_error_code(code), code
