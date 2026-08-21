"""Regression tests over the committed issue-reproduction corpus.

Each test renders / describes / inspects a minimal repro that a user attached to
a GitHub issue (kept under ``tests/corpus/issues/<N>/``, Apache-2.0 — see that
dir's README) and asserts the corrected behaviour, so a fixed bug stays fixed.
These fixtures are IN-REPO, so unlike the ``needs_samples`` corpus tests they
always run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    CaseStructureNode,
    DisableStructureNode,
    SequenceNode,
)
from lvkit.models import DisableStructureKind
from lvkit.render import render_vi_file
from lvkit.render.scene import _frame_info

_CORPUS = Path(__file__).resolve().parent / "corpus" / "issues"


def _fixture_vis() -> list:
    """Every ``.vi`` under the committed issue corpus, paired with its issue
    directory (used as the search-path root so a multi-VI project resolves)."""
    params = []
    for issue_dir in sorted(p for p in _CORPUS.iterdir() if p.is_dir()):
        for vi in sorted(issue_dir.rglob("*.vi")):
            params.append(pytest.param(vi, issue_dir, id=f"{issue_dir.name}-{vi.stem}"))
    return params


def _load(rel: str) -> tuple[InMemoryVIGraph, str]:
    vi = _CORPUS / rel
    graph = InMemoryVIGraph()
    graph.load_vi(vi, mode=LoadMode.MINIMAL, search_paths=[vi.parent], layout=True)
    return graph, graph.resolve_vi_name(vi.name)


def test_issue36_bundle_unbundle_and_waveform_field_names():
    """#36: every named bundle/unbundle / component drawer resolves a real field
    name — no bracketed-index fallbacks.

    (A) A drawer selecting cluster field index 0 has its ``<i>``/``<index>``
    element OMITTED by LabVIEW; lvkit used to fall back to the drawer's list
    position, so the reporter's ``Name`` field (index 0, at list slot 2) rendered
    as a second ``size``. (B) Waveform / digital-data component nodes resolve
    their built-in component names (``t0/dt/Y/attributes``, ``data/transitions``)
    from the MeasureData flavor instead of showing ``[N]`` indices.
    """
    graph, vi = _load("36/bundle-unbundle-names.vi")
    resolved: set[str] = set()
    unresolved: list[tuple[str | None, int | None]] = []
    for node in graph.iter_nodes(vi):
        nt = str(getattr(node, "node_type", None))
        if nt not in ("nMux", "decomposeClusterNode"):
            continue
        for term in node.terminals:
            if getattr(term, "nmux_role", None) != "list":
                continue
            if term.display_name:
                resolved.add(term.display_name)
            else:
                unresolved.append((node.name, term.nmux_field_index))
    # (A) the custom cluster's index-0 'Name'; (B) the waveform + digital-data
    # component names — all resolved, none left as an index fallback.
    expected = {"Name", "size", "t0", "dt", "Y", "attributes", "data", "transitions"}
    assert expected <= resolved, f"missing field names {expected - resolved}"
    assert not unresolved, f"drawers left as an index fallback: {unresolved}"


def test_issue30_case_and_sequence_open_on_saved_visible_frame():
    """#30: a Case Structure and a Stacked Sequence saved with a non-first frame
    showing must open on that frame, not frame 0.

    The displayed frame is the structure node's heap ``dIdx`` when it is a valid
    local index (``0 <= dIdx < n_frames``); an out-of-range ``dIdx`` is a legacy
    global-diagram ordinal and is rejected (frame-0 fallback). Both structures in
    this repro carry ``dIdx=1`` (their 2nd frame).
    """
    graph, vi = _load("30/visible-frame-case-sequence.vi")
    seen: dict[str, int | None] = {}
    for node in graph.iter_nodes(vi):
        if isinstance(node, CaseStructureNode):
            seen["case"] = node.displayed_frame
        elif isinstance(node, SequenceNode) and node.node_type != "flatSequence":
            seen["sequence"] = node.displayed_frame
    assert seen == {"case": 1, "sequence": 1}, seen


def test_issue30_conditional_disable_opens_on_saved_visible_frame():
    """#30 (issue's second comment): a Conditional Disable Structure saved while
    viewing a non-enabled frame must open on that SAVED VISIBLE frame (heap
    ``dIdx``), not the enabled subdiagram.

    Both structures here carry ``dIdx=1`` (their 2nd frame). One (uid ending 42)
    has that frame *disabled* in the IDE (``RUN_TIME_ENGINE==True``) with no
    ``activeDiag`` — before the fix it fell back to frame 0. The visible frame
    (``dIdx``) is now preferred over the enabled one.
    """
    graph, vi = _load("30/visible-frame-conditional-disable.vi")
    nodes = [n for n in graph.iter_nodes(vi) if isinstance(n, DisableStructureNode)]
    assert len(nodes) == 2
    assert all(n.displayed_frame == 1 for n in nodes), [
        n.displayed_frame for n in nodes
    ]
    # The renderer opens both on frame index 1 (the saved visible frame).
    default_frame, frame_values, _, _ = _frame_info(
        list(graph.iter_nodes(vi)), vi, graph
    )
    for n in nodes:
        raw = n.id.split("::")[-1]
        shown = default_frame[raw]
        assert frame_values[raw].index(shown) == 1, (raw, shown)


@pytest.mark.parametrize("vi,issue_dir", _fixture_vis())
def test_issue_fixture_renders(vi: Path, issue_dir: Path):
    """Every committed issue-repro VI loads and renders to a non-empty SVG --
    a crash/regression guard over the whole issue corpus. (Bug-specific
    behaviour is asserted by the dedicated per-issue tests below.)"""
    svg = render_vi_file(vi, search_paths=[issue_dir])
    assert svg is not None, f"render returned None for {vi.name}"
    assert "<svg" in svg[:500]
    assert len(svg) > 200


def test_issue31_disable_structure_frame_names():
    """#31: disable-family frame labels are data-driven from ``activeDiag`` +
    the detected subtype -- no ``Frame N`` placeholders on the labelable ones.

    Diagram Disable -> Enabled/Disabled; Conditional Disable -> each frame's own
    decoded condition (empty => Default); Type Specialization -> bare
    storage-order ``[i]`` (LabVIEW's ``[N]`` + Accepted/Declined/Ignored are a
    compile result it does not persist, so we don't fabricate them).
    """
    graph, vi = _load("31/disable-structure-frame-names.vi")
    # Ordered (not set) per subtype — so a duplicate label or an
    # Enabled/Disabled inversion actually fails, not just a missing name.
    labels_by_kind = {
        node.kind: [str(f.selector_value) for f in node.frames]
        for node in graph.iter_nodes(vi)
        if isinstance(node, DisableStructureNode)
    }
    assert labels_by_kind[DisableStructureKind.DIAGRAM] == ["Enabled", "Disabled"]
    assert labels_by_kind[DisableStructureKind.CONDITIONAL] == [
        "Default",
        "RUN_TIME_ENGINE==False",
    ]
    assert labels_by_kind[DisableStructureKind.TYPE_SPEC] == ["[0]", "[1]", "[2]"]
