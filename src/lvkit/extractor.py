"""VI XML extraction using pylabview."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

_CACHE_ROOT = Path(tempfile.gettempdir()) / "lvkit" / "extract"


def _default_cache_dir(vi_path: Path) -> Path:
    """Return a stable per-VI cache directory under the OS temp dir.

    The directory name is ``<stem>_<hash12>`` where ``<hash12>`` is the
    first 12 hex chars of SHA-256 over the resolved absolute path. This
    keeps two VIs with the same stem in different folders from colliding,
    while staying short enough to skim in ``ls`` output.
    """
    digest = hashlib.sha256(str(vi_path).encode("utf-8")).hexdigest()[:12]
    return _CACHE_ROOT / f"{vi_path.stem}_{digest}"


def extract_vi_xml(
    vi_path: Path | str,
    output_dir: Path | None = None,
    force: bool = False,
) -> tuple[Path, Path | None, Path | None]:
    """Extract a VI file to XML using pylabview.

    Uses caching: if XML files already exist and are newer than the VI file,
    skip extraction and return cached files. This significantly speeds up
    repeated operations on the same VI hierarchy.

    Args:
        vi_path: Path to the .vi file.
        output_dir: Directory for output files. Defaults to a per-VI
            subdirectory under the OS temp dir
            (``<tempdir>/lvkit/extract/<stem>_<hash>/``) so extracted
            artifacts never land in the user's source tree.
        force: Force re-extraction even if cache is valid.

    Returns:
        Tuple of ``(bd_xml_path, fp_xml_path, main_xml_path)``.
        ``fp_xml`` and ``main_xml`` may be ``None`` if not generated.

    Raises:
        RuntimeError: If extraction fails.
    """
    vi_path = Path(vi_path).resolve()

    if output_dir is None:
        output_dir = _default_cache_dir(vi_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    vi_stem = vi_path.stem
    bd_xml = output_dir / f"{vi_stem}_BDHb.xml"
    fp_xml = output_dir / f"{vi_stem}_FPHb.xml"
    main_xml = output_dir / f"{vi_stem}.xml"

    # Check cache: skip extraction if XML files exist and are newer than VI
    if not force and bd_xml.exists():
        vi_mtime = vi_path.stat().st_mtime
        bd_mtime = bd_xml.stat().st_mtime

        # BD XML must be newer than VI, and other files if they exist
        if bd_mtime >= vi_mtime:
            fp_valid = not fp_xml.exists() or fp_xml.stat().st_mtime >= vi_mtime
            main_valid = not main_xml.exists() or main_xml.stat().st_mtime >= vi_mtime
            if fp_valid and main_valid:
                # Cache hit - return existing files
                return (
                    bd_xml,
                    fp_xml if fp_xml.exists() else None,
                    main_xml if main_xml.exists() else None,
                )

    # Cache miss - extract using pylabview
    result = subprocess.run(
        [sys.executable, "-m", "pylabview.readRSRC", "-i", str(vi_path), "-x"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(f"pylabview extraction failed: {result.stderr}")

    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return (
        bd_xml,
        fp_xml if fp_xml.exists() else None,
        main_xml if main_xml.exists() else None,
    )
