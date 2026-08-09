"""VI XML extraction using pylabview."""

from __future__ import annotations

import argparse
import io
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

# Import the patch layer BEFORE pylabview: its module-level code installs the
# compile-time SyntaxWarning filter (and holds the runtime monkeypatches) ahead
# of the first pylabview compile below. Order is load-bearing, so keep it above.
from lvkit._pylabview_patches import install_pylabview_patches  # isort: skip

import pylabview.LVrsrcontainer as _lv_rsrc  # type: ignore[import-untyped]  # noqa: E402
import pylabview.LVxml as _lv_xml  # type: ignore[import-untyped]  # noqa: E402

# All pylabview monkeypatches live in _pylabview_patches.py. Apply them before
# any extraction runs (idempotent).
install_pylabview_patches()


# The cache-location + freshness primitives live in the stdlib-only
# ``cache_paths`` module so the CLI's render/diff HIT path can locate and
# validate a cached artifact without importing this module (which pulls
# pylabview). Re-exported here so long-standing callers of ``extractor.X`` (e.g.
# ``graph.core`` -> ``extractor.set_extraction_roots``) keep working unchanged.
from lvkit.cache_paths import (  # noqa: E402
    _slug,
    classify,
    global_cache_root,
    meta_fresh,
    migrate_legacy_extract,
)
from lvkit.text_encoding import (  # noqa: E402
    labview_text_encoding,
    normalize_extracted_xml,
)

logger = logging.getLogger(__name__)

# Windows caps a path at MAX_PATH (260, incl. the trailing NUL) unless the app
# opts into long paths. Two-part defence: (1) extract into a SHALLOW staging dir
# so the *temp* path never grows with project depth (the failure mode a deep
# repo hits), and (2) watch the FINAL path with a margin so a genuinely deep
# project announces itself in the logs BEFORE it can fail a real user — the
# residual case (a final cache path that is itself > 260) whose structural fix
# is bounding the per-project slug. Extraction is the deepest cache surface, so
# if its path is safe the shorter render/diff/index leaves under the same slug
# are too.
_WIN_MAX_PATH = 260
_WIN_PATH_WARN_AT = 240
_warned_near_max_path = False


def _staging_root() -> Path:
    """SHALLOW per-user staging dir (``<cache>/.staging``) for extraction temp
    files. On the same filesystem as every cache dir (both under
    ``global_cache_root()``) so the publish ``os.replace`` stays atomic — but
    kept OUT of the deep ``projects/<slug>/…`` output dir, so the temp path is
    independent of how deep the project sits and a deep repo can't push it past
    MAX_PATH."""
    d = global_cache_root() / ".staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _warn_if_near_windows_limit(path: Path) -> None:
    """Log ONCE per process when a cache path nears the Windows MAX_PATH limit —
    the sensor that surfaces a deep repo in telemetry before it fails a user.
    No-op off Windows."""
    global _warned_near_max_path
    if sys.platform != "win32" or _warned_near_max_path:
        return
    if len(str(path)) >= _WIN_PATH_WARN_AT:
        _warned_near_max_path = True
        logger.warning(
            "lvkit cache path is %d chars, nearing Windows MAX_PATH (%d); a "
            "deeper project will start failing extraction. Mitigate by setting "
            "LVKIT_CACHE_DIR to a short root, enabling LongPathsEnabled, or "
            "moving the project higher in the tree. Path: %s",
            len(str(path)), _WIN_MAX_PATH, path,
        )


def _windows_long_path_hint(path: Path) -> str:
    """Diagnostic suffix that turns a bare OSError into an actionable message
    when a Windows path is genuinely over-length. '' unless on Windows AND over
    the limit."""
    if sys.platform != "win32" or len(str(path)) < _WIN_MAX_PATH:
        return ""
    return (
        f" — this is a Windows MAX_PATH failure: the path is {len(str(path))} "
        f"chars (limit {_WIN_MAX_PATH}). Set LVKIT_CACHE_DIR to a short root, "
        f"enable LongPathsEnabled, or move the project higher in the tree."
    )


