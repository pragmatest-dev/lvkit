"""VI XML extraction using pylabview."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_CACHE_ROOT = Path(tempfile.gettempdir()) / "lvkit" / "extract"
_LLB_CACHE_ROOT = Path(tempfile.gettempdir()) / "lvkit" / "llb"


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


# ===== LLB container extraction =====

_UNSAFE_CHARS = re.compile(r"[\\/*?:<>|\x00-\x1f]+")


def _llb_cache_dir(llb_path: Path) -> Path:
    """Return a stable per-LLB cache directory under the OS temp dir.

    Same naming scheme as ``_default_cache_dir()``:
    ``<stem>_<hash12>`` where hash12 is the first 12 hex chars of
    SHA-256 over the resolved absolute path.
    """
    digest = hashlib.sha256(str(llb_path).encode("utf-8")).hexdigest()[:12]
    return _LLB_CACHE_ROOT / f"{llb_path.stem}_{digest}"


def _open_llb_vi(llb_path: Path):  # type: ignore[return]
    """Open an LLB file with the pylabview VI API.

    Returns a ``pylabview.LVrsrcontainer.VI`` object, or raises RuntimeError
    if the file cannot be parsed.
    """
    try:
        import pylabview.LVrsrcontainer as lvrsrc  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("pylabview is not installed") from exc

    po = argparse.Namespace(
        verbose=0,
        rsrc=str(llb_path),
        xml="",
        filebase=llb_path.stem,
        print_map=None,
        keep_names=True,
        raw_connectors=False,
        typedesc_list_limit=4095,
        array_data_limit=(2**28) - 1,
        store_as_data_above=4095,
    )
    with open(llb_path, "rb") as fh:
        vi = lvrsrc.VI(po, rsrc_fh=fh, text_encoding="mac_roman")
    return vi


def _decode_member_name(name_bytes: bytes, encoding: str) -> str:
    """Decode a section name_text to a safe filename."""
    raw = name_bytes.decode(encoding, errors="replace").strip()
    safe = _UNSAFE_CHARS.sub("-", raw)
    return safe


def extract_llb(llb_path: Path) -> Path:
    """Extract all member VIs from an LLB archive to a cache directory.

    Skips extraction if the cache dir already exists and is newer than the
    LLB file.  Returns the cache directory path.

    Supports both classic UCRF/CPRF/ZCRF LLBs and modern LVzp (XOR-ZIP) LLBs.

    Args:
        llb_path: Path to the ``.llb`` file.

    Returns:
        Path to the cache directory containing extracted ``.vi`` files.

    Raises:
        RuntimeError: If the LLB cannot be opened or extracted.
    """
    llb_path = llb_path.resolve()
    cache_dir = _llb_cache_dir(llb_path)

    # Cache validity check: skip if sentinel exists and is newer than the LLB
    sentinel = cache_dir / ".extracted"
    if sentinel.exists():
        llb_mtime = llb_path.stat().st_mtime
        if sentinel.stat().st_mtime >= llb_mtime:
            return cache_dir

    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        vi = _open_llb_vi(llb_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to open LLB {llb_path}: {exc}") from exc

    extracted_any = False

    # Try classic block types in priority order
    for block_ident in ("UCRF", "CPRF", "ZCRF"):
        block = vi.get(block_ident)
        if block is None:
            continue
        for snum, section in block.sections.items():
            if section.name_text is None or len(section.name_text) == 0:
                continue
            member_name = _decode_member_name(section.name_text, vi.textEncoding)
            if not member_name:
                continue
            try:
                bldata: io.BytesIO = block.getData(section_num=snum)
                (cache_dir / member_name).write_bytes(bldata.read())
                extracted_any = True
            except Exception:
                pass  # Skip unreadable sections; they remain absent from cache

    # Fall back to modern LVzp format (XOR-encrypted ZIP)
    if not extracted_any:
        block = vi.get("LVzp")
        if block is not None and block.sections:
            snum = next(iter(block.sections))
            try:
                bldata = block.getData(section_num=snum)
                with zipfile.ZipFile(io.BytesIO(bldata.read())) as zf:
                    for member in zf.namelist():
                        if member.lower().endswith(".vi"):
                            member_name = _UNSAFE_CHARS.sub(
                                "-", Path(member).name
                            )
                            (cache_dir / member_name).write_bytes(
                                zf.read(member)
                            )
                            extracted_any = True
            except Exception:
                pass

    # Write sentinel so future calls skip re-extraction
    sentinel.touch()

    if not extracted_any:
        # Empty LLB or unrecognised format — cache dir exists but is empty
        pass

    return cache_dir


def list_llb_members(llb_path: Path) -> list[str]:
    """Return member filenames inside an LLB without full extraction.

    This is lighter-weight than ``extract_llb()`` — it reads section headers
    only, without writing files.

    Args:
        llb_path: Path to the ``.llb`` file.

    Returns:
        List of member filenames (e.g. ``["Error Cluster From Error Code.vi"]``).
    """
    llb_path = llb_path.resolve()

    try:
        vi = _open_llb_vi(llb_path)
    except Exception:
        return []

    members: list[str] = []

    for block_ident in ("UCRF", "CPRF", "ZCRF"):
        block = vi.get(block_ident)
        if block is None:
            continue
        for section in block.sections.values():
            if section.name_text is None or len(section.name_text) == 0:
                continue
            member_name = _decode_member_name(section.name_text, vi.textEncoding)
            if member_name:
                members.append(member_name)

    if not members:
        block = vi.get("LVzp")
        if block is not None and block.sections:
            snum = next(iter(block.sections))
            try:
                bldata = block.getData(section_num=snum)
                with zipfile.ZipFile(io.BytesIO(bldata.read())) as zf:
                    for member in zf.namelist():
                        if member.lower().endswith(".vi"):
                            members.append(Path(member).name)
            except Exception:
                pass

    return members
