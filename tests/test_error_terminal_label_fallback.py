"""Regression: a compressed/stripped VI's error-cluster terminal must never
report a CHILD field name ("source"/"code"/"status") as its own label.

docs/_internal/design/error-indicator-handoff.md documents 14 VIs under
``JKI-VI-Tester/source/Built Project Integration`` whose front-panel-heap
label for the top-level error cluster control was nulled by whatever build /
repackaging step produced that copy (their ``source/LabVIEW Project Plugin``
siblings resolve cleanly as "error in" / "error out" — same uids, same
surrounding heap, only the cluster's OWN partID=16 label text differs, from
"error out" to a lone NUL byte). Investigated and confirmed NOT a
decompression bug: every other string in the same heap decodes fine,
including the child field names ("source"/"code"/"status") and the
boilerplate tooltip text, so pylabview's decompression is working — the
label bytes are simply gone from this copy. There is no clean-room path to
recover data that was never stored, so lvkit falls back to the graceful
``control_<uid>`` placeholder (never the leaked child-field name) and logs a
warning so the gap is visible instead of silently mis-naming a terminal.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, LoadMode

pytestmark = pytest.mark.needs_samples

_ROOT = (
    Path(__file__).resolve().parent.parent
    / ".lvkit" / "cache" / "samples" / "JKI-VI-Tester"
    / "source" / "Built Project Integration"
)

# All VITester_*.vi in this build-output directory; 14 of these carry a
# nulled error-cluster label per the handoff doc (the other 2, the
# *_Interface.vi files, expose no connector-pane error terminals at all).
_VI_NAMES = [
    "VITester_Global_Init.vi",
    "VITester_Global_Interface.vi",
    "VITester_Global_OnCommand.vi",
    "VITester_Global_OnUpdateCommand.vi",
    "VITester_Item_Init.vi",
    "VITester_Item_Interface.vi",
    "VITester_Item_NotifyChanged.vi",
    "VITester_Item_OnCommand.vi",
    "VITester_Item_OnPopupMenu.vi",
    "VITester_Provider_InitItems.vi",
    "VITester_Provider_Interface.vi",
    "VITester_Provider_NotifyChanged.vi",
    "VITester_Provider_OnCommand.vi",
    "VITester_Provider_OnPopupMenu.vi",
    "VITester_Provider_OnUpdateCommandBegin.vi",
    "VITester_Provider_Shutdown.vi",
    "VITester_Provider_Startup.vi",
]

# Real error-cluster field names — a terminal must NEVER report one of these
# as its OWN name (that's the "source" leak this test guards against).
_CLUSTER_FIELD_NAMES = {"status", "code", "source"}


def _error_terminal_names(graph: InMemoryVIGraph, vi_name: str) -> list[str]:
    names = []
    for t in graph.get_inputs(vi_name, public_only=False):
        if t.is_error_cluster:
            names.append(t.name)
    for t in graph.get_outputs(vi_name, public_only=False):
        if t.is_error_cluster:
            names.append(t.name)
    return names


@pytest.mark.parametrize("vi_filename", _VI_NAMES)
def test_error_terminal_never_leaks_a_cluster_field_name(
    vi_filename: str, caplog: pytest.LogCaptureFixture,
) -> None:
    vi_path = _ROOT / vi_filename
    if not vi_path.exists():
        pytest.skip(f"{vi_filename} not present in sample corpus")

    graph = InMemoryVIGraph()
    with caplog.at_level(logging.WARNING, logger="lvkit.parser.vi"):
        graph.load_vi(vi_path, LoadMode.NONE)

    error_names = _error_terminal_names(graph, vi_filename)
    for name in error_names:
        assert name not in _CLUSTER_FIELD_NAMES, (
            f"{vi_filename}: error-cluster terminal leaked child field name "
            f"{name!r} instead of falling back to control_<uid>"
        )
        # Every name must be either the real label or the graceful fallback —
        # never something else fabricated.
        assert name == "error in" or name == "error out" or name.startswith(
            "control_"
        ), f"{vi_filename}: unexpected error-cluster terminal name {name!r}"

    # This corpus's 14 VIs are confirmed to have unresolvable labels (the
    # cluster's own partID=16 text was nulled) -- when a terminal did fall
    # back to control_<uid>, a warning must have been logged, never silence.
    if any(n.startswith("control_") for n in error_names):
        assert any(
            "no resolvable label" in rec.message and vi_filename in rec.message
            for rec in caplog.records
        ), f"{vi_filename}: control_<uid> fallback fired without a warning"
