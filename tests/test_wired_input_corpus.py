"""Corpus VIs whose wired inputs had no variable: each generates with the gap or
the default LabVIEW gives instead of stopping."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.pipeline import generate_python

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
AF = SAMPLES / "actor-framework"


def _generate(vi: Path, tmp_path: Path) -> tuple[str, list[str]]:
    result = generate_python(vi, tmp_path)
    written = Path(result["vi_paths"][str(vi.resolve())])
    return written.read_text(), [gap.reason for gap in result["gaps"]]


def test_a_disable_structure_output_left_unwired_sends_the_type_default(
    tmp_path: Path,
) -> None:
    vi = (
        AF
        / "Core/ActorFramework/Time-Delayed Send Message"
        / "Time-Delayed Send Message.vi"
    )
    source, _ = _generate(vi, tmp_path)
    assert "tunnel_default = None" in source


def test_a_bundle_with_an_element_that_selects_no_field_is_a_gap(
    tmp_path: Path,
) -> None:
    vi = (
        AF
        / "Providers/MessageMakerProvider/_Message Maker/_templates"
        / "Concrete Message Template/Dummy Read Attributes.vi"
    )
    source, gaps = _generate(vi, tmp_path)
    assert any("selects no field of its cluster" in gap for gap in gaps)
    assert "selects no field of its cluster" in source


def test_wires_that_close_a_cycle_make_each_node_of_it_a_gap(tmp_path: Path) -> None:
    root = SAMPLES / "lv-flex-channel-examples"
    _, gaps = _generate(root / "DAQ AO SFP/Main.vi", tmp_path)
    cycle = [gap for gap in gaps if "read as a cycle" in gap]
    assert len(cycle) == 3
    assert cycle[0] == (
        "The wires between 'DAQ AO SFP.lvlib:Control.vi', "
        "'DAQmx AO.lvlib:DAQ AO.vi', 'WaveGen.lvlib:WaveGen.vi' read as a cycle, "
        "which a saved, runnable VI cannot have; lvkit is likely misreading a "
        "terminal's direction here"
    )
