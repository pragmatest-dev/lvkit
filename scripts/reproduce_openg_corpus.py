#!/usr/bin/env python3
"""Reproduce the OpenG Toolkit ``File Group N/`` extracted-VI corpus.

lvkit's tests load OpenG VIs directly out of ``samples/OpenG/extracted/File
Group 0/user.lib/_OpenG.lib/<lib>/<lib>.llb/<name>__ogtk.vi`` (plus sibling
``*_BDHb.xml`` etc). This script reproduces that exact layout from scratch:

1. Download each OpenG library package (a ``.vip``, which is a zip) from its
   canonical SourceForge project file listing.
2. Unzip every package into the same destination directory. Every OpenG
   package puts its real VI content under
   ``File Group 0/user.lib/_OpenG.lib/<lib>/<lib>.llb/`` (verified per
   package below) so unzipping them all into one directory merges cleanly —
   no filename collisions across libraries.
3. Some ``<lib>.llb`` entries unzip straight to a plain directory of raw
   ``.vi`` files (most packages); a few unzip to a single binary LLB
   container file (LabVIEW's own archive format). For the latter, use
   ``lvkit.extractor.extract_llb`` to explode it into the same directory
   shape as the former, so downstream logic is uniform.
4. For every ``.vi`` file now sitting in a ``.llb`` directory, run
   ``lvkit.extractor.extract_vi_xml`` to produce the sibling
   ``<stem>_BDHb.xml`` / ``_FPHb.xml`` / ``.xml`` files the tests load —
   one VI at a time (memory-flat; never parse the whole corpus at once).

Every package below ships its own top-level ``license`` file containing
OpenG's standard BSD-3-Clause text (verified by hand — see
docs/test-corpus-sources.md). Versions are the highest publicly listed on
the ``opengtoolkit`` SourceForge project as of 2026-07.

Usage:
    python3 scripts/reproduce_openg_corpus.py <dest_dir>

Called by scripts/pull_samples.sh; can also be run standalone.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lvkit.extractor import extract_llb, extract_vi_xml  # noqa: E402

SOURCEFORGE_BASE = "https://sourceforge.net/projects/opengtoolkit/files"

# (lib folder, version dir, filename) — one entry per OpenG library package.
OPENG_VIP_SPECS = [
    ("lib_appcontrol", "4.x", "oglib_appcontrol-4.1.0.7.vip"),
    ("lib_array", "4.x", "oglib_array-4.1.1.14.vip"),
    ("lib_boolean", "4.x", "oglib_boolean-4.0.0.7.vip"),
    ("lib_buttons", "4.x", "oglib_buttons-4.0.0.7.vip"),
    ("lib_comparison", "4.x", "oglib_comparison-4.0.0.3.vip"),
    ("lib_dictionary", "4.x", "oglib_dictionary-4.0.0.4.vip"),
    ("lib_error", "4.x", "oglib_error-4.2.0.23.vip"),
    ("lib_file", "4.x", "oglib_file-4.0.1.22.vip"),
    ("lib_largefile", "4.x", "oglib_largefile-4.0.0.3.vip"),
    ("lib_lvdata", "4.x", "oglib_lvdata-4.2.0.21.vip"),
    ("lib_md5", "4.x", "oglib_md5-4.1.1.10.vip"),
    ("lib_msgqueue", "4.x", "oglib_msgqueue-4.0.0.15.vip"),
    ("lib_numeric", "4.x", "oglib_numeric-4.1.0.8.vip"),
    ("lib_picture", "4.x", "oglib_picture-4.0.0.13.vip"),
    ("lib_string", "4.x", "oglib_string-4.2.0.13.vip"),
    ("lib_time", "4.x", "oglib_time-4.0.1.3.vip"),
    ("lib_variantconfig", "4.x", "oglib_variantconfig-4.0.0.5.vip"),
]


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "lvkit-corpus-pull/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)


def _fetch_and_unzip(dest_root: Path, tmp_dir: Path) -> None:
    for lib_folder, version_dir, filename in OPENG_VIP_SPECS:
        url = f"{SOURCEFORGE_BASE}/{lib_folder}/{version_dir}/{filename}/download"
        vip_path = tmp_dir / filename
        print(f"fetch  {filename} <- {url}")
        _download(url, vip_path)
        with zipfile.ZipFile(vip_path) as zf:
            zf.extractall(dest_root)  # noqa: S202
        vip_path.unlink()


def _materialize_llb_dir(llb_path: Path) -> None:
    """Ensure ``llb_path`` is a directory of raw ``.vi`` files.

    Most OpenG packages already unzip that way. A few (e.g. ``buttons``)
    unzip to a single binary LLB container file — explode those via
    ``extract_llb`` into the same directory shape.
    """
    if llb_path.is_dir():
        return
    cache_dir = extract_llb(llb_path)
    llb_path.unlink()
    llb_path.mkdir()
    for member in cache_dir.iterdir():
        if member.name == ".extracted":
            continue
        shutil.copy2(member, llb_path / member.name)


def _extract_all_vi_xml(dest_root: Path) -> int:
    """Walk every ``*.llb`` dir and extract each member VI's XML into the
    project-local ``.lvkit/cache/`` (NOT beside the ``.vi``).

    One VI at a time (memory-flat) — never parse the whole corpus at once.
    """
    count = 0
    for llb_path in sorted(dest_root.glob("**/*.llb")):
        _materialize_llb_dir(llb_path)
        for vi_path in sorted(llb_path.glob("*.vi")):
            try:
                extract_vi_xml(vi_path)  # -> .lvkit/cache/extracted/ (default)
                count += 1
            except RuntimeError as exc:
                print(
                    f"  warn: extraction failed for {vi_path.name}: {exc}",
                    file=sys.stderr,
                )
    return count


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: reproduce_openg_corpus.py <dest_dir>", file=sys.stderr)
        return 2
    dest_root = Path(sys.argv[1]).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="lvkit-openg-") as tmp:
        _fetch_and_unzip(dest_root, Path(tmp))

    n = _extract_all_vi_xml(dest_root)
    print(f"done: extracted XML for {n} VIs under {dest_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
