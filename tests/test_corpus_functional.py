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


def test_boolean_trigger_rising_and_falling_edges_are_independent() -> None:
    """Boolean Trigger holds the previous input in a shift register and reports a
    rising edge (F->T) and a falling edge (T->F) on SEPARATE outputs, both
    suppressed on the first call. Guards the compound-arithmetic output-naming
    collision where the two edge results shared one variable (`should_stop`) and
    the second assignment clobbered the first, making rising_edge == falling_edge.

    Driven in-process (not via _run_leaf) because edge detection needs several
    calls with the module's first-call / previous-state globals set between them.
    """
    vi = CORPUS / "boolean/boolean.llb/Boolean Trigger__ogtk.vi"
    if not vi.exists():
        pytest.skip("corpus VI missing: Boolean Trigger")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    ctx = graph.get_vi_context(name)
    code = build_module(ctx, name, graph=graph)
    ns: dict = {}
    exec(compile(code, "<boolean_trigger>", "exec"), ns)  # noqa: S102
    fn = ns[to_function_name(ctx.name)]

    def set_state(first_call: bool, prev: bool) -> None:
        for k in list(ns):
            if k.startswith("_lv_first_call"):
                ns[k] = first_call
            elif k.startswith("_lv_state"):
                ns[k] = prev

    set_state(first_call=True, prev=False)
    r = fn(True)  # first call: both edges suppressed
    assert (r.rising_edge, r.falling_edge) == (False, False)
    set_state(first_call=False, prev=False)
    r = fn(True)  # F -> T: rising only
    assert (r.rising_edge, r.falling_edge) == (True, False)
    set_state(first_call=False, prev=True)
    r = fn(False)  # T -> F: falling only
    assert (r.rising_edge, r.falling_edge) == (False, True)


def test_case_default_frame_on_integer_selector() -> None:
    """An integer-selector case has an infinite domain, so it ALWAYS has a
    default frame. MD5's unrecoverable-character padding gates on
    (length mod block), with frame 0 = aligned (no pad) and a "1, Default" frame
    that pads by (block - remainder) for EVERY other remainder. The default frame
    must compile to `case _`, or a remainder like 5 falls through and never pads.
    Pads 'hello' (len 5) to the 64-byte block: 5 + (64-5) zeros."""
    r = _run_leaf(
        "md5/md5.llb/MD5 Unrecoverable character padding__ogtk.vi", "hello", 64
    )
    assert len(r.padded_message) == 64
    assert r.padded_message[:5] == "hello"
    assert r.padded_message[5:] == "\x00" * 59


def test_unwired_array_input_defaults_to_empty() -> None:
    """An array input left unwired defaults to None and is normalized to [] at
    the top of the function (LabVIEW's unwired array == empty array), so the body
    iterates it instead of crashing on None. 1D Array to String joins its input
    with a delimiter -> '' for the empty default, 'a,b,c' for a real array."""
    empty = _run_leaf("string/string.llb/1D Array to String__ogtk.vi")
    assert empty.delimited_string == ""
    joined = _run_leaf(
        "string/string.llb/1D Array to String__ogtk.vi", ["a", "b", "c"], ","
    )
    assert joined.delimited_string == "a,b,c"


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


def test_conditional_auto_indexing_tunnel_filters_by_boolean_array() -> None:
    """Conditional Auto-Indexing Tunnel keeps array_in[i] where the boolean
    elements_to_keep[i] is True. Exercises element-wise broadcast of the
    conversion chain (Boolean To (0,1) -> To U32 -> Add Array Elements) over a
    boolean-array input, which used to crash with 'int' object is not iterable."""
    r = _run_pkg(
        "array/array.llb/Conditional Auto-Indexing Tunnel (I32)__ogtk.vi",
        elements_to_keep=[True, False, True],
        array_in=[10, 20, 30],
    )
    assert list(r.filtered_array_out) == [10, 30]
    r2 = _run_pkg(
        "array/array.llb/Conditional Auto-Indexing Tunnel (I32)__ogtk.vi",
        elements_to_keep=[False, True, True, False],
        array_in=[1, 2, 3, 4],
    )
    assert list(r2.filtered_array_out) == [2, 3]


