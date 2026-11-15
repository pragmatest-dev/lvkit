"""A VI reference to a control's file (a type definition), generated from the
Game Of Life measurement plug-in: ``Get Plugin Paths.vi`` reads the path of each
VI and control the plug-in is made of, and the generated class imports the
control's module to read its file."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.pipeline import generate_python
from tests.corpus_graphs import OPENG_USERLIB

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
MEASUREMENT_PLUGIN = SAMPLES / "measurement-plugin-labview" / "Source"
GET_PLUGIN_PATHS = (
    MEASUREMENT_PLUGIN
    / "Example Measurements"
    / "Game Of Life"
    / "Game Of Life"
    / "Advanced"
    / "Get Plugin Paths.vi"
)


def test_the_plugin_paths_name_the_modules_of_the_controls(tmp_path: Path) -> None:
    generate_python(
        GET_PLUGIN_PATHS,
        tmp_path,
        userlib_root=OPENG_USERLIB,
        search_path=[str(MEASUREMENT_PLUGIN / "Runtime")],
    )
    sys.path.insert(0, str(tmp_path))
    try:
        (package,) = (p.name for p in tmp_path.iterdir() if p.is_dir())
        module = importlib.import_module(
            f"{package}.game_of_life.advanced.measurement_plugin"
        )
        paths = module.MeasurementPlugin().get_plugin_paths().plugin_paths
        assert Path(paths.measurement_configuration_ctl_path).name == (
            "measurement_configurationctl.py"
        )
        assert Path(paths.measurement_results_ctl_path).name == (
            "measurement_resultsctl.py"
        )
        assert Path(paths.measurement_logic_vi_path).name == "measurement_logic.py"
    finally:
        sys.path.remove(str(tmp_path))
        for loaded in [m for m in sys.modules if m.split(".")[0] == package]:
            del sys.modules[loaded]
