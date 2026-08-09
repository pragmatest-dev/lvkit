"""``front_panel.parse_connector_pane_labels`` -- the VCTP flat-type-table
fallback for a connector-pane terminal's name (used when the front-panel
object's own partID=16/82 label is empty/absent, e.g. a stripped build
copy — see docs/_internal/design/error-indicator-handoff.md).

Two things need proving, both against the real JKI-VI-Tester corpus (no
synthetic XML — the resource layout, in particular that a VI's VCTP embeds
several unrelated ``Type="Function"`` entries and only one of them is this
VI's OWN connector pane, is corpus-specific and easy to get wrong with a
hand-built fixture):

1. The mechanism WORKS: on a VI whose FP heap still has the label, the VCTP
   type table carries the SAME name independently, recoverable with zero
   dependency on the FP heap at all.
2. It's authoritative, not a heuristic first-match: several of these VIs
   have multiple same-shaped ``Function`` TypeDescs in one VCTP (their own
   connector pane plus call-site parameter types for other VIs they call);
   resolving via CONP/CPC2 must land on the VI's OWN connector pane, never
   an unrelated same-shaped one (this was the actual mistake in an earlier,
   rejected diagnosis of this bug -- see the handoff doc history).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml
from lvkit.parser.front_panel import parse_connector_pane_labels

pytestmark = pytest.mark.needs_samples

_CLEAN_ROOT = (
    Path(__file__).resolve().parent.parent
    / ".lvkit" / "cache" / "samples" / "JKI-VI-Tester"
    / "source" / "LabVIEW Project Plugin"
)
_BUILT_ROOT = (
    Path(__file__).resolve().parent.parent
    / ".lvkit" / "cache" / "samples" / "JKI-VI-Tester"
    / "source" / "Built Project Integration"
)

# VI name -> (output slot index, output label, input slot index, input label)
# Ground truth read directly off each clean VI's FPHb front-panel object
# labels (independent source), then cross-checked structurally: the input
# slot's referenced type is the same 3-field status/code/source cluster
# shape as the output slot's.
_CLEAN_ERROR_SLOTS = {
    "VITester_Item_NotifyChanged.vi": (0, "error out", 8, "error in"),
    "VITester_Provider_NotifyChanged.vi": (0, "error out", 8, "error in"),
    "VITester_Provider_Shutdown.vi": (0, "error out", 8, "error in (no error)"),
    "VITester_Provider_Startup.vi": (0, "error out", 8, "error in (no error)"),
}


@pytest.mark.parametrize("vi_filename", sorted(_CLEAN_ERROR_SLOTS))
def test_recovers_real_label_from_type_table_alone(vi_filename: str) -> None:
    """The clean siblings' error in/out names, read PURELY from the VCTP
    flat type table (this function never looks at the FP heap at all) --
    proves the mechanism recovers a real label, not just "doesn't crash"."""
    vi_path = _CLEAN_ROOT / vi_filename
    if not vi_path.exists():
        pytest.skip(f"{vi_filename} not present in sample corpus")

    _, _, main_xml = extract_vi_xml(vi_path)
    labels = parse_connector_pane_labels(main_xml)

    out_idx, out_label, in_idx, in_label = _CLEAN_ERROR_SLOTS[vi_filename]
    assert labels.get(out_idx) == out_label, (
        f"{vi_filename}: slot {out_idx} type-table label was "
        f"{labels.get(out_idx)!r}, expected {out_label!r}"
    )
    assert labels.get(in_idx) == in_label, (
        f"{vi_filename}: slot {in_idx} type-table label was "
        f"{labels.get(in_idx)!r}, expected {in_label!r}"
    )


@pytest.mark.parametrize("vi_filename", sorted(_CLEAN_ERROR_SLOTS))
def test_built_copy_type_table_is_also_stripped(vi_filename: str) -> None:
    """The 'Built Project Integration' copies are confirmed to have lost the
    label from BOTH sources, not just the FP heap -- the type-table fallback
    genuinely finds nothing here (this is why those VIs still report
    control_<uid>, not a bug in the recovery lookup itself)."""
    vi_path = _BUILT_ROOT / vi_filename
    if not vi_path.exists():
        pytest.skip(f"{vi_filename} not present in sample corpus")

    _, _, main_xml = extract_vi_xml(vi_path)
    labels = parse_connector_pane_labels(main_xml)

    out_idx, _out_label, in_idx, _in_label = _CLEAN_ERROR_SLOTS[vi_filename]
    assert out_idx not in labels, (
        f"{vi_filename}: expected slot {out_idx} to have no type-table label "
        f"in the built copy, got {labels.get(out_idx)!r}"
    )
    assert in_idx not in labels, (
        f"{vi_filename}: expected slot {in_idx} to have no type-table label "
        f"in the built copy, got {labels.get(in_idx)!r}"
    )


def test_resolves_own_connector_pane_not_an_unrelated_same_shaped_function() -> None:
    """VITester_Global_Init.vi's VCTP has >=2 distinct Type="Function"
    TypeDescs: its OWN connector pane (a near-empty pattern -- only one
    terminal, "Object", is actually wired) and an unrelated, same-slot-count
    Function describing some OTHER VI's parameter types at a call site
    inside Global_Init.vi's own block diagram (that unrelated one happens to
    have an "error out"-labeled slot). The resolver must land on the VI's
    OWN pane and therefore recover NOTHING for the (unwired, non-existent on
    this pane) error terminals -- picking up the unrelated Function's label
    would be exactly the false positive this design avoids."""
    vi_path = _BUILT_ROOT / "VITester_Global_Init.vi"
    if not vi_path.exists():
        pytest.skip("VITester_Global_Init.vi not present in sample corpus")

    _, _, main_xml = extract_vi_xml(vi_path)
    labels = parse_connector_pane_labels(main_xml)

    # The only real wired slot on this VI's actual connector pane is index
    # 11 ("Object"); nothing else should appear, and "error out" must not
    # leak in from the unrelated call-site Function.
    assert "error out" not in labels.values()
    assert "error in" not in labels.values()
    assert labels.get(11) == "Object"
