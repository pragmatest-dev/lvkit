"""Compile transpiled Formula Node C to a shared library.

Used both at generation time (to emit a platform-tagged .so next to the .py)
and at runtime (to compile the shipped .c on a platform with no matching
prebuilt binary). Fails loud with the compiler's stderr.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


class FormulaCompileError(RuntimeError):
    """Raised when the C compiler fails or is unavailable."""


def platform_tag() -> str:
    """Stable os-arch tag for naming a platform-specific .so."""
    return f"{platform.system()}-{platform.machine()}".lower()


def _compiler() -> str:
    import shutil
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        raise FormulaCompileError(
            "no C compiler found (cc/gcc/clang) — required to build Formula "
            "Node code. Install a C toolchain (e.g. build-essential)."
        )
    return cc


def compile_shared(c_path: Path, so_path: Path) -> Path:
    """Compile ``c_path`` to a shared library at ``so_path``.

    Compiles to a temp file and atomically renames into place so concurrent
    builds of the same artifact don't see a half-written .so.
    """
    c_path = Path(c_path)
    so_path = Path(so_path)
    so_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = so_path.with_suffix(so_path.suffix + f".tmp{__import__('os').getpid()}")
    cmd = [
        _compiler(), "-shared", "-fPIC", "-O2",
        str(c_path), "-o", str(tmp), "-lm",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise FormulaCompileError(
            f"failed to compile {c_path.name}:\n{result.stderr.strip()}"
        )
    tmp.replace(so_path)
    return so_path
