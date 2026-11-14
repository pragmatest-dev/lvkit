"""Corpus VIs that call catalog VIs, generated through the real pipeline: the
generated function is imported and run, and what it returns is asserted.

A VI whose other nodes are gaps (a project API, the measurement plug-in SDK, the
DAQmx hardware) cannot run: its generated source is checked instead."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.corpus_graphs import OPENG_USERLIB

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
GENERATE = Path(__file__).resolve().parent.parent / "scripts" / "generate_python.py"


def _generate(vi: Path, out: Path) -> Path:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    done = subprocess.run(
        [
            sys.executable,
            str(GENERATE),
            str(vi),
            "-o",
            str(out),
            "--userlib",
            str(OPENG_USERLIB),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode in (0, 3), f"{done.stdout}\n{done.stderr}"
    return next(p for p in out.iterdir() if p.is_dir())


def test_get_method_path_joins_the_proxy_name_to_the_folder_of_the_vi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The JKI VI calls OpenG's Current VI's Path with the relative path ``..``
    and builds a path to ``API Proxy - Open and Run All Tests.vi`` below it; the
    OpenG VI's own result is the path of the file it runs in, here the generated
    module."""
    vi = (
        SAMPLES
        / "JKI-VI-Tester/source/LabVIEW Project Plugin/Support"
        / "VI Tester Project API Get Method Path.vi"
    )
    package = _generate(vi, tmp_path)
    monkeypatch.syspath_prepend(str(package.parent))
    try:
        module = importlib.import_module(
            f"{package.name}.support.vi_tester_project_api_get_method_path"
        )
        result = module.vi_tester_project_api_get_method_path(project_api_method=1)
        module_file = Path(module.__file__).resolve()
        assert result.path_to_vi_tester_project_api_proxy_vi.resolve() == (
            module_file.parent / "API Proxy - Open and Run All Tests.vi"
        )
        assert result.error_out is None
    finally:
        for name in [m for m in sys.modules if m.split(".")[0] == package.name]:
            del sys.modules[name]


def test_wait_until_generation_complete_asks_the_session_and_times_apart(
    tmp_path: Path,
) -> None:
    """The VI reads two timestamps (``High Resolution Relative Seconds``): each is
    its own variable, so the duration is their difference. It cannot run: its
    Measure Call Context property node is a gap."""
    vi = (
        SAMPLES
        / "measurement-plugin-labview/Source/Example Measurements"
        / "NI-FGEN Standard Function/NIFgenStandardFunction/subVIs"
        / "Wait Until Generation Complete.vi"
    )
    package = _generate(vi, tmp_path)
    source = next(package.rglob("wait_until_generation_complete.py")).read_text()
    assert ".is_done()" in source
    names = re.findall(r"^\s*(\w+) = time\.perf_counter\(\)", source, re.MULTILINE)
    assert len(names) == len(set(names)) >= 2
