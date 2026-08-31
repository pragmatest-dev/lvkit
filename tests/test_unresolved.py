"""Tests for `lvkit unresolved` — batch resolution-gap collection."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes.primitive import _emit_placeholder, _emit_unknown
from lvkit.codegen.nodes.subvi import _emit_vilib_resolution
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.models import Terminal
from lvkit.primitive_resolver import PrimitiveResolutionNeeded, ResolvedPrimitive
from lvkit.unresolved import (
    UnresolvedItem,
    collect_unresolved,
    format_unresolved_report,
)
from lvkit.vilib_resolver import VILibResolutionNeeded

# ---------------------------------------------------------------------------
# The sink: emit sites APPEND the constructed exception, then continue.
# ---------------------------------------------------------------------------


def test_sink_collects_unknown_primitive() -> None:
    """An unresolved_sink collects the primitive gap and does not raise."""
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Mystery",
        terminals=[Terminal(id="t0", index=0, direction="output", name="result")],
        prim_id=99999,
    )
    sink: list[Exception] = []
    ctx = CodeGenContext(
        soft_unresolved=True,
        unresolved_sink=sink,
        vi_name="Caller.vi",
    )
    # No raise — soft mode + sink continues.
    _emit_unknown(node, prim_id=99999, ctx=ctx)
    assert len(sink) == 1
    assert isinstance(sink[0], PrimitiveResolutionNeeded)
    assert sink[0].prim_id == "99999"


def test_sink_collects_unmapped_vilib() -> None:
    """An unresolved_sink collects the vi.lib gap and does not raise."""
    node = VINode(
        id="s",
        vi_path="test.vi",
        name="Imaginary VI.vi",
        terminals=[Terminal(id="t1", index=0, direction="input", name="in1")],
        node_type="iUse",
    )
    sink: list[Exception] = []
    ctx = CodeGenContext(
        soft_unresolved=True,
        unresolved_sink=sink,
        vi_name="Caller.vi",
    )
    _emit_vilib_resolution(node, ctx, vilib_vi=None)
    assert len(sink) == 1
    assert isinstance(sink[0], VILibResolutionNeeded)
    assert sink[0].vi_name == "Imaginary VI.vi"


def test_sink_collects_placeholder_primitive_tagged() -> None:
    """A placeholder primitive is collected and tagged distinctly from unknown."""
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Wait on Notification",
        terminals=[Terminal(id="t0", index=0, direction="input", name="notifier")],
        prim_id=9105,
    )
    resolved = ResolvedPrimitive(
        prim_id="9105",
        name="Wait on Notification",
        confidence="placeholder",
    )
    sink: list[Exception] = []
    ctx = CodeGenContext(
        soft_unresolved=True,
        unresolved_sink=sink,
        vi_name="Caller.vi",
    )
    _emit_placeholder(node, resolved, ctx)
    assert len(sink) == 1
    assert isinstance(sink[0], PrimitiveResolutionNeeded)
    assert getattr(sink[0], "is_placeholder", False) is True


def test_sink_absent_preserves_hard_raise() -> None:
    """With no sink and hard mode, the gap still raises (unchanged behavior)."""
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Mystery",
        terminals=[],
        prim_id=99999,
    )
    ctx = CodeGenContext(soft_unresolved=False, vi_name="Caller.vi")
    with pytest.raises(PrimitiveResolutionNeeded):
        _emit_unknown(node, prim_id=99999, ctx=ctx)


# ---------------------------------------------------------------------------
# Report formatting + aggregation shape.
# ---------------------------------------------------------------------------


def test_format_empty_report() -> None:
    assert "No unresolved" in format_unresolved_report([], "Foo.vi")


def test_format_groups_and_counts() -> None:
    items = [
        UnresolvedItem(
            kind="unknown_primitive",
            identifier="99999",
            name="Mystery",
            vi_names=["Lib.lvlib:A.vi", "Lib.lvlib:B.vi"],
            count=3,
        ),
        UnresolvedItem(
            kind="placeholder_primitive",
            identifier="9105",
            name="Wait on Notification",
            vi_names=["Lib.lvlib:A.vi"],
            count=1,
        ),
        UnresolvedItem(
            kind="unmapped_vilib",
            identifier="Foo.vi",
            name="Foo.vi",
            vi_names=["Lib.lvlib:A.vi"],
            count=1,
        ),
    ]
    report = format_unresolved_report(items, "Lib.lvlib")
    assert "Unknown primitives" in report
    assert "Placeholder primitives" in report
    assert "Unmapped vi.lib VIs" in report
    assert "[prim 99999] Mystery" in report
    assert "[prim 9105] Wait on Notification" in report
    assert "3x in 2 VI(s)" in report
    assert "Lib.lvlib:A.vi" in report


# ---------------------------------------------------------------------------
# End-to-end on the JKI corpus (skips when the sample corpus is absent).
# ---------------------------------------------------------------------------

_JKI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/Calculate Test Coverage.vi"
)


@pytest.mark.slow
def test_collect_unresolved_on_corpus_vi() -> None:
    """The command surfaces the OpenG terminal-mapping gap in one pass."""
    if not _JKI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    items = collect_unresolved(
        _JKI,
        search_paths=[_JKI.parents[3]],
    )
    # The OpenG __ogtk.vi dependencies don't resolve here → at least one gap.
    assert items
    names = {it.name for it in items}
    assert any("Strip Path Extension" in n for n in names)


_TESTCASE_CLASS = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Classes/TestCase/TestCase.lvclass"
)


@pytest.mark.slow
def test_q33_unresolved_gap_counts_on_testcase_class() -> None:
    """Eval bank Q33 ground truth: `lvkit unresolved` on TestCase.lvclass.

    Pinned per the eval-harness convention (docs/_internal/mcp-evals.md): if a
    future change resolves one of these primitives/vi.lib VIs (or a regression
    stops detecting one), this count shifts and the test fails -- the signal to
    update both the pin and the eval bank. Ground truth as of this branch:
    18 distinct gaps = 9 placeholder primitives + 8 terminal-mapping + 1
    unknown primitive (the generic eventRegNode)."""
    from collections import Counter

    if not _TESTCASE_CLASS.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    items = collect_unresolved(
        _TESTCASE_CLASS,
        search_paths=[_TESTCASE_CLASS.parents[2]],
    )
    counts = Counter(it.kind for it in items)
    assert len(items) == 18, f"expected 18 distinct gaps, got {len(items)}: {counts}"
    assert counts["placeholder_primitive"] == 9
    assert counts["terminal_mapping"] == 8
    assert counts["unknown_primitive"] == 1
