"""VI XML extraction using pylabview."""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

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
from lvkit import staging  # noqa: E402
from lvkit.cache_paths import (  # noqa: E402
    MANAGED_ARTIFACT_STEM,
    _slug,
    classify,
    cleanup_legacy_cache,
    global_cache_root,
    meta_fresh,
    touch_if_stale,
    write_meta,
)
from lvkit.llb_origin import (  # noqa: E402,F401
    LLB_SENTINEL,
    llb_container_of,
    read_llb_meta,
)
from lvkit.run_report import (  # noqa: E402
    CacheWriteError,
    UnreadableVIError,
    home_relative,
)
from lvkit.stderr_capture import capture_stderr  # noqa: E402
from lvkit.text_encoding import (  # noqa: E402
    labview_text_encoding,
    normalize_extracted_xml,
    vers_language_of,
)
from lvkit.vi_files import is_vi_file  # noqa: E402

logger = logging.getLogger(__name__)

# Windows caps a path at MAX_PATH (260, incl. the trailing NUL) unless the app
# opts into long paths. Two-part defence: (1) extract into a SHALLOW staging dir
# (``<cache>/.staging/…``, see :mod:`lvkit.staging`) so the *temp* path never
# grows with project depth (the failure mode a deep repo hits), and (2) watch the
# FINAL path with a margin so a genuinely deep project announces itself in the
# logs BEFORE it can fail a real user — the residual case (a final cache path
# that is itself > 260) whose structural fix is bounding the per-project slug.
# Extraction is the deepest cache surface, so if its path is safe the shorter
# render/diff/index leaves under the same slug are too.
_WIN_MAX_PATH = 260
_WIN_PATH_WARN_AT = 240
_warned_near_max_path = False


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
            len(str(path)),
            _WIN_MAX_PATH,
            path,
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
    """The per-VI EXTRACTION cache directory for ``vi_path`` (not created: the
    publish step creates it, or renames a finished one into place).

    A one-time cleanup drops the abandoned kind-first cache trees on first use.
    """
    cleanup_legacy_cache()
    target, _, _ = classify(vi_path, "extract")
    return target


def _write_cache_meta(vi_path: Path, meta_path: Path) -> None:
    """Write the cache-VALID marker for ``vi_path`` at ``meta_path``. ``meta_path``
    is inside a private staging directory; it reaches the cache only when the
    finished extraction is published (meta last, atomically)."""
    _, source, _ = classify(vi_path, "extract")
    write_meta(
        vi_path,
        meta_path,
        source=source,
        tool="pylabview",
        extracted_at=time.time(),
        text_encoding=labview_text_encoding(),
    )


def _replace_unless_same(src: Path, dst: Path) -> None:
    """Move ``src`` over ``dst`` atomically, or leave ``dst`` alone when it already
    holds exactly the same bytes (re-extraction of an unchanged block writes
    nothing)."""
    try:
        if dst.stat().st_size == src.stat().st_size and (
            dst.read_bytes() == src.read_bytes()
        ):
            return
    except OSError:
        pass
    staging.replace(src, dst)


