"""Main VI to Python converter."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .blockdiagram import create_llm_prompt, summarize_vi
from .cypher import from_blockdiagram as summarize_vi_cypher
from .frontpanel import generate_nicegui_code, parse_front_panel, summarize_front_panel
from .llm import LLMConfig, generate_code


@dataclass
class ExtractedVI:
    """Paths to extracted VI XML files."""
    main_xml: Path
    bd_xml: Path
    fp_xml: Path | None = None


def extract_vi(vi_path: Path | str, output_dir: Path | str | None = None) -> ExtractedVI:
    """Extract a VI file to XML using pylabview.

    Args:
        vi_path: Path to the .vi file
        output_dir: Directory for output files (default: same as VI)

    Returns:
        ExtractedVI with paths to main, block diagram, and front panel XML

    Raises:
        RuntimeError: If extraction fails
    """
    vi_path = Path(vi_path)
    if output_dir is None:
        output_dir = vi_path.parent
    else:
        output_dir = Path(output_dir)

    # Run pylabview to extract (use same Python as current process)
    result = subprocess.run(
        [sys.executable, "-m", "pylabview.readRSRC", "-i", str(vi_path), "-x"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pylabview extraction failed: {result.stderr}")

    # Find the generated XML files
    vi_stem = vi_path.stem
    main_xml = output_dir / f"{vi_stem}.xml"
    bd_xml = output_dir / f"{vi_stem}_BDHb.xml"
    fp_xml = output_dir / f"{vi_stem}_FPHb.xml"

    if not main_xml.exists():
        raise RuntimeError(f"Main XML not found: {main_xml}")
    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return ExtractedVI(
        main_xml=main_xml,
        bd_xml=bd_xml,
        fp_xml=fp_xml if fp_xml.exists() else None,
    )


@dataclass
class ConvertedVI:
    """Result of VI conversion."""
    backend_code: str
    frontend_code: str | None = None


def convert_vi(
    vi_path: Path | str,
    output_path: Path | str | None = None,
    llm_config: LLMConfig | None = None,
    mode: str = "script",
) -> str | ConvertedVI:
    """Convert a VI file to Python code.

    Args:
        vi_path: Path to the .vi file
        output_path: Optional path to write the Python code
        llm_config: Optional LLM configuration
        mode: "script" for single file, "gui" for frontend/backend split

    Returns:
        Generated Python code (script mode) or ConvertedVI (gui mode)
    """
    vi_path = Path(vi_path)

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Extract VI to XML
        extracted = extract_vi(vi_path, tmp_path)

        # Generate backend code
        summary = summarize_vi(extracted.bd_xml, extracted.main_xml)
        prompt = create_llm_prompt(summary, mode=mode)
        backend_code = generate_code(prompt, llm_config)

        if mode == "gui" and extracted.fp_xml:
            # Generate frontend code from front panel
            fp = parse_front_panel(extracted.fp_xml)
            frontend_code = generate_nicegui_code(
                fp,
                vi_name=vi_path.stem,
                backend_function=_extract_function_name(backend_code),
            )

            result = ConvertedVI(
                backend_code=backend_code,
                frontend_code=frontend_code,
            )

            # Write output files if requested
            if output_path:
                output_path = Path(output_path)
                output_dir = output_path.parent
                stem = output_path.stem

                backend_path = output_dir / f"{stem}_backend.py"
                frontend_path = output_dir / f"{stem}_frontend.py"

                backend_path.write_text(backend_code)
                frontend_path.write_text(frontend_code)

            return result

    # Script mode - single file output
    if output_path:
        Path(output_path).write_text(backend_code)

    return backend_code


def convert_xml(
    bd_xml_path: Path | str,
    main_xml_path: Path | str | None = None,
    fp_xml_path: Path | str | None = None,
    output_path: Path | str | None = None,
    llm_config: LLMConfig | None = None,
    mode: str = "script",
    summary_format: str = "text",
) -> str | ConvertedVI:
    """Convert pre-extracted VI XML to Python code.

    Use this when you've already run pylabview extraction.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional)
        fp_xml_path: Path to the front panel XML (*_FPHb.xml, optional)
        output_path: Optional path to write the Python code
        llm_config: Optional LLM configuration
        mode: "script" for single file, "gui" for frontend/backend split
        summary_format: "text" (default) or "cypher" for Neo4j graph format

    Returns:
        Generated Python code (script mode) or ConvertedVI (gui mode)
    """
    bd_xml_path = Path(bd_xml_path)

    # Generate backend summary and code
    if summary_format == "cypher":
        summary = summarize_vi_cypher(bd_xml_path, main_xml_path)
    else:
        summary = summarize_vi(bd_xml_path, main_xml_path)

    # Add front panel info to summary if available and in gui mode
    if mode == "gui" and fp_xml_path:
        fp = parse_front_panel(fp_xml_path)
        fp_summary = summarize_front_panel(fp)
        summary = f"{summary}\n\n{fp_summary}"

    prompt = create_llm_prompt(summary, mode=mode, summary_format=summary_format)
    backend_code = generate_code(prompt, llm_config)

    if mode == "gui" and fp_xml_path:
        # Generate frontend code
        fp = parse_front_panel(fp_xml_path)
        vi_name = bd_xml_path.stem.replace("_BDHb", "")
        frontend_code = generate_nicegui_code(
            fp,
            vi_name=vi_name,
            backend_function=_extract_function_name(backend_code),
        )

        result = ConvertedVI(
            backend_code=backend_code,
            frontend_code=frontend_code,
        )

        # Write output files if requested
        if output_path:
            output_path = Path(output_path)
            output_dir = output_path.parent
            stem = output_path.stem

            backend_path = output_dir / f"{stem}_backend.py"
            frontend_path = output_dir / f"{stem}_frontend.py"

            backend_path.write_text(backend_code)
            frontend_path.write_text(frontend_code)

        return result

    # Script mode
    if output_path:
        Path(output_path).write_text(backend_code)

    return backend_code


def _extract_function_name(code: str) -> str:
    """Extract the main function name from generated code."""
    import re
    match = re.search(r"def\s+(\w+)\s*\(", code)
    if match:
        return match.group(1)
    return "process"