def test_search_1d_array_collects_all_match_indices() -> None:
    """Search 1D Array walks the array from start_index, appending each match's
    index to an accumulator shift register and advancing a start-index shift
    register past the last hit, until no match remains. Guards against the
    dropped-shift-register-feedback regression where the loop returned an empty
    list because the accumulator/back-edge inside the case were never wired."""
    r = _run_pkg(
        "array/array.llb/Search 1D Array (I32)__ogtk.vi",
        array=[5, 3, 5, 7],
        element_data=5,
        start_index_0=0,
    )
    assert list(r.indices_of_elements) == [0, 2]
    r_none = _run_pkg(
        "array/array.llb/Search 1D Array (I32)__ogtk.vi",
        array=[1, 2, 3],
        element_data=9,
        start_index_0=0,
    )
    assert list(r_none.indices_of_elements) == []
    r_off = _run_pkg(
        "array/array.llb/Search 1D Array (I32)__ogtk.vi",
        array=[5, 3, 5, 7],
        element_data=5,
        start_index_0=1,
    )
    assert list(r_off.indices_of_elements) == [2]


def test_sort_1d_array_returns_values_and_original_pointers() -> None:
    """Sort 1D Array pairs each element with its original index, sorts the pairs,
    optionally reverses (order=1), then splits back into sorted values and the
    original-position pointers. Guards the shift-register/case-tunnel regression
    where the value-collecting loop body was emitted as a bare ``pass``."""
    r_asc = _run_pkg(
        "array/array.llb/Sort 1D Array (I32)__ogtk.vi",
        array_in=[30, 10, 20],
        order=0,
    )
    assert list(r_asc.sorted_array_out) == [10, 20, 30]
    assert list(r_asc.sorted_pointers) == [1, 2, 0]
    r_desc = _run_pkg(
        "array/array.llb/Sort 1D Array (I32)__ogtk.vi",
        array_in=[30, 10, 20],
        order=1,
    )
    assert list(r_desc.sorted_array_out) == [30, 20, 10]
    assert list(r_desc.sorted_pointers) == [0, 2, 1]


def test_remove_duplicates_keeps_first_and_records_removed_indices() -> None:
    """Remove Duplicates from 1D Array keeps the first occurrence of each value
    (appending unseen values to the output shift register) and records the input
    index of every dropped duplicate. Exercises the ``case -1`` selector
    correlation (not-found -> append) and both shift-register back-edges."""
    r = _run_pkg(
        "array/array.llb/Remove Duplicates from 1D Array (DBL)__ogtk.vi",
        input_array=[1.0, 2.0, 1.0, 3.0, 2.0],
    )
    assert list(r.output_array) == [1.0, 2.0, 3.0]
    assert list(r.indices_of_removed_elements) == [2, 4]
    r_none = _run_pkg(
        "array/array.llb/Remove Duplicates from 1D Array (DBL)__ogtk.vi",
        input_array=[1.0, 2.0, 3.0],
    )
    assert list(r_none.output_array) == [1.0, 2.0, 3.0]
    assert list(r_none.indices_of_removed_elements) == []


def test_md5_message_digest_matches_known_vectors() -> None:
    """OpenG's MD5 computed end-to-end (padding, the 64-step F/G/H/I round loop
    over 16-word blocks, and the element-wise block-state add) must reproduce the
    RFC 1321 test vectors. This is the whole-algorithm guard: it exercises Rotate,
    Type Cast, byte-faithful string constants, the integer-case default frame, the
    Index Array output naming, the list-concat-vs-elementwise-add distinction, and
    array-typed shift-register tracking -- any regression in those breaks it."""
    import hashlib

    for msg in ("", "abc", "message digest"):
        r = _run_pkg(
            "md5/md5.llb/MD5 Message Digest (Binary String)__ogtk.vi", message=msg
        )
        got = r.md5_message_digest.encode("latin-1").hex()
        assert got == hashlib.md5(msg.encode()).hexdigest(), f"MD5({msg!r})"