def _cache_target(vi_path: Path) -> Path:
    """The per-VI EXTRACTION cache directory for ``vi_path`` (created).

    A one-time rename migrates any pre-``extract/`` layout on first use.
    """
    migrate_legacy_extract()
    target, _, _ = classify(vi_path, "extract")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_cache_meta(vi_path: Path, meta_path: Path) -> None:
    _, source, _ = classify(vi_path, "extract")
    from lvkit.cache_paths import write_meta

    # meta.json is the cache-VALID marker (readers gate on meta_fresh), so write
    # it to a UNIQUE temp sidecar and atomically rename into place — a concurrent
    # reader never sees a half-written meta, and racing writers (threads OR
    # processes) can't corrupt or collide on it. mkstemp guarantees a unique
    # name even within one process (os.getpid() alone collides across threads).
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{meta_path.name}.", suffix=".tmp", dir=meta_path.parent
    )
    os.close(fd)
    tmp = Path(tmp_name)
    write_meta(
        vi_path, tmp,
        source=source, tool="pylabview", extracted_at=time.time(),
        text_encoding=labview_text_encoding(),
    )
    os.replace(tmp, meta_path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically — a unique temp file in the same
    directory then ``os.replace`` — so concurrent extractors of the same
    archive can't interleave into a half-written member file."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _make_read_po(**overrides: object) -> argparse.Namespace:
    """The pylabview "parsed options" namespace for an in-process RSRC READ.

    Centralizes the read-path defaults (``verbose``/``print_map`` + the
    typedesc/array/data limits) shared by every in-process
    ``LVrsrcontainer.VI`` construction — here and in
    :mod:`lvkit.flattened_typedesc`. Callers pass the site-specific fields
    (``rsrc``/``xml``/``filebase``/…) as ``overrides``.
    """
    opts: dict[str, object] = {
        "verbose": 0,
        "print_map": None,
        "typedesc_list_limit": 4095,
        "array_data_limit": (2**28) - 1,
        "store_as_data_above": 4095,
    }
    opts.update(overrides)
    return argparse.Namespace(**opts)


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
    po = _make_read_po(
        rsrc=str(vi_path),
        xml=str(xml_path),
        textcp="mac_roman",
        raw_connectors=False,
        keep_names=False,
        filebase=vi_stem,
        list=False,
        dump=False,
        extract=True,
        create=False,
        password=None,
    )
    with open(vi_path, "rb") as rsrc_fh:
        vi = _lv_rsrc.VI(po, rsrc_fh=rsrc_fh, text_encoding=po.textcp)
        root = vi.exportXMLTree()
    tree = _lv_xml.ElementTree(root)
    with open(xml_path, "wb") as xml_fh:
        tree.write(xml_fh, encoding="utf-8", xml_declaration=True)
    for path in output_dir.iterdir():
        belongs_to_vi = (
            path.name == f"{vi_stem}.xml"
            or path.name.startswith(f"{vi_stem}_")
        )
        if path.suffix == ".xml" and belongs_to_vi:
            normalize_extracted_xml(path)


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
            subdirectory under the global per-user cache
            (``global_cache_root()``; see ``_cache_target``) so extracted
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
    if (
        not force
        and bd_xml.exists()
        and meta_fresh(
            vi_path,
            meta_path,
            extra={"text_encoding": labview_text_encoding()},
        )
    ):
        return (
            bd_xml,
            fp_xml if fp_xml.exists() else None,
            main_xml if main_xml.exists() else None,
        )

    # Cache miss. Extract into a PRIVATE temp dir on the same filesystem, then
    # atomically publish each artifact into the shared cache dir (meta.json
    # LAST, as the cache-valid marker). This makes concurrent extractions of the
    # SAME VI safe — two lvkit processes (parallel agents, a CI matrix, or a
    # user running several commands) each publish COMPLETE files via atomic
    # os.replace, and a reader (which gates on bd_xml + meta_fresh) never sees a
    # half-written cache. Writing straight into output_dir would let concurrent
    # writers interleave into the same file and corrupt it.
    #
    # There is deliberately NO subprocess fallback: it runs UNPATCHED pylabview
    # (re-hitting bugs we've patched), fails identically on genuine crashes, and
    # only masks the true cause behind a wrapped error. The byte-identical gate
    # confirmed in-process == subprocess output.
    # Advance sensor: if the FINAL path is nearing MAX_PATH, say so now (once)
    # so a deep repo is visible in logs before it can fail a user.
    _warn_if_near_windows_limit(bd_xml)

    # Extract into a SHALLOW staging dir (``<cache>/.staging``), NOT nested under
    # the deep ``projects/<slug>/…`` output dir. Two payoffs: the temp path is
    # independent of project depth (a deep repo can't overflow it), and pylabview
    # only ever writes short paths — no MAX_PATH exposure for the extractor at all.
    # ``.x-`` prefix (no ``vi_stem``): mkdtemp's random suffix is already unique
    # and the dir is private, so embedding the name only cost length. Same
    # filesystem as output_dir (both under global_cache_root), so the publish
    # os.replace stays atomic.
    tmp_dir = Path(tempfile.mkdtemp(prefix=".x-", dir=_staging_root()))
    try:
        try:
            _extract_in_process(vi_path, tmp_dir, vi_stem)
        except Exception as exc:
            raise RuntimeError(
                f"pylabview extraction failed for {vi_path.name}: {exc}"
                f"{_windows_long_path_hint(tmp_dir / f'{vi_stem}_BDHb.xml')}"
            ) from exc

        if not (tmp_dir / f"{vi_stem}_BDHb.xml").exists():
            raise RuntimeError(f"Block diagram XML not found: {vi_stem}_BDHb.xml")

        # Publish every produced artifact atomically (same filesystem as the
        # temp dir), then meta.json last so its appearance marks the cache valid.
        for produced in sorted(tmp_dir.iterdir()):
            if produced.is_file():
                os.replace(produced, output_dir / produced.name)
        _write_cache_meta(vi_path, meta_path)
    except OSError as exc:
        # A publish/mkdir OSError on a genuinely deep FINAL path — augment the
        # bare Errno with the Windows long-path diagnosis so it's actionable.
        raise RuntimeError(
            f"failed to write extraction cache for {vi_path.name}: {exc}"
            f"{_windows_long_path_hint(bd_xml)}"
        ) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if not bd_xml.exists():
        raise RuntimeError(f"Block diagram XML not found: {bd_xml}")

    return (
        bd_xml,
        fp_xml if fp_xml.exists() else None,
        main_xml if main_xml.exists() else None,
    )


def resolve_extracted(
    vi_path: Path | str, *, force: bool = False
) -> tuple[Path, Path | None, Path | None]:
    """Single entry point readers use to get a VI's extracted XML.

    Returns ``(bd_xml, fp_xml, main_xml)`` from the global per-user cache (see
    ``_cache_target``), extracting on a miss. Both the engine and every reader
    route through this so writes and reads agree — the cache path is a pure
    function of the resolved ``.vi`` path and the run's library roots.
    """
    return extract_vi_xml(vi_path, force=force)


# ===== LLB container extraction =====

_UNSAFE_CHARS = re.compile(r"[\\/*?:<>|\x00-\x1f]+")


def _llb_cache_dir(llb_path: Path) -> Path:
    """Return a stable per-LLB cache directory under the global cache root.

    ``<global-cache>/llb/<slug>`` where slug is the LLB's absolute path with
    separators turned into ``-`` (see :func:`_slug`) — readable, no hash.
    """
    return global_cache_root() / "llb" / _slug(llb_path)


def _open_llb_vi(llb_path: Path) -> Any:
    """Open an LLB file with the pylabview VI API.

    Returns a ``pylabview.LVrsrcontainer.VI`` object, or raises RuntimeError
    if the file cannot be parsed.
    """
    try:
        import pylabview.LVrsrcontainer as lvrsrc  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("pylabview is not installed") from exc

    po = _make_read_po(
        rsrc=str(llb_path),
        xml="",
        filebase=llb_path.stem,
        keep_names=True,
        raw_connectors=False,
    )
    with open(llb_path, "rb") as fh:
        vi = lvrsrc.VI(
            po,
            rsrc_fh=fh,
            text_encoding=labview_text_encoding(),
        )
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
                _atomic_write_bytes(cache_dir / member_name, bldata.read())
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
                            _atomic_write_bytes(
                                cache_dir / member_name, zf.read(member)
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

