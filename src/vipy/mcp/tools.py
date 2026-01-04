"""VI analysis tools for MCP server."""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

from ..analysis import analyze_vi as core_analyze_vi
from .schemas import (
    CodeGenResult,
    ControlSchema,
    IndicatorSchema,
    VIAnalysisResult,
)


def analyze_vi(
    vi_path: str, search_paths: list[str] | None = None, expand_subvis: bool = True
) -> VIAnalysisResult:
    """Analyze a VI and return structured data.

    Calls the core analysis.analyze_vi() function and converts to pydantic model.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        search_paths: Optional list of search paths for dependencies
        expand_subvis: If True, recursively load all SubVI dependencies
                      (slower but complete). If False, only load this VI
                      (faster but limited cross-references).

    Returns:
        VIAnalysisResult with complete VI structure
    """
    # Call core analysis function (returns VIAnalysis dataclass)
    result = core_analyze_vi(
        vi_path=vi_path,
        search_paths=search_paths,
        expand_subvis=expand_subvis,
    )

    # Convert to pydantic schema for MCP protocol
    return VIAnalysisResult(
        vi_name=result.vi_name,
        summary=result.summary,
        controls=[ControlSchema(**asdict(c)) for c in result.controls],
        indicators=[IndicatorSchema(**asdict(i)) for i in result.indicators],
        graph={
            "inputs": [asdict(inp) for inp in result.graph.inputs],
            "outputs": [asdict(out) for out in result.graph.outputs],
            "operations": [asdict(op) for op in result.graph.operations],
            "constants": [asdict(c) for c in result.graph.constants],
            "data_flow": [asdict(w) for w in result.graph.data_flow],
        },
        dependencies=result.dependencies,
        execution_order=result.execution_order,
    )


def generate_documents(
    library_path: str,
    output_dir: str,
    search_paths: list[str] | None = None,
    expand_subvis: bool = True,
) -> str:
    """Generate HTML documentation for a LabVIEW library, class, directory,
    or single VI.

    This is a thin wrapper that calls the deterministic scripts/generate_docs.py script.

    Args:
        library_path: Path to .lvlib, .lvclass, directory, or .vi file
        output_dir: Output directory for HTML files
        search_paths: Optional list of search paths for dependencies
        expand_subvis: If True, load all SubVI dependencies for complete
                      cross-references (slower). If False, only load VIs in
                      the library/directory (faster).

    Returns:
        Summary message with statistics
    """
    import subprocess

    # Build command
    script_path = Path(__file__).parent.parent.parent / "scripts" / "generate_docs.py"
    cmd = [sys.executable, str(script_path), library_path, output_dir]

    if search_paths:
        for sp in search_paths:
            cmd.extend(["--search-path", sp])

    if not expand_subvis:
        cmd.append("--no-expand")

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
) -> CodeGenResult:
    """Generate Python code from a LabVIEW VI using AST-based translation.

    This is a thin wrapper that calls the deterministic
    scripts/generate_python.py script.

    Args:
        vi_path: Path to VI file (.vi) or block diagram XML (*_BDHb.xml)
        output_dir: Output directory for generated Python files
        search_paths: Optional list of search paths for dependencies
        include_code: If True, include generated code in response (default: False)

    Returns:
        CodeGenResult with generated files, errors, and review needs.
    """
    import json
    import subprocess
    from pathlib import Path

    # Build command
    script_path = Path(__file__).parent.parent.parent / "scripts" / "generate_python.py"
    cmd = [
        sys.executable,
        str(script_path),
        vi_path,
        output_dir,
    ]

    if search_paths:
        for sp in search_paths:
            cmd.extend(["--search-path", sp])

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
