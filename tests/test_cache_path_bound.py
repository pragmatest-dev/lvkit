"""Bounded per-VI cache slots + retained VI-path identity (issue #97).

The managed cache used to reproduce a VI's full owner-relative directory
hierarchy under the cache root and embed the source stem in every artifact
name, so a deep source tree or a long VI filename could push the final cache
path past Windows' MAX_PATH even with a short ``LVKIT_CACHE_DIR``. The fix
collapses the whole owner-relative path to one bounded ``vi-<hash>`` slot dir
(``cache_paths._vi_slot``) and uses fixed short artifact names
(``vi_BDHb.xml`` / ``vi.<ext>``) — for extract, render AND diff.

The load-bearing invariant this must NOT break: a VI's identity is its SOURCE
PATH (the graph-node key), never the cache filename. These tests prove both the
bounding AND that every graph key stays the real source path — including the
confluence case of two same-named VIs at different paths.

The autouse ``_hermetic_cache`` fixture (conftest) points ``LVKIT_CACHE_DIR`` at
a per-test tmp dir, so nothing here touches the real ``~/.lvkit/cache``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvkit import cache_paths, extractor, output_cache
from lvkit.graph import InMemoryVIGraph
from lvkit.index.build import build_index
from lvkit.index.store import db_path as index_db_path
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index
from lvkit.load_mode import LoadMode

_CORPUS = Path(__file__).parent / "corpus" / "issues"

# Nesting depth whose owner-relative path string exceeds 600 chars (each segment
# is short, so no single component breaks the 255-char POSIX filename limit).
_DEEP = 90


def _mark_project(root: Path) -> None:
    (root / ".git").mkdir(parents=True, exist_ok=True)


def _touch_vi(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _deep_rel(levels: int) -> Path:
    """An owner-relative path whose string is long via NESTING (each segment is
    short so no single component exceeds the 255-char POSIX filename limit)."""
    return Path(*[f"lvl{i:03d}" for i in range(levels)]) / "leaf.vi"


# ── depth-independence: the cache path no longer grows with source depth ──────


class TestBoundedDepth:
    def test_extract_path_length_is_independent_of_source_depth(
        self, tmp_path: Path
    ) -> None:
        """A shallow VI and a very deep VI under the SAME project get extract
        cache paths of EQUAL length below the cache root — proof that source
        depth is no longer an input to the path (only the fixed-width hash is)."""
        proj = tmp_path / "proj"
        _mark_project(proj)
        shallow = _touch_vi(proj / "a.vi")
        deep = _touch_vi(proj / _deep_rel(_DEEP))  # rel string > 600 chars
        assert len(str(_deep_rel(_DEEP))) > 600

        s_dir, _, _ = cache_paths.classify(shallow, "extract")
        d_dir, _, _ = cache_paths.classify(deep, "extract")

        # Slot dir is the bounded ``vi-<16hex>`` in both cases.
        assert s_dir.name.startswith("vi-") and len(s_dir.name) == len("vi-") + 16
        assert d_dir.name.startswith("vi-") and len(d_dir.name) == len("vi-") + 16
        # Full artifact paths (dir + fixed name) are the SAME length — the deep
        # VI's path is not longer by its depth.
        s_art = s_dir / "vi_BDHb.xml"
        d_art = d_dir / "vi_BDHb.xml"
        assert len(str(s_art)) == len(str(d_art))

    @pytest.mark.parametrize("kind", ["extract", "render", "diff"])
    def test_all_source_shaped_kinds_are_depth_bounded(
        self, tmp_path: Path, kind: str
    ) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        shallow = _touch_vi(proj / "a.vi")
        deep = _touch_vi(proj / _deep_rel(_DEEP))
        s_dir, _, _ = cache_paths.classify(shallow, kind)
        d_dir, _, _ = cache_paths.classify(deep, kind)
        # The cache DIR below the root is bounded (a fixed set of short segments
        # + one hash), regardless of the 600+ char source depth.
        assert d_dir.name == d_dir.name  # exists / no exception
        assert s_dir.name.startswith("vi-") and d_dir.name.startswith("vi-")
        # Same length below the shared slug: only the per-VI hash differs.
        assert len(str(s_dir)) == len(str(d_dir))

    def test_final_extract_path_fits_max_path_under_a_short_root(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Even a 600+ char owner-relative path yields an extract artifact path
        under the Windows MAX_PATH ceiling when the cache root is short."""
        short_root = tmp_path / "c"
        monkeypatch.setattr(cache_paths, "global_cache_root", lambda: short_root)
        proj = tmp_path / "proj"
        _mark_project(proj)
        deep = _touch_vi(proj / _deep_rel(_DEEP))
        d_dir, _, _ = cache_paths.classify(deep, "extract")
        art = d_dir / "vi_BDHb.xml"
        assert len(str(art)) < extractor._WIN_MAX_PATH


