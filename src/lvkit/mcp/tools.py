"""VI analysis tools for MCP server."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..lv_detect import detect_labview
from .schemas import CodeGenResult


def _apply_auto_vilib(
    cmd: list[str],
    vilib_root: str | None,
    userlib_root: str | None,
    auto_vilib: bool,
) -> None:
    """Append --vilib/--userlib to ``cmd``, auto-detecting when unset.

    Explicit ``vilib_root`` always wins. When it is unset and ``auto_vilib`` is
    on, best-effort detect a local LabVIEW and use its vi.lib/user.lib. Fully
    non-fatal: a detection failure leaves ``cmd`` unchanged.
    """
    if vilib_root:
        cmd.extend(["--vilib", vilib_root])
    elif auto_vilib:
        try:
            detected = detect_labview()
        except Exception:
            detected = None
        if detected is not None:
            cmd.extend(["--vilib", str(detected.vilib_root)])
            if userlib_root is None and detected.userlib_root is not None:
                userlib_root = str(detected.userlib_root)

    if userlib_root:
        cmd.extend(["--userlib", userlib_root])


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    load_mode: str = "full",
    vilib_root: str | None = None,
    userlib_root: str | None = None,
    auto_vilib: bool = True,
) -> str:
    """Generate HTML documentation for a LabVIEW library, class, directory,
    or single VI.

    This is a thin wrapper that calls the deterministic scripts/generate_docs.py script.

    Args:
        library_path: Path to .lvlib, .lvclass, directory, or .vi file
        output_dir: Output directory for HTML files
        search_paths: Optional list of search paths for dependencies
        load_mode: how deep to load dependencies (see LoadMode). ``"full"``
                   (default) loads the whole SubVI/class-method tree for complete
                   cross-references; ``"minimal"`` loads each VI + its direct
                   SubVIs' connector panes + referenced-type fields (faithful
                   render, far cheaper); ``"none"`` loads each VI alone.

    Returns:
        Summary message with statistics
    """
    # Build command
    script_path = (
        Path(__file__).parent.parent.parent.parent / "scripts" / "generate_docs.py"
    )
    cmd = [sys.executable, str(script_path), library_path, output_dir]

    if search_paths:
        for sp in search_paths:
            cmd.extend(["--search-path", sp])

    cmd.extend(["--load-mode", load_mode])

    _apply_auto_vilib(cmd, vilib_root, userlib_root, auto_vilib)

    # Run the deterministic script
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = result.stderr or result.stdout
        raise RuntimeError(f"Documentation generation failed: {error_msg}")

    # Return the summary output from the script
    return result.stdout





# ========== Python Code Generation ==========


def generate_python(
    vi_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    include_code: bool = False,
    soft_unresolved: bool = False,
    vilib_root: str | None = None,
    userlib_root: str | None = None,
    auto_vilib: bool = True,
) -> CodeGenResult:
    """Generate Python code from a LabVIEW VI using AST-based translation.

    This is a thin wrapper that calls the deterministic
    scripts/generate_python.py script.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        output_dir: Output directory for generated Python files
        search_paths: Optional list of search paths for dependencies
        include_code: If True, include generated code in response (default: False)
        soft_unresolved: If True, unknown primitives / vi.lib VIs are emitted
            as inline raise statements instead of failing the build.

    Returns:
        CodeGenResult with generated files, errors, and review needs.
    """
    # Build command
    script_path = (
        Path(__file__).parent.parent.parent.parent / "scripts" / "generate_python.py"
    )
    cmd = [
        sys.executable,
        str(script_path),
        vi_path,
        output_dir,
    ]

    if search_paths:
        for sp in search_paths:
            cmd.extend(["--search-path", sp])

    if soft_unresolved:
        cmd.append("--placeholder-on-unresolved")

    _apply_auto_vilib(cmd, vilib_root, userlib_root, auto_vilib)

    # Run the deterministic script
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return CodeGenResult(
            success=False,
            output_dir=output_dir,
            package_name="",
            files=[],
            summary=f"Code generation failed:\n{result.stderr}",
            errors=[result.stderr],
            warnings=[],
            total_vis=0,
            successful=0,
            failed=1,
            needs_review=[],
        )

    # Parse JSON output from script
    try:
        output_data = json.loads(result.stdout)
        return CodeGenResult(**output_data)
    except json.JSONDecodeError:
        return CodeGenResult(
            success=False,
            output_dir=output_dir,
            package_name="",
            files=[],
            summary=f"Failed to parse script output:\n{result.stdout}",
            errors=["JSON parse error"],
            warnings=[],
            total_vis=0,
            successful=0,
            failed=1,
            needs_review=[],
        )
