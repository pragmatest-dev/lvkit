"""VI XML extraction using pylabview."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
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


def global_cache_root() -> Path:
    """Root of lvkit's per-user extraction cache — never in the user's repo.

    ``$LVKIT_CACHE_DIR`` if set (test hook + power-user/CI override), else
    ``<global-home>/cache`` — i.e. ``~/.lvkit/cache`` — branded like ``~/.claude``
    and consistent with the project-local ``.lvkit/`` store. The home location
    comes from ``project_store.global_home()`` (the single source of truth).
    """
    env = os.environ.get("LVKIT_CACHE_DIR")
    if env:
        return Path(env)
    from lvkit.project_store import global_home

    return global_home() / "cache"


# ── Run-scoped extraction roots ────────────────────────────────────────────
# The resolved <vilib>/<userlib> roots for the current run, set once at
# `InMemoryVIGraph.set_library_roots` so `_cache_target` can prefix-match a VI
# to its tier without re-guessing from the path. Mirrors the global-config
# pattern of `primitive_resolver.reset_resolver()` (same single-process
# concurrency caveat — one run at a time).
_vilib_root: Path | None = None
_userlib_root: Path | None = None


def set_extraction_roots(
    *, vilib_root: Path | None, userlib_root: Path | None
) -> None:
    """Record the run's resolved library roots for cache classification.

    Roots are resolved to absolute paths so prefix-matching in `_cache_target`
    is stable regardless of how a VI path was spelled. Passing ``None`` clears
    that root.
    """
    global _vilib_root, _userlib_root
    _vilib_root = vilib_root.resolve() if vilib_root is not None else None
    _userlib_root = userlib_root.resolve() if userlib_root is not None else None


def clear_extraction_roots() -> None:
    """Forget the run's library roots (fall back to project/adhoc)."""
    global _vilib_root, _userlib_root
    _vilib_root = None
    _userlib_root = None


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


def _hash12(text: str) -> str:
    """First 12 hex chars of ``sha256(text)`` — a short, stable namespace id."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _rel_under(child: Path, parent: Path | None) -> Path | None:
    """``child`` relative to ``parent`` if it is under it, else ``None``.

    ``parent`` is already resolved (see ``set_extraction_roots``); ``child`` is
    resolved here so the prefix test is spelling-independent.
    """
    if parent is None:
        return None
    try:
        return child.resolve().relative_to(parent)
    except ValueError:
        return None


def _classify(vi_path: Path) -> tuple[Path, str]:
    """Map ``vi_path`` to ``(cache_dir, source_label)`` under the global cache.

    The cache dir is the VI's PARENT directory mirrored under a namespace, so
    sibling VIs share a dir and each keeps its own ``<stem>_BDHb.xml`` etc.
    ``source_label`` is the VI relative to whichever root matched (recorded in
    the meta for debugging). Prefix-matching the resolved path against the run's
    real roots — not substring markers — is what puts vendored OpenG under the
    project (correct) instead of the shared tier (wrong).
    """
    resolved = vi_path.resolve()
    root = global_cache_root()

    rel = _rel_under(resolved, _vilib_root)
    if rel is not None:
        ns = _hash12(str(_vilib_root))
        return root / "shared" / "vilib" / ns / rel.parent, str(rel)

    rel = _rel_under(resolved, _userlib_root)
    if rel is not None:
        ns = _hash12(str(_userlib_root))
        return root / "shared" / "userlib" / ns / rel.parent, str(rel)

    project = _project_root_for(resolved)
    if project is not None:
        proj_abs = project.resolve()
        rel = resolved.relative_to(proj_abs)
        ns = _hash12(str(proj_abs))
        return root / "projects" / ns / rel.parent, str(rel)

    ns = _hash12(str(resolved))
    return root / "adhoc" / ns, resolved.name


def _cache_target(vi_path: Path) -> Path:
    """The per-VI cache directory for ``vi_path`` (created on demand)."""
    target, _ = _classify(vi_path)
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
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    st = vi_path.stat()
    if meta.get("size") == st.st_size and meta.get("mtime") == st.st_mtime:
        return True
    if meta.get("sha256") == _sha256_file(vi_path):
        meta["mtime"] = st.st_mtime
        meta["size"] = st.st_size
        try:
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            pass
        return True
    return False


def _write_cache_meta(vi_path: Path, meta_path: Path) -> None:
    st = vi_path.stat()
    _, source = _classify(vi_path)
    meta_path.write_text(json.dumps({
        "source": source,
        "sha256": _sha256_file(vi_path),
        "mtime": st.st_mtime,
        "size": st.st_size,
        "tool": "pylabview",
        "extracted_at": time.time(),
    }), encoding="utf-8")


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

    ``<global-cache>/llb/<stem>_<hash12>`` where hash12 is the first 12 hex
    chars of SHA-256 over the resolved absolute path.
    """
    digest = _hash12(str(llb_path))
    return global_cache_root() / "llb" / f"{llb_path.stem}_{digest}"


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


