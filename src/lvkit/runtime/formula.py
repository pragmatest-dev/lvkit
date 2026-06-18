"""Load and call a compiled Formula Node from generated Python.

A lvkit-generated module that contains a Formula Node imports this and calls
``load(...)`` once at import time to obtain a marshaling callable. The hybrid
artifact strategy:

  1. If a prebuilt ``<basename>.<platform>.so`` sits next to the module, load
     it directly — no compilation at runtime.
  2. Otherwise compile the shipped ``<basename>.c`` once into a per-user cache
     (keyed by content hash) and load that. This only happens the first time
     the module runs on a platform it wasn't generated for.

Marshaling is driven entirely by the ordered ``params`` spec emitted by the
transpiler, so this module has no per-VI knowledge.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from ..formula.compile import compile_shared, platform_tag

# One param: (c_param_name, ctype_name, role, variable_name). role is one of
# scalar_in | scalar_out | array_in | array_inout | array_out.
ParamSpec = tuple[str, str, str, str]


def _cache_dir() -> Path:
    base = os.environ.get("LVKIT_CACHE")
    root = Path(base) if base else Path.home() / ".cache" / "lvkit"
    return root / "formula"


def _resolve_so(directory: Path, basename: str) -> Path:
    """Find a loadable .so: prefer a platform-matched prebuilt one, else
    compile the shipped .c into the per-user cache (once, by content hash)."""
    prebuilt = directory / f"{basename}.{platform_tag()}.so"
    if prebuilt.exists():
        return prebuilt
    c_path = directory / f"{basename}.c"
    if not c_path.exists():
        raise FileNotFoundError(
            f"no prebuilt .so for this platform and no source to compile: "
            f"expected {prebuilt.name} or {c_path.name} in {directory}"
        )
    digest = hashlib.sha256(c_path.read_bytes()).hexdigest()[:16]
    cached = _cache_dir() / f"{basename}.{platform_tag()}.{digest}.so"
    if not cached.exists():
        compile_shared(c_path, cached)
    return cached


def load(
    directory: str | Path,
    basename: str,
    func_name: str,
    params: Sequence[ParamSpec],
) -> Callable[..., dict]:
    """Return a callable wrapping the compiled Formula Node.

    The callable takes keyword arguments (one per input variable) and returns
    a dict of output variable name -> value (scalars as Python numbers,
    arrays as lists).
    """
    directory = Path(directory)
    lib = ctypes.CDLL(str(_resolve_so(directory, basename)))
    fn = getattr(lib, func_name)

    argtypes: list = []
    for _pname, ctype_name, role, _var in params:
        ct = getattr(ctypes, ctype_name)
        argtypes.append(ct if role == "scalar_in" else ctypes.POINTER(ct))
    fn.argtypes = argtypes
    fn.restype = None

    def call(**inputs) -> dict:
        args: list = []
        scalar_outs: list[tuple[str, ctypes._SimpleCData]] = []
        array_outs: list[tuple[str, ctypes.Array]] = []
        for _pname, ctype_name, role, var in params:
            ct = getattr(ctypes, ctype_name)
            if role == "scalar_in":
                args.append(inputs[var])
            elif role == "scalar_out":
                obj = ct()
                args.append(ctypes.byref(obj))
                scalar_outs.append((var, obj))
            elif role in ("array_in", "array_inout"):
                seq = inputs[var]
                buf = (ct * len(seq))(*seq)
                args.append(buf)
                if role == "array_inout":
                    array_outs.append((var, buf))
            elif role == "array_out":
                raise ValueError(
                    f"output-only array {var!r} has no length source; "
                    "wire it as in/out or pass its length"
                )
            else:
                raise ValueError(f"unknown param role {role!r}")

        fn(*args)

        out: dict = {}
        for var, obj in scalar_outs:
            out[var] = obj.value
        for var, buf in array_outs:
            out[var] = list(buf)
        return out

    return call
