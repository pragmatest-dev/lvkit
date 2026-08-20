"""Regression tests over the committed issue-reproduction corpus.

Each test renders / describes / inspects a minimal repro that a user attached to
a GitHub issue (kept under ``tests/corpus/issues/<N>/``, Apache-2.0 — see that
dir's README) and asserts the corrected behaviour, so a fixed bug stays fixed.
These fixtures are IN-REPO, so unlike the ``needs_samples`` corpus tests they
always run.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode

_CORPUS = Path(__file__).resolve().parent / "corpus" / "issues"


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
