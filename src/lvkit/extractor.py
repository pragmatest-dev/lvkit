"""VI XML extraction using pylabview."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]
import pylabview.LVxml as _lv_xml  # type: ignore[import-untyped]

from lvkit._pylabview_patches import install_pylabview_patches

# All pylabview monkeypatches live in _pylabview_patches.py. Apply them before
# any extraction runs (idempotent).
install_pylabview_patches()

_CACHE_ROOT = Path(tempfile.gettempdir()) / "lvkit" / "extract"
_LLB_CACHE_ROOT = Path(tempfile.gettempdir()) / "lvkit" / "llb"


def _default_temp_cache_dir(vi_path: Path) -> Path:
    """Fallback per-VI cache dir under the OS temp dir, for VIs outside any
    project (ad-hoc ``lvkit render /tmp/foo.vi``). Name is ``<stem>_<hash12>``
    over the resolved absolute path.
    """
    digest = hashlib.sha256(str(vi_path).encode("utf-8")).hexdigest()[:12]
    return _CACHE_ROOT / f"{vi_path.stem}_{digest}"


def _project_root_for(vi_path: Path) -> Path | None:
    """Nearest ancestor that is a project root: one holding a ``.lvkit/`` store
    or a git repo root (``.git``). Returns the root dir, or ``None`` for a VI
    outside any project. (Mirrors ``project_store.find_project_store`` but also
    reports the git root so the cache can be created there on demand.)
    """
    ancestors = (vi_path.parent, *vi_path.parent.parents)
    # An EXISTING .lvkit/ store wins, even when a NEARER ancestor is its own git
    # repo: vendored/cloned corpora (each carrying a .git) must share the outer
    # project's cache instead of sprouting a .lvkit/ inside every clone.
    for anc in ancestors:
        if (anc / ".lvkit").is_dir():
            return anc
    for anc in ancestors:
        if (anc / ".git").exists():
            return anc
    return None


def extraction_cache_root(vi_path: Path) -> Path | None:
    """Project-local extraction cache root, created on demand.

    Returns ``<project-root>/.lvkit/cache/extracted`` (creating ``.lvkit/cache``
    if absent and dropping a ``*`` .gitignore so the cache is never committed),
    or ``None`` when ``vi_path`` is outside any project (caller uses temp).
    """
    root = _project_root_for(vi_path)
    if root is None:
        return None
    cache = root / ".lvkit" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    gitignore = cache / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# lvkit derived cache — rebuildable, never commit\n*\n")
    extracted = cache / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    return extracted


def _lv_version() -> str:
    """Best-effort installed-LabVIEW version for the vilib cache namespace."""
    try:
        from lvkit.lv_detect import detect_labview

        detected = detect_labview()
        if detected is not None and detected.version:
            return detected.version
    except Exception:
        pass
    return "unknown"


def classify_vi(vi_path: Path, project_root: Path | None) -> tuple[str, Path]:
    """Classify a VI path into ``(bucket, rel_dir)`` for cache placement.

    Buckets mirror ``pipeline.to_library_name`` but are derived from the
    filesystem path so extraction needs no graph context. ``rel_dir`` is the
    VI's PARENT directory relative to the bucket marker (never the filename), so
    the cache path-mirrors the source and stays portable across clones.
    """
    parts = vi_path.parts

    def _after(marker: str) -> Path:
        # parent dirs AFTER the last occurrence of `marker`, excluding the file
        idx = len(parts) - 1 - parts[::-1].index(marker)
        return Path(*parts[idx + 1 : -1])

    if "vi.lib" in parts:
        return f"vilib/{_lv_version()}", _after("vi.lib")
    if "_OpenG.lib" in parts:
        return "openg", _after("_OpenG.lib")
    if "instr.lib" in parts:
        return "drivers", _after("instr.lib")
    # The dev corpus lives under .lvkit/cache/samples/ ("local-only-always").
    # Bucket it as `samples` so it never nests as project/.lvkit/cache/samples/…
    for i in range(len(parts) - 2):
        if parts[i : i + 3] == (".lvkit", "cache", "samples"):
            return "samples", Path(*parts[i + 3 : -1])
    if project_root is not None:
        try:
            return "project", vi_path.parent.relative_to(project_root)
        except ValueError:
            pass
    digest = hashlib.sha256(str(vi_path).encode("utf-8")).hexdigest()[:12]
    return f"external/{digest}", Path()


def _cache_target(vi_path: Path) -> Path:
    """The per-VI cache directory for ``vi_path`` (created on demand)."""
    extracted = extraction_cache_root(vi_path)
    if extracted is None:
        return _default_temp_cache_dir(vi_path)
    bucket, rel = classify_vi(vi_path, _project_root_for(vi_path))
    target = extracted / bucket / rel
    target.mkdir(parents=True, exist_ok=True)
    return target


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_fresh(vi_path: Path, meta_path: Path) -> bool:
    """True if the cached extraction for ``vi_path`` is still valid.

    Fast-path on unchanged ``(mtime, size)``; otherwise fall back to a content
    ``sha256`` compare (robust to clone/checkout mtime resets) and refresh the
    recorded mtime on a content match.
    """
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return False
    st = vi_path.stat()
    if meta.get("size") == st.st_size and meta.get("mtime") == st.st_mtime:
        return True
    if meta.get("sha256") == _sha256_file(vi_path):
        meta["mtime"] = st.st_mtime
        meta["size"] = st.st_size
        try:
            meta_path.write_text(json.dumps(meta))
        except OSError:
            pass
        return True
    return False


def _write_cache_meta(vi_path: Path, meta_path: Path) -> None:
    st = vi_path.stat()
    root = _project_root_for(vi_path)
    try:
        source = str(vi_path.relative_to(root)) if root else vi_path.name
    except ValueError:
        source = vi_path.name
    meta_path.write_text(json.dumps({
        "source": source,
        "sha256": _sha256_file(vi_path),
        "mtime": st.st_mtime,
        "size": st.st_size,
        "tool": "pylabview",
        "extracted_at": time.time(),
    }))


def _extract_in_process(vi_path: Path, output_dir: Path, vi_stem: str) -> None:
    """Extract a VI to XML in-process, matching ``readRSRC -i <vi> -x``.

    pylabview places each block's sidecar XML (``_BDHb.xml`` etc.) in
    ``os.path.dirname(po.xml)`` and records only the basename in the
    ``File=`` attribute (``LVblock.exportFilesBase``). Setting ``po.xml`` to an
    absolute path inside ``output_dir`` therefore lands every file there with
    byte-identical content and **no** ``chdir`` — safe under the render app's
    request concurrency. This avoids the per-call interpreter boot + pylabview
    re-import that the subprocess path pays on every extraction.

    Mirrors the option set that ``readRSRC.main()`` builds for the ``-x``
    (extract) subcommand via argparse.
    """
    xml_path = output_dir / f"{vi_stem}.xml"
    po = argparse.Namespace(
        verbose=0,
        rsrc=str(vi_path),
        xml=str(xml_path),
        textcp="mac_roman",
        raw_connectors=False,
        print_map=None,
        keep_names=False,
        filebase=vi_stem,
        list=False,
        dump=False,
        extract=True,
        create=False,
        password=None,
        typedesc_list_limit=4095,
        array_data_limit=(2**28) - 1,
        store_as_data_above=4095,
    )
    with open(vi_path, "rb") as rsrc_fh:
        vi = _lv_rsrc.VI(po, rsrc_fh=rsrc_fh, text_encoding=po.textcp)
        root = vi.exportXMLTree()
    tree = _lv_xml.ElementTree(root)
    with open(xml_path, "wb") as xml_fh:
        tree.write(xml_fh, encoding="utf-8", xml_declaration=True)


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
        output_dir = _cache_target(vi_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    vi_stem = vi_path.stem
    bd_xml = output_dir / f"{vi_stem}_BDHb.xml"
    fp_xml = output_dir / f"{vi_stem}_FPHb.xml"
    main_xml = output_dir / f"{vi_stem}.xml"
    meta_path = output_dir / f"{vi_stem}.meta.json"

    # Cache hit: XML present and the recorded content-hash (or mtime/size
    # fast-path) still matches the VI.
    if not force and bd_xml.exists() and _cache_fresh(vi_path, meta_path):
        return (
            bd_xml,
            fp_xml if fp_xml.exists() else None,
            main_xml if main_xml.exists() else None,
        )

    # Cache miss - extract in-process, surfacing the real pylabview error on
    # failure. There is deliberately NO subprocess fallback: it runs UNPATCHED
    # pylabview (so it re-hits bugs we've patched), fails identically on genuine
    # pylabview crashes, and only masks the true cause behind a wrapped error.
    # The byte-identical gate confirmed in-process == subprocess output.
    try:
        _extract_in_process(vi_path, output_dir, vi_stem)
    except Exception as exc:
        raise RuntimeError(
            f"pylabview extraction failed for {vi_path.name}: {exc}"
        ) from exc

    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    _write_cache_meta(vi_path, meta_path)

    return (
        bd_xml,
        fp_xml if fp_xml.exists() else None,
        main_xml if main_xml.exists() else None,
    )


def resolve_extracted(
    vi_path: Path | str, *, force: bool = False
) -> tuple[Path, Path | None, Path | None]:
    """Single entry point readers use to get a VI's extracted XML.

    Returns ``(bd_xml, fp_xml, main_xml)`` from the project-local cache
    (``.lvkit/cache/extracted/<bucket>/<rel>/``), extracting on a miss. Both the
    engine and every reader route through this so writes and reads agree — the
    cache path is a pure function of the resolved ``.vi`` path.
    """
    return extract_vi_xml(vi_path, force=force)


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


def _open_llb_vi(llb_path: Path) -> Any:
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
            except Exception as exc:
                print(
                    f"Warning: failed to extract LVzp block from"
                    f" {llb_path.name}: {exc}",
                    file=sys.stderr,
                )

    # Write sentinel so future calls skip re-extraction
    sentinel.touch()

    if not extracted_any:
        # Empty LLB or unrecognised format — cache dir exists but is empty
        pass

    return cache_dir