def _publish(stage: Path, output_dir: Path, meta_name: str) -> None:
    """Make the finished extraction in ``stage`` visible at ``output_dir``.

    Fresh slot (the common case): ONE directory rename puts every file there at
    once, meta included. If ``output_dir`` already exists (a re-extraction, a
    concurrent extractor that won the race, or a platform that refuses the
    rename), fall back to per-file atomic replaces with the meta LAST, so a reader
    gating on ``meta_fresh`` never sees a half-published slot.
    """
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if not output_dir.exists():
        try:
            os.rename(stage, output_dir)
            return
        except OSError:
            pass
    output_dir.mkdir(exist_ok=True)
    produced = sorted(p for p in stage.iterdir() if p.is_file())
    for path in (p for p in produced if p.name != meta_name):
        _replace_unless_same(path, output_dir / path.name)
    if (stage / meta_name).exists():
        staging.replace(stage / meta_name, output_dir / meta_name)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically — a unique file in this run's staging
    directory then ``os.replace`` — so concurrent extractors of the same archive
    can't interleave into a half-written member file."""
    tmp = staging.temp_path(global_cache_root())
    try:
        tmp.write_bytes(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(tmp, path)
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


def _extract_in_process(vi_path: Path, output_dir: Path, artifact_stem: str) -> None:
    """Extract a VI to XML in-process, matching ``readRSRC -i <vi> -x``.

    pylabview places each block's sidecar XML (``_BDHb.xml`` etc.) in
    ``os.path.dirname(po.xml)`` and records only the basename in the
    ``File=`` attribute (``LVblock.exportFilesBase``). Setting ``po.xml`` to an
    absolute path inside ``output_dir`` therefore lands every file there with
    byte-identical content and **no** ``chdir`` — safe under the render app's
    request concurrency. This avoids the per-call interpreter boot + pylabview
    re-import that the subprocess path pays on every extraction.

    ``artifact_stem`` names the produced files — the caller's real VI stem for a
    caller-owned ``output_dir``, or the fixed ``MANAGED_ARTIFACT_STEM`` in the
    managed cache; it is NOT necessarily ``vi_path.stem``.

    Mirrors the option set that ``readRSRC.main()`` builds for the ``-x``
    (extract) subcommand via argparse.
    """
    xml_path = output_dir / f"{artifact_stem}.xml"
    po = _make_read_po(
        rsrc=str(vi_path),
        xml=str(xml_path),
        textcp="mac_roman",
        raw_connectors=False,
        keep_names=False,
        filebase=artifact_stem,
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
    # Computed ONCE (from the VI's own recorded Language -- real data, not a
    # guess) and passed to every one of this VI's XML files so they all
    # normalize under the same encoding.
    target_encoding = labview_text_encoding(vers_language_of(root))
    for path in output_dir.iterdir():
        belongs_to_vi = path.name == f"{artifact_stem}.xml" or path.name.startswith(
            f"{artifact_stem}_"
        )
        if path.suffix == ".xml" and belongs_to_vi:
            normalize_extracted_xml(path, target_encoding)


def _extract_capturing_warnings(
    vi_path: Path, output_dir: Path, artifact_stem: str
) -> tuple[str, ...]:
    """Run ``_extract_in_process``, returning the lines pylabview printed to
    stderr while reading ``vi_path`` (its absolute path replaced by the file
    name). Raises ``UnreadableVIError`` carrying them when extraction fails."""
    error: Exception | None = None
    with capture_stderr() as captured:
        try:
            _extract_in_process(vi_path, output_dir, artifact_stem)
        except Exception as exc:
            error = exc
    lines = tuple(
        line.replace(str(vi_path), vi_path.name).strip()
        for line in captured.getvalue().splitlines()
        if line.strip()
    )
    if error is not None:
        raise UnreadableVIError(
            f"pylabview could not read the file: {error}", lines, vi_path
        ) from error
    return lines


class _ArtifactSlot(NamedTuple):
    """Where one VI's extracted artifacts live."""

    output_dir: Path
    artifact_stem: str
    bd_xml: Path
    fp_xml: Path
    main_xml: Path
    meta_path: Path


def _artifact_slot(vi_path: Path, output_dir: Path | None) -> _ArtifactSlot:
    """The artifact paths for the resolved ``vi_path``.

    A caller-supplied ``output_dir`` is caller-owned, so keep the real source stem
    in the artifact names there. The MANAGED cache (``output_dir`` is None) uses a
    FIXED short stem instead: a long source filename would otherwise overflow
    the cache path once a ``_BDHb.xml`` suffix is appended — the second Windows
    MAX_PATH axis, paired with the bounded per-VI slot dir (cache_paths._vi_slot).
    The real source name/path is preserved for callers: the graph loader keys
    every node on the source path (never on this stem), and the ``source`` field
    of the ``.meta.json`` records the real owner-relative path.
    """
    managed = output_dir is None
    if output_dir is None:
        output_dir = _cache_target(vi_path)
    stem = MANAGED_ARTIFACT_STEM if managed else vi_path.stem
    return _ArtifactSlot(
        output_dir,
        stem,
        output_dir / f"{stem}_BDHb.xml",
        output_dir / f"{stem}_FPHb.xml",
        output_dir / f"{stem}.xml",
        output_dir / f"{stem}.meta.json",
    )


def _cache_hit(
    vi_path: Path, slot: _ArtifactSlot
) -> tuple[Path, Path | None, Path | None] | None:
    """The cached ``(bd_xml, fp_xml, main_xml)`` when the XML is present and the
    recorded content-hash (or mtime/size fast-path) still matches the VI, else
    None. The extraction-code compatibility check is the <extraction_fingerprint>
    level of the cache PATH (see cache_paths.kind_fingerprint): an extraction-code
    change lands in a DIFFERENT <fp> dir, so a stale slice is never even looked at
    here — this meta only has to catch a VI-content or text-encoding change. Bumps
    the slice's mtime on a hit so the access-TTL sweep keeps the build in active
    use and only retires ones the user has upgraded away from.
    """
    if not slot.bd_xml.exists() or not meta_fresh(
        vi_path,
        slot.meta_path,
        extra={"text_encoding": labview_text_encoding()},
    ):
        return None
    touch_if_stale(slot.bd_xml, slot.meta_path)
    return (
        slot.bd_xml,
        slot.fp_xml if slot.fp_xml.exists() else None,
        slot.main_xml if slot.main_xml.exists() else None,
    )


def cached_extraction(
    vi_path: Path | str,
) -> tuple[Path, Path | None, Path | None] | None:
    """``(bd_xml, fp_xml, main_xml)`` when ``vi_path`` is already extracted into
    the managed cache and still valid — exactly the case in which
    :func:`extract_vi_xml` returns without extracting — else None."""
    vi_path = Path(vi_path).resolve()
    return _cache_hit(vi_path, _artifact_slot(vi_path, None))


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
        UnreadableVIError: If pylabview cannot read the file or exports no
            block diagram (its ``warnings`` are what pylabview printed).
        CacheWriteError: If the extraction cannot be written to the cache.
    """
    vi_path = Path(vi_path).resolve()
    slot = _artifact_slot(vi_path, output_dir)
    output_dir, artifact_stem = slot.output_dir, slot.artifact_stem
    bd_xml, meta_path = slot.bd_xml, slot.meta_path

    if not force:
        hit = _cache_hit(vi_path, slot)
        if hit is not None:
            return hit

    # Cache miss. Extract into a PRIVATE work dir inside this process's single
    # staging directory (see :mod:`lvkit.staging`), then publish it into the cache
    # in one atomic step (meta.json LAST, as the cache-valid marker). This makes
    # concurrent extractions of the SAME VI safe — two lvkit processes (parallel
    # agents, a CI matrix, or a user running several commands) each publish a
    # COMPLETE slot, and a reader (which gates on bd_xml + meta_fresh) never sees a
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

    # The work dir sits in the SHALLOW ``<cache>/.staging`` tree, NOT nested under
    # the deep ``projects/<slug>/…`` output dir. Two payoffs: the temp path is
    # independent of project depth (a deep repo can't overflow it), and pylabview
    # only ever writes short paths — no MAX_PATH exposure for the extractor at all.
    # Same filesystem as output_dir (both under global_cache_root), so the publish
    # rename stays atomic.
    tmp_dir = staging.work_dir(global_cache_root())
    try:
        try:
            warnings = _extract_capturing_warnings(vi_path, tmp_dir, artifact_stem)
        except UnreadableVIError as exc:
            hint = _windows_long_path_hint(tmp_dir / f"{artifact_stem}_BDHb.xml")
            raise UnreadableVIError(
                f"{exc.reason}{hint}", exc.warnings, vi_path
            ) from exc

        if not (tmp_dir / f"{artifact_stem}_BDHb.xml").exists():
            raise UnreadableVIError(
                "no block diagram could be read", warnings, vi_path
            )

        _write_cache_meta(vi_path, tmp_dir / meta_path.name)
        _publish(tmp_dir, output_dir, meta_path.name)
    except OSError as exc:
        # A publish/mkdir OSError on a genuinely deep FINAL path — augment the
        # bare Errno with the Windows long-path diagnosis so it's actionable.
        raise CacheWriteError(
            f"could not write the extraction cache at {home_relative(bd_xml.parent)}: "
            f"{exc.strerror or exc}{_windows_long_path_hint(bd_xml)}"
        ) from exc
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not bd_xml.exists():
        raise UnreadableVIError("no block diagram could be read", path=vi_path)

    return (
        bd_xml,
        slot.fp_xml if slot.fp_xml.exists() else None,
        slot.main_xml if slot.main_xml.exists() else None,
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
    # Later member sections read from this stream lazily. Keep an in-memory
    # snapshot owned by the VI, without retaining an open filesystem handle.
    archive_stream = io.BytesIO(llb_path.read_bytes())
    archive_stream.name = str(llb_path)
    vi = lvrsrc.VI(
        po,
        rsrc_fh=archive_stream,
        text_encoding=labview_text_encoding(),
    )
    return vi


def _decode_member_name(name_bytes: bytes, encoding: str) -> str:
    """Decode a section name_text to a safe filename."""
    raw = name_bytes.decode(encoding, errors="replace").strip()
    safe = _UNSAFE_CHARS.sub("-", raw)
    return safe


class LlbFailure(NamedTuple):
    """A member of an LLB that could not be extracted: ``member`` is its file name,
    or None when the archive's whole content block failed."""

    member: str | None
    reason: str


class LlbExtraction(NamedTuple):
    """The cache folder an LLB was extracted into and what it could not extract."""

    cache_dir: Path
    failures: tuple[LlbFailure, ...]


def _llb_failures(meta: dict[str, Any]) -> tuple[LlbFailure, ...]:
    return tuple(LlbFailure(member, reason) for member, reason in meta["failures"])


def extract_llb_members(llb_path: Path) -> LlbExtraction:
    """Extract all member VIs from an LLB archive to a cache directory.

    Skips extraction if the cache dir already exists and is newer than the
    LLB file.  Returns the cache directory and the members that could not be
    extracted (recorded in the folder, so a later call reports them too).

    Supports both classic UCRF/CPRF/ZCRF LLBs and modern LVzp (XOR-ZIP) LLBs.

    Args:
        llb_path: Path to the ``.llb`` file.

    Returns:
        The cache directory containing extracted ``.vi`` files and the failures.

    Raises:
        RuntimeError: If the LLB cannot be opened or extracted.
    """
    llb_path = llb_path.resolve()
    cache_dir = _llb_cache_dir(llb_path)

    # Cache validity check: skip if the sentinel records this archive and is newer
    # than the LLB file.
    sentinel = cache_dir / LLB_SENTINEL
    meta = read_llb_meta(cache_dir)
    if meta is not None and sentinel.stat().st_mtime >= llb_path.stat().st_mtime:
        return LlbExtraction(cache_dir, _llb_failures(meta))

    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        vi = _open_llb_vi(llb_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to open LLB {llb_path}: {exc}") from exc

    extracted_any = False
    failures: list[LlbFailure] = []

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
            except Exception as exc:
                failures.append(LlbFailure(member_name, str(exc)))

    # Fall back to modern LVzp format (XOR-encrypted ZIP)
    if not extracted_any:
        block = vi.get("LVzp")
        if block is not None and block.sections:
            snum = next(iter(block.sections))
            try:
                bldata = block.getData(section_num=snum)
                with zipfile.ZipFile(io.BytesIO(bldata.read())) as zf:
                    for member in zf.namelist():
                        if is_vi_file(member):
                            member_name = _UNSAFE_CHARS.sub("-", Path(member).name)
                            _atomic_write_bytes(
                                cache_dir / member_name, zf.read(member)
                            )
                            extracted_any = True
            except Exception as exc:
                failures.append(LlbFailure(None, f"LVzp block: {exc}"))

    # The sentinel records the archive (so a VI in this folder is known by the
    # .llb's path) and what could not be extracted, and tells future calls to skip
    # re-extraction.
    sentinel.write_text(
        json.dumps(
            {
                "source": str(llb_path),
                "failures": [[f.member, f.reason] for f in failures],
            }
        ),
        encoding="utf-8",
    )

    if not extracted_any:
        # Empty LLB or unrecognised format — cache dir exists but is empty
        pass

    return LlbExtraction(cache_dir, tuple(failures))


def extract_llb(llb_path: Path) -> Path:
    """The cache directory ``extract_llb_members`` extracted ``llb_path`` into."""
    return extract_llb_members(llb_path).cache_dir
