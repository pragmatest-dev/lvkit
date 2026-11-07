"""A Property Node whose reference input is unwired has no Python value to act
on unless its property is documented (see ``default_objects``); a primitive with
no implementation stops generation. Neither becomes a run-time object."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
CONFIG_VI = SAMPLES / "DCAF-DAQModule/source/testing/Config.vi"
OBTAIN_BROADCAST_EVENTS_VI = (
    SAMPLES
    / "configurable-dqmh-example/source/LabVIEW Data/DQMH Module Templates"
    / "Source/Delacor/ConfigurableQMH/Obtain Broadcast Events.vi"
)


def _generate(vi: Path) -> str:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    return build_module(graph.get_vi_context(name), name, graph=graph)


def test_unwired_daqmx_reference_raises_naming_class_and_property():
    source = _generate(CONFIG_VI)
    assert "raise NotImplementedError(" in source
    assert "default DAQmx System object" in source


def test_a_primitive_without_an_implementation_raises_where_it_runs():
    source = _generate(OBTAIN_BROADCAST_EVENTS_VI)
    assert (
        "raise NotImplementedError('Create User Event is not converted yet\\n"
        "gap kind: placeholder_primitive\\n"
    ) in source
