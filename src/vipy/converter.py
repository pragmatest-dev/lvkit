"""Main VI to Python converter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .llm import LLMConfig, generate_code
from .summarizer import create_llm_prompt, summarize_vi


def extract_vi(vi_path: Path | str, output_dir: Path | str | None = None) -> tuple[Path, Path]:
    """Extract a VI file to XML using pylabview.

    Args:
        vi_path: Path to the .vi file
        output_dir: Directory for output files (default: same as VI)

    Returns:
        Tuple of (main_xml_path, block_diagram_xml_path)

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

    if not main_xml.exists():
        raise RuntimeError(f"Main XML not found: {main_xml}")
    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return main_xml, bd_xml


def convert_vi(
    vi_path: Path | str,
    output_path: Path | str | None = None,
    llm_config: LLMConfig | None = None,
) -> str:
    """Convert a VI file to Python code.

    Args:
        vi_path: Path to the .vi file
        output_path: Optional path to write the Python code
        llm_config: Optional LLM configuration

    Returns:
        Generated Python code
    """
    vi_path = Path(vi_path)

    with TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # Extract VI to XML
        main_xml, bd_xml = extract_vi(vi_path, tmp_path)

        # Generate summary
        summary = summarize_vi(bd_xml, main_xml)

        # Create prompt
        prompt = create_llm_prompt(summary)

        # Generate code
        code = generate_code(prompt, llm_config)

    # Write output if requested
    if output_path:
        output_path = Path(output_path)
        output_path.write_text(code)

    return code


def convert_xml(
    bd_xml_path: Path | str,
    main_xml_path: Path | str | None = None,
    output_path: Path | str | None = None,
    llm_config: LLMConfig | None = None,
) -> str:
    """Convert pre-extracted VI XML to Python code.

    Use this when you've already run pylabview extraction.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional)
        output_path: Optional path to write the Python code
        llm_config: Optional LLM configuration

    Returns:
        Generated Python code
    """
    # Generate summary
    summary = summarize_vi(bd_xml_path, main_xml_path)

    # Create prompt
    prompt = create_llm_prompt(summary)

    # Generate code
    code = generate_code(prompt, llm_config)

    # Write output if requested
    if output_path:
        Path(output_path).write_text(code)

    return code
