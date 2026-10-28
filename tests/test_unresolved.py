"""Tests for `lvkit unresolved` — batch resolution-gap collection."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.gaps import gap_of
from lvkit.codegen.nodes import generate as generate_node
from lvkit.codegen.nodes.primitive import raise_placeholder, raise_unknown
from lvkit.codegen.nodes.subvi import _raise_vilib_resolution
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.models import Terminal
from lvkit.primitive_resolver import PrimitiveResolutionNeeded
from lvkit.primitive_resolver.models import ResolvedPrimitive
from lvkit.unresolved import (
    UnresolvedItem,
    collect_unresolved,
    format_unresolved_report,
)
from lvkit.vilib_resolver import VILibResolutionNeeded

# ---------------------------------------------------------------------------
# The kind of each gap.
# ---------------------------------------------------------------------------


def _caught(raiser, *args, **kwargs) -> ConversionGap:
    with pytest.raises(ConversionGap) as caught:
        raiser(*args, **kwargs)
    return caught.value


def test_unknown_primitive_gap() -> None:
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Mystery",
        terminals=[Terminal(id="t0", index=0, direction="output", name="result")],
        prim_id=99999,
    )
    ctx = CodeGenContext(vi_name="Caller.vi")
    exc = _caught(raise_unknown, node, 99999, ctx)
    assert isinstance(exc, PrimitiveResolutionNeeded)
    gap = gap_of(exc, "Caller.vi", node)
    assert (gap.kind, gap.identifier, gap.name) == (
        "unknown_primitive",
        "99999",
        "Mystery",
    )


def test_unmapped_vilib_gap() -> None:
    node = VINode(
        id="s",
        vi_path="test.vi",
        name="Imaginary VI.vi",
        terminals=[Terminal(id="t1", index=0, direction="input", name="in1")],
        node_type="iUse",
    )
    exc = _caught(_raise_vilib_resolution, node, CodeGenContext(vi_name="C.vi"), None)
    assert isinstance(exc, VILibResolutionNeeded)
    gap = gap_of(exc, "C.vi", node)
    assert (gap.kind, gap.identifier) == ("unmapped_vilib", "Imaginary VI.vi")


def test_placeholder_primitive_gap_is_tagged_apart_from_unknown() -> None:
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Wait on Notification",
        terminals=[Terminal(id="t0", index=0, direction="input", name="notifier")],
        prim_id=9105,
    )
    resolved = ResolvedPrimitive(
        prim_id="9105", name="Wait on Notification", confidence="placeholder"
    )
    exc = _caught(raise_placeholder, node, resolved, CodeGenContext(vi_name="C.vi"))
    gap = gap_of(exc, "C.vi", node)
    assert gap.kind == "placeholder_primitive"
    assert gap.reason == "Wait on Notification is not converted yet"


def test_any_other_node_gap_is_an_unsupported_node() -> None:
    node = PrimitiveNode(
        id="p", vi_path="t.vi", name="Property Node", node_type="propNode", terminals=[]
    )
    gap = gap_of(ConversionGap("no Python equivalent", node), "C.vi", node)
    assert (gap.kind, gap.name, gap.node_name) == (
        "unsupported_node",
        "no Python equivalent",
        "Property Node",
    )


def test_a_gap_in_no_node_is_an_unsupported_vi() -> None:
    gap = gap_of(ConversionGap("no parent"), "Lib/Class.lvclass", None)
    assert (gap.kind, gap.identifier, gap.node_name) == (
        "unsupported_vi",
        "Lib/Class.lvclass",
        None,
    )


# ---------------------------------------------------------------------------
# Report formatting + aggregation shape.
# ---------------------------------------------------------------------------


def test_format_empty_report() -> None:
    assert format_unresolved_report([], "Foo.vi") == "No gaps in Foo.vi."


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
    34 distinct gaps = 6 placeholder primitives + 11 terminal-mapping + 4
    unknown primitives + 1 Event Structure + 12 VIs that were not found."""
    from collections import Counter

    if not _TESTCASE_CLASS.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    items = collect_unresolved(
        _TESTCASE_CLASS,
        search_paths=[_TESTCASE_CLASS.parents[2]],
    )
    counts = Counter(it.kind for it in items)
    assert len(items) == 34, f"expected 34 distinct gaps, got {len(items)}: {counts}"
    assert counts["placeholder_primitive"] == 6
    assert counts["terminal_mapping"] == 11
    assert counts["unknown_primitive"] == 4
    assert counts["unsupported_node"] == 1
    assert counts["unsupported_vi"] == 12


def test_placeholder_primitive_raises_where_it_runs() -> None:
    """A primitive known by name with no implementation generates a plain
    ``raise NotImplementedError`` at its place, importing nothing from lvkit."""
    node = PrimitiveNode(
        id="p",
        vi_path="test.vi",
        name="Wait on Notification",
        terminals=[
            Terminal(id="t0", index=0, direction="input", name="notifier"),
            Terminal(id="t1", index=1, direction="output", name="message"),
        ],
        prim_id=9105,
    )
    ctx = CodeGenContext(vi_name="Caller.vi")
    fragment = generate_node(node, ctx)
    assert fragment.imports == set()
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    raised = ast.unparse(module).splitlines()[-1]
    assert raised.startswith(
        "raise NotImplementedError('Wait on Notification is not converted yet\\n"
        "gap kind: placeholder_primitive\\nVI: Caller.vi\\n"
        "node: Wait on Notification\\n"
    )
    assert "index=0 direction=input" in raised
