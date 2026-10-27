"""A Property Node whose reference input is unwired has no Python value to act
on unless its property is documented (see ``default_objects``); a primitive with
no implementation stops generation. Neither becomes a run-time object."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors import CodeGenError
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
CONFIG_VI = SAMPLES / "DCAF-DAQModule/source/testing/Config.vi"
CURRENT_VI_PATH_VI = (
    SAMPLES
    / "OpenG/extracted/File Group 0/user.lib/_OpenG.lib/file/file.llb"
    / "Current VI's Path__ogtk.vi"
)


def _generate(vi: Path) -> str:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    return build_module(graph.get_vi_context(name), name, graph=graph)


def test_unwired_daqmx_reference_stops_generation_naming_class_and_property():
    with pytest.raises(CodeGenError, match="default DAQmx System object"):
        _generate(CONFIG_VI)


def test_a_primitive_without_an_implementation_raises_where_it_runs():
    source = _generate(CURRENT_VI_PATH_VI)
    assert "raise NotImplementedError('Close Reference is not converted yet')" in (
        source
    )
