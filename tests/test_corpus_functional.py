"""Functional-correctness regression suite for deterministic codegen.

These tests GENERATE a corpus VI, EXECUTE the generated Python, and assert its
real output matches what the VI's block-diagram logic should produce — NOT a
structural/string check of the code. They are the guardrail for "don't break a
VI that already works": the generated implementation may change freely, but the
outcomes here must stay correct.

Run/skip as a block:
    uv run pytest -m functional            # only these
    uv run pytest -m 'not functional'      # everything else

Each `_run_leaf` VI must be a LEAF (no SubVI imports) so it execs standalone;
VIs with dependencies get a package-level case (see _run_pkg) once available.
"""

from __future__ import annotations

import importlib
import inspect
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.ast_utils import to_function_name
from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

CORPUS = Path(".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib")
SEARCH = Path(".lvkit/cache/samples/OpenG/extracted")
_GEN = Path("scripts/generate_python.py")


def _run_leaf(vi_rel: str, *args: object) -> Any:
    """Generate a leaf VI, exec it, and call its entry function with args."""
    vi = CORPUS / vi_rel
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi_rel}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    ctx = graph.get_vi_context(name)
    code = build_module(ctx, name, graph=graph)
    if "from .." in code:
        pytest.skip(f"{vi_rel} has SubVI imports; not a leaf (needs package case)")
    ns: dict = {}
    exec(compile(code, f"<{vi_rel}>", "exec"), ns)  # noqa: S102
    func_name = to_function_name(ctx.name)
    fn = ns.get(func_name)
    assert callable(fn), f"entry function {func_name} not defined"
    return fn(*args)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.replace("__ogtk.vi", "").lower())


