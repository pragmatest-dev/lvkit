"""VI XML extraction using pylabview."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def extract_vi_xml(
    vi_path: Path | str,
    output_dir: Path | None = None,
) -> tuple[Path, Path | None, Path | None]:
    """Extract a VI file to XML using pylabview.

    Args:
        vi_path: Path to the .vi file
        output_dir: Directory for output files (default: same as VI)

    Returns:
        Tuple of (bd_xml_path, fp_xml_path, main_xml_path)
        fp_xml and main_xml may be None if not generated

    Raises:
        RuntimeError: If extraction fails
    """
    vi_path = Path(vi_path).resolve()

    if output_dir is None:
        output_dir = vi_path.parent

    result = subprocess.run(
        [sys.executable, "-m", "pylabview.readRSRC", "-i", str(vi_path), "-x"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pylabview extraction failed: {result.stderr}")

    vi_stem = vi_path.stem
    bd_xml = output_dir / f"{vi_stem}_BDHb.xml"
    fp_xml = output_dir / f"{vi_stem}_FPHb.xml"
    main_xml = output_dir / f"{vi_stem}.xml"

    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return (
        bd_xml,
        fp_xml if fp_xml.exists() else None,
        main_xml if main_xml.exists() else None,
    )
