"""A Property Node whose reference input is unwired has no Python value to act
on unless its property is documented (see ``default_objects``); a primitive with
no implementation stops generation. Neither becomes a run-time object."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode
from tests.error_diagrams import (
    ERR,
    QUEUE,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
CONFIG_VI = SAMPLES / "DCAF-DAQModule/source/testing/Config.vi"


def _generate(vi: Path) -> str:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    return build_module(graph.get_vi_context(name), name, graph=graph)


def test_unwired_daqmx_reference_raises_naming_class_and_property():
    source = _generate(CONFIG_VI)
    assert "raise NotImplementedError(" in source
    assert "default DAQmx System object" in source


def test_a_primitive_without_an_implementation_raises_where_it_runs(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi("Menu.vi")
    menu = vi.control("Menu", QUEUE, REQUIRED)
    out = vi.indicator("Menu Out", QUEUE, REQUIRED)
    insert = vi.primitive(
        9003,
        [
            ("menu reference", 9, QUEUE, False),
            ("error in", 6, ERR, False),
            ("menu reference out", 3, QUEUE, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(menu, insert["menu reference"])
    vi.wire(insert["menu reference out"], out)
    project.add(vi)
    source = project.generate("Menu.vi").source
    assert "raise NotImplementedError(" in source
    assert "Insert Menu Items is not converted yet: " in source
    assert "run-time menu has no Python equivalent" in source
    assert "gap kind: placeholder_primitive" in source
