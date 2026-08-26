"""Coverage for ``lvkit.list_deps`` — the web (Pyodide) extension's staging
closure primitive (fix/web-lazy-staging).

The web extension mirrors a VI's transitive dependency closure into Pyodide's
``/proj`` filesystem before rendering (instead of the old whole-workspace
``.vi``-only walk). ``list_deps`` returns ONE file's DIRECT deps; the JS side
(``editors/vscode/web/extension.js``) BFS-walks it. These tests cover, all
against the local-only JKI VI Tester corpus (never the network, never a real
VS Code host):

1. ``list_deps`` on a known VI returns its expected direct deps.
2. A transitive-closure helper (mirroring the JS BFS) stays inside the
   workspace tree.
3. THE KEY TEST: rendering from a closure-staged temp tree (mimicking
   ``/proj`` — copy in ONLY the closure files) is byte-identical to
   rendering the same VI from the full corpus with real search paths —
   for VIs with ``.ctl`` and ``.lvclass`` dependencies specifically. If this
   ever fails, the closure is incomplete (``list_deps`` has drifted from
   ``_load_dependency``), not a rendering nondeterminism.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from lvkit.list_deps import list_deps
from lvkit.render import render_vi_body

SAMPLES = Path(".lvkit/cache/samples/JKI-VI-Tester/source")


def _skip_if_missing(*paths: Path) -> None:
    for p in paths:
        if not p.exists():
            pytest.skip(f"Sample not available: {p}")


def _rel_set(paths: list[str], root: Path) -> set[str]:
    return {str(Path(p).resolve().relative_to(root.resolve())) for p in paths}


# ---------------------------------------------------------------------------
# 1. list_deps() on a single VI
# ---------------------------------------------------------------------------


@pytest.mark.needs_samples
def test_list_deps_run_vi_direct_deps():
    """``Classes/TestCase/run.vi`` calls JKI-Reuse error-handling SubVIs, is a
    method of TestCase.lvclass (itself a dep), calls into TestResult.lvclass
    (+ its methods) and a Utilities VI, and references its own private
    ``method--Enum.ctl`` typedef. Assert the expected files are all present,
    without over-constraining to an exact/ordered list (the underlying VIVI/
    type_map walk order isn't a public contract)."""
    entry = SAMPLES / "Classes/TestCase/run.vi"
    _skip_if_missing(entry)

    deps = list_deps(entry, search_paths=[SAMPLES])
    rels = _rel_set(deps, SAMPLES)

    expected_present = {
        "Classes/TestCase/TestCase.lvclass",
        "Classes/TestResult/TestResult.lvclass",
        "Classes/TestCase/private/method--Enum.ctl",
        "Utilities/Get LVClass Name from TD.vi",
    }
    missing = expected_present - rels
    assert not missing, f"list_deps missed expected deps: {missing} (got {rels})"

    # Every returned path must be a real, existing file inside the corpus.
    for d in deps:
        p = Path(d)
        assert p.is_file(), d
        assert p.is_relative_to(SAMPLES.resolve()), d


@pytest.mark.needs_samples
def test_list_deps_skips_vilib_userlib():
    """<vilib>/<userlib> refs are never resolved (never in the workspace) —
    every returned dep must be a real corpus file, never a stub/library path."""
    entry = SAMPLES / "Classes/TestCase/run.vi"
    _skip_if_missing(entry)
    deps = list_deps(entry, search_paths=[SAMPLES])
    assert deps, "expected at least one resolved dependency"
    for d in deps:
        assert "vi.lib" not in d.replace("\\", "/").lower()


@pytest.mark.needs_samples
def test_list_deps_lvclass_and_ctl():
    """``.lvclass``/``.ctl`` inputs (not just ``.vi``) resolve their own deps
    (private-data control / parent class / methods; nested type refs)."""
    lvclass = SAMPLES / "Tests/Parentheses (Valid)/Parentheses (Valid).lvclass"
    _skip_if_missing(lvclass)
    deps = list_deps(lvclass, search_paths=[SAMPLES])
    rels = _rel_set(deps, SAMPLES)
    assert any(r.endswith("setUp.vi") for r in rels), rels
    assert any(r.endswith("tearDown.vi") for r in rels), rels

    ctl = SAMPLES / "Classes/TestResult/resultStatusChanged--Cluster.ctl"
    _skip_if_missing(ctl)
    ctl_deps = list_deps(ctl, search_paths=[SAMPLES])
    # A cluster typedef's own deps (if any) must still be real, existing files.
    for d in ctl_deps:
        assert Path(d).is_file()


# ---------------------------------------------------------------------------
# 2. Transitive closure (mirrors the JS BFS)
# ---------------------------------------------------------------------------


def _transitive_closure(entry: Path, root: Path) -> set[Path]:
    """BFS over ``list_deps``, exactly mirroring the web extension's staging
    loop (``stageWorkspaceSubtree`` in ``editors/vscode/web/extension.js``):
    read one level, discover the next from what was just staged, repeat."""
    frontier = [entry.resolve()]
    staged: set[Path] = {entry.resolve()}
    while frontier:
        next_frontier: list[Path] = []
        for f in frontier:
            for dep_str in list_deps(f, search_paths=[root]):
                dep = Path(dep_str).resolve()
                if dep not in staged:
                    staged.add(dep)
                    next_frontier.append(dep)
        frontier = next_frontier
    return staged


@pytest.mark.needs_samples
def test_transitive_closure_stays_in_workspace_and_is_bounded():
    entry = SAMPLES / "Classes/TestCase/run.vi"
    _skip_if_missing(entry)
    closure = _transitive_closure(entry, SAMPLES)

    assert entry.resolve() in closure
    for p in closure:
        assert p.is_relative_to(SAMPLES.resolve()), p
        assert p.is_file(), p
    # Nowhere near the old 400-file whole-workspace cap — a real closure for
    # one class method is dozens to low hundreds of files, not the whole repo.
    assert len(closure) < 400


# ---------------------------------------------------------------------------
# 3. THE KEY TEST — closure-staged render == full-corpus (desktop) render
# ---------------------------------------------------------------------------


def _stage_closure(entry: Path, root: Path, mirror_dir: Path) -> Path:
    """Copy ONLY the transitive closure into ``mirror_dir``, preserving each
    file's path relative to ``root`` — exactly the ``/proj/<rel>`` layout the
    web extension stages into Pyodide's virtual FS."""
    closure = _transitive_closure(entry, root)
    for p in closure:
        rel = p.relative_to(root.resolve())
        dest = mirror_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    return mirror_dir / entry.relative_to(root)


_NODE_ID_RE = re.compile(r"lv-[a-z0-9-]*-vi", re.IGNORECASE)


def _normalize(svg: str) -> str:
    """Strip the path-derived DOM id (differs between the full-corpus path
    and the staged mirror path) — mirrors
    ``editors/vscode/build/check-web-parity.sh``'s ``norm()``."""
    return _NODE_ID_RE.sub("lv-ID", svg)


# VIs with a mix of dependency shapes: a class method calling into ANOTHER
# class's methods + a private .ctl typedef (run.vi); a VI referencing two
# classes plus their .ctl-typed private data (defaultTestResult.vi); a plain
# class-typed leaf call (testMethod.vi); a VI with only a .ctl dep
# (wasSuccessful.vi). Each has a corpus-UNIQUE bare filename — some "run.vi"/
# "testExample.vi"-named siblings expose an unrelated, pre-existing
# ambiguous-bare-name bug in ``render_vi_file_titled``/``resolve_vi_name``
# (multiple same-named VIs on disk; it can title/render the wrong one) that
# has nothing to do with staging closures — this suite steers clear of it so
# it tests ONLY what it's here to test.
PARITY_CASES = [
    "Classes/TestCase/run.vi",
    "Classes/TestCase/defaultTestResult.vi",
    "Classes/TestCase/testMethod.vi",
    "Classes/TestResult/wasSuccessful.vi",
]


@pytest.mark.needs_samples
@pytest.mark.parametrize("rel", PARITY_CASES)
def test_closure_staged_render_matches_desktop(rel, tmp_path):
    entry = SAMPLES / rel
    _skip_if_missing(entry)

    native_svg = render_vi_body(entry, fmt="svg", search_paths=[SAMPLES])
    assert native_svg is not None, f"native render declined for {rel}"

    mirror_dir = tmp_path / "proj_mirror"
    mirror_dir.mkdir()
    staged_entry = _stage_closure(entry, SAMPLES, mirror_dir)
    staged_svg = render_vi_body(staged_entry, fmt="svg", search_paths=[mirror_dir])
    assert staged_svg is not None, f"closure-staged render declined for {rel}"

    assert _normalize(native_svg) == _normalize(staged_svg), (
        f"web/closure render diverges from desktop for {rel} — "
        "the staging closure is missing a file the desktop loader used"
    )