def _run_pkg(vi_rel: str, **kwargs: object) -> Any:
    """Generate a VI *with its SubVI dependencies* as a package (via the real
    pipeline, in a subprocess), import the entry module, and call the entry
    function with kwargs. This is the counterpart to ``_run_leaf`` for VIs that
    emit relative imports — the bulk of OpenG. Kwargs (not positional) because a
    poly-wrapped entry's parameter order isn't guaranteed."""
    vi = CORPUS / vi_rel
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi_rel}")
    outdir = Path(tempfile.mkdtemp(prefix=f"lvkit_pkg_{_slug(vi.name)}_"))
    proc = subprocess.run(
        [sys.executable, str(_GEN), str(vi), "-o", str(outdir),
         "--search-path", str(SEARCH)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert "error: 0" in proc.stdout, (
        f"generation failed:\n{proc.stdout}\n{proc.stderr}"
    )
    pkgs = [p for p in outdir.iterdir() if p.is_dir()]
    assert pkgs, f"no package emitted in {outdir}"
    pkg = pkgs[0]
    tgt = _slug(vi.name)
    mods = list((pkg / "openg").glob("*.py")) if (pkg / "openg").exists() else []
    best = next(
        (m for m in mods if m.stem != "__init__" and tgt in _slug(m.stem)), None
    )
    assert best is not None, f"no entry module matching {tgt} in {pkg}"
    sys.path.insert(0, str(outdir))
    try:
        modname = f"{pkg.name}.openg.{best.stem}"
        mod = importlib.import_module(modname)
        fn = next(
            (o for n, o in inspect.getmembers(mod, inspect.isfunction)
             if o.__module__ == modname and tgt in _slug(n)),
            None,
        )
        assert fn is not None, f"no entry function matching {tgt} in {modname}"
        return fn(**kwargs)
    finally:
        # Drop this package's modules + path entry so a later VI's identically
        # named submodules don't resolve to this one, and remove the temp dir.
        if str(outdir) in sys.path:
            sys.path.remove(str(outdir))
        for mod_name in [m for m in sys.modules if m.startswith(pkg.name)]:
            del sys.modules[mod_name]
        shutil.rmtree(outdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Cases. Add one per VI as it is confirmed working; keep assertions on the
# VALUE, so a codegen change that alters structure but preserves behavior passes.
# --------------------------------------------------------------------------


def test_random_number_within_range_is_in_bounds() -> None:
    """low + rand*(high-low) must land in [low, high]. (Non-deterministic → range.)"""
    for _ in range(25):
        r = _run_leaf(
            "numeric/numeric.llb/Random Number - Within Range__ogtk.vi", 0.0, 10.0
        )
        assert 0.0 <= r.random_number <= 10.0


def test_string_to_character_array() -> None:
    """Loops i over the string, taking one char at a time — needs the loop index."""
    r = _run_leaf("string/string.llb/String to Character Array__ogtk.vi", "hello")
    assert r.character_array == ["h", "e", "l", "l", "o"]


def test_strip_path_traditional() -> None:
    """Split a path into its last component (name) and its parent (stripped)."""
    r = _run_leaf("file/file.llb/Strip Path - Traditional__ogtk.vi", "/home/u/f.txt")
    assert r.name == "f.txt"
    assert str(r.stripped_path) == "/home/u"


def test_vi_library_appends_to_vilib() -> None:
    """Appends the input to the (path-constant) vi.lib fragment."""
    r = _run_leaf("file/file.llb/VI Library__ogtk.vi", "proj")
    assert str(r.vi_library__relative) == "vi.lib/proj"


def test_reorder_1d_array_by_indices() -> None:
    """array (passthrough tunnel, whole) reordered by indices (indexing tunnel):
    out[i] = array[indices[i]]. Guards the input-tunnel indexing-vs-passthrough
    distinction."""
    r = _run_leaf(
        "array/array.llb/Reorder 1D Array2 (I32)__ogtk.vi", [10, 20, 30, 40], [3, 1, 0]
    )
    assert r.reordered_array == [40, 20, 10]


def test_reorder_1d_array_dbl() -> None:
    r = _run_leaf(
        "array/array.llb/Reorder 1D Array2 (DBL)__ogtk.vi", [1.5, 2.5, 3.5], [2, 0, 1]
    )
    assert r.reordered_array == [3.5, 1.5, 2.5]


def test_reorder_1d_array_string() -> None:
    r = _run_leaf(
        "array/array.llb/Reorder 1D Array2 (String)__ogtk.vi",
        ["a", "b", "c"],
        [2, 1, 0],
    )
    assert r.reordered_array == ["c", "b", "a"]


def test_empty_1d_array() -> None:
    """len(array) == 0."""
    assert _run_leaf("array/array.llb/Empty 1D Array (I32)__ogtk.vi", []).empty_array
    got = _run_leaf("array/array.llb/Empty 1D Array (I32)__ogtk.vi", [1, 2])
    assert got.empty_array is False


def test_reorder_1d_array_with_pointers() -> None:
    """Reorder 1D Array (the sort-pointer variant) reorders + passes pointers."""
    r = _run_leaf(
        "array/array.llb/Reorder 1D Array (I32)__ogtk.vi", [10, 20, 30], [2, 0, 1]
    )
    assert r.sorted_array_out == [30, 10, 20]
    assert r.sorted_pointers_out == [2, 0, 1]


def test_build_path_traditional() -> None:
    """base path + a name -> base/name (single-name join)."""
    r = _run_leaf(
        "file/file.llb/Build Path - Traditional__ogtk.vi", "/home/u", "f.txt"
    )
    assert str(r.appended_path) == "/home/u/f.txt"


def test_build_path_file_names_array() -> None:
    """base path + [names] -> [base/name ...] (element-wise join over the array)."""
    r = _run_leaf(
        "file/file.llb/Build Path - File Names Array__ogtk.vi",
        "/home/u",
        ["a.txt", "b.txt"],
    )
    assert [str(p) for p in r.appended_path] == ["/home/u/a.txt", "/home/u/b.txt"]


def test_build_path_traditional_path() -> None:
    """The path-typed variant: base path + a name -> base/name."""
    r = _run_leaf(
        "file/file.llb/Build Path - Traditional - path__ogtk.vi", "/a/b", "c.txt"
    )
    assert str(r.appended_path) == "/a/b/c.txt"


def test_build_path_file_names_array_path() -> None:
    """The path-typed array variant: base + [names] -> [base/name ...]."""
    r = _run_leaf(
        "file/file.llb/Build Path - File Names Array - path__ogtk.vi",
        "/a/b",
        ["x", "y"],
    )
    assert [str(p) for p in r.appended_path] == ["/a/b/x", "/a/b/y"]


# --------------------------------------------------------------------------
# Package cases (VI + its SubVI deps), via _run_pkg. Kwargs, not positional.
# --------------------------------------------------------------------------


def test_index_array_elements() -> None:
    """Index Array Elements picks array[i] for each i in indices."""
    r = _run_pkg(
        "array/array.llb/Index Array Elements__ogtk.vi",
        array=[10, 20, 30],
        indices=[0, 2],
    )
    assert r.elements == [10, 30]