# ── per-VI slot: distinct VIs never collide, the same VI is stable ────────────


class TestSlotIdentity:
    def test_distinct_relpaths_get_distinct_slots(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        a = _touch_vi(proj / "dirA" / "Do.vi")
        b = _touch_vi(proj / "dirB" / "Do.vi")  # SAME basename, different path
        da, _, _ = cache_paths.classify(a, "extract")
        db, _, _ = cache_paths.classify(b, "extract")
        assert da != db  # same-named VIs never share a slot

    def test_same_vi_is_deterministic(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        vi = _touch_vi(proj / "sub" / "x.vi")
        first, _, _ = cache_paths.classify(vi, "extract")
        second, _, _ = cache_paths.classify(vi, "extract")
        assert first == second

    def test_vi_slot_is_bounded_and_prefixed(self) -> None:
        slot = cache_paths._vi_slot(_deep_rel(80))
        assert slot.startswith("vi-")
        assert len(slot) == len("vi-") + 16


# ── fixed short artifact names in the managed cache ──────────────────────────


class TestFixedNames:
    def test_managed_extract_uses_fixed_names_and_records_real_source(self) -> None:
        vi = (_CORPUS / "29" / "Test LVKit" / "Lib1" / "Class" / "Do.vi").resolve()
        bd_xml, fp_xml, main_xml = extractor.extract_vi_xml(vi)
        # Fixed short names, NOT the source stem — under a bounded ``vi-`` slot.
        assert bd_xml.name == "vi_BDHb.xml"
        assert bd_xml.parent.name.startswith("vi-")
        # The real source path is not lost: it's recorded in the meta sidecar.
        meta = json.loads((bd_xml.parent / "vi.meta.json").read_text(encoding="utf-8"))
        assert meta["source"].endswith("Do.vi")

    def test_caller_supplied_output_dir_keeps_the_real_stem(
        self, tmp_path: Path
    ) -> None:
        vi = (_CORPUS / "29" / "Test LVKit" / "Lib1" / "Class" / "Do.vi").resolve()
        out = tmp_path / "out"
        out.mkdir()
        bd_xml, _, _ = extractor.extract_vi_xml(vi, output_dir=out)
        # A caller-owned dir keeps the source stem (only the managed cache is fixed).
        assert bd_xml.name == "Do_BDHb.xml"


# ── render / diff slot names ─────────────────────────────────────────────────


class TestRenderDiffNames:
    def test_render_slot_name_is_fixed(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        vi = _touch_vi(proj / "x.vi")
        slot = output_cache.render_slot(vi, "svg")
        assert slot.name == "vi.svg"
        assert slot.parent.name.startswith("vi-")

    def test_diff_slot_name_is_fixed_plus_before_hash(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        before = tmp_path / "old.vi"
        before.write_bytes(b"BEFORE")
        after = _touch_vi(proj / "x.vi")
        slot = output_cache.diff_slot(before, after, "html")
        assert slot.name.startswith("vi.") and slot.name.endswith(".html")
        assert slot.parent.name.startswith("vi-")


class TestRenderDiffMetaPreservesSource:
    """The fixed ``vi.<ext>`` body name carries no source info by itself — the
    real VI path must survive in the meta sidecar, exactly as extraction's
    ``vi.meta.json`` already does (see ``TestFixedNames``). ``store_render``/
    ``store_diff`` must write a ``source`` field, not just a bounded slot name."""

    def test_render_meta_records_real_source(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        vi = _touch_vi(proj / "deep" / "sub" / "MyVI.vi")
        output_cache.store_render(vi, "svg", "opts", "1.0.0", "<svg/>")

        body_path, meta_path, source_label, _ = output_cache._render_paths(vi, "svg")
        assert body_path.name == "vi.svg"  # bounded name, no source info itself
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["source"] == source_label
        assert meta["source"] == str(Path("deep") / "sub" / "MyVI.vi")

    def test_diff_meta_records_real_source(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        before = tmp_path / "old.vi"
        before.write_bytes(b"BEFORE")
        after = _touch_vi(proj / "deep" / "sub" / "MyVI.vi")
        output_cache.store_diff(before, after, "html", "opts", "1.0.0", "<diff/>")

        body_path, meta_path, _, source_label, _ = output_cache._diff_paths(
            before, after, "html"
        )
        assert body_path.name.startswith("vi.")  # bounded name, no source info
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # The AFTER VI is the one path-addressed here — its real path is recorded.
        assert meta["source"] == source_label
        assert meta["source"] == str(Path("deep") / "sub" / "MyVI.vi")


# ── the load-bearing invariant: graph identity is the SOURCE PATH ─────────────


class TestGraphIdentityRetained:
    def test_subvi_keys_are_real_source_paths_not_cache_names(self) -> None:
        """Load a VI whose two SubVIs are same-named ``Do.vi`` at different
        paths. Every dep-graph node key must be the REAL absolute source path —
        never the fixed cache name (``vi.vi`` / ``vi_BDHb.xml``) — and the two
        ``Do.vi`` must be DISTINCT keys (identity is the path, not the name)."""
        root = (_CORPUS / "29" / "Test LVKit").resolve()
        entry = root / "Lib2" / "Class" / "Test.vi"
        graph = InMemoryVIGraph()
        graph.load_vi(entry, LoadMode.FULL, search_paths=[root])

        keys = list(graph._dep_graph.nodes)
        # No key leaks a cache-internal name.
        for k in keys:
            assert "vi_BDHb" not in k
            assert f"{'/'}extract{'/'}" not in k
            assert not k.endswith("/vi.vi") and not k.endswith("\\vi.vi")

        # Both same-named Do.vi loaded as DISTINCT real-path keys (confluence).
        do_keys = {k for k in keys if k.endswith("Do.vi")}
        assert len(do_keys) == 2
        assert any((root / "Lib1" / "Class" / "Do.vi").name in k for k in do_keys)
        # Each Do.vi key is the real absolute path that exists on disk.
        for k in do_keys:
            assert Path(k).exists()
        # The entry VI is keyed by its real path too.
        assert str(entry.resolve()) in keys

    def test_warm_reload_hits_cache_and_keeps_identity(self, monkeypatch) -> None:
        """A second extraction of the same VI is a CACHE HIT (no re-extraction —
        the performance guarantee) and the graph key stays the real path."""
        vi = (_CORPUS / "29" / "Test LVKit" / "Lib1" / "Class" / "Do.vi").resolve()

        calls = {"n": 0}
        real = extractor._extract_in_process

        def _counting(vp, od, stem):
            calls["n"] += 1
            return real(vp, od, stem)

        monkeypatch.setattr(extractor, "_extract_in_process", _counting)

        g1 = InMemoryVIGraph()
        g1.load_vi(vi, LoadMode.NONE)
        assert calls["n"] == 1  # cold: extracted once

        g2 = InMemoryVIGraph()
        g2.load_vi(vi, LoadMode.NONE)
        assert calls["n"] == 1  # warm: served from cache, NOT re-extracted

        assert str(vi) in g2._dep_graph.nodes


# ── the 4th cache kind (index) — structurally exempt, verify it stays so ──────


class TestIndexCacheUnaffectedByViSlot:
    """The index cache (``<cache>/projects/<slug>/index/<fp>/index.db``) is a
    SINGLE per-PROJECT SQLite file, never per-VI — ``index/store.py::db_path``
    never calls ``classify()``/``_vi_slot``, and every row's ``vi_path`` column
    is populated straight from the real filesystem path (``index/project.py``'s
    directory walk), never from an extraction-cache filename. So it was never at
    risk of the identity-loss #97 fixed for extract/render/diff — these tests
    prove it stays that way: a deeply-nested VI's real path survives the full
    build -> SQLite round trip, and the index DB's own path carries no
    ``vi-<hash>`` slot component."""

    def test_deep_vi_indexes_under_its_real_path(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        _mark_project(proj)
        deep_vi = proj / _deep_rel(_DEEP)
        deep_vi.parent.mkdir(parents=True, exist_ok=True)
        deep_vi.write_bytes(
            (_CORPUS / "29" / "Test LVKit" / "Lib1" / "Class" / "Do.vi").read_bytes()
        )

        result = build_index(proj, [deep_vi])
        real_path = str(deep_vi.resolve())
        by_path = {f.path: f for f in result.facts}
        assert real_path in by_path, (
            f"indexed path {sorted(by_path)} does not contain the real deep "
            f"source path {real_path!r} — identity was lost somewhere in the "
            "build (e.g. re-derived from a cache filename)"
        )

        # Round-trip through the SQLite index DB — the real path must survive.
        save_index(proj, result.facts)
        loaded = {f.path for f in load_index(proj)}
        assert real_path in loaded

    def test_index_db_path_has_no_per_vi_slot(self, tmp_path: Path) -> None:
        """The index DB is per-PROJECT, not per-VI — its own cache path must
        contain no ``vi-<hash>`` segment, whatever the VI count/depth is."""
        proj = tmp_path / "proj"
        _mark_project(proj)
        db = index_db_path(proj)
        assert not any(part.startswith("vi-") for part in db.parts)
        assert db.name == "index.db"
        # Deterministic: the same project always maps to the same DB file.
        assert index_db_path(proj) == db
