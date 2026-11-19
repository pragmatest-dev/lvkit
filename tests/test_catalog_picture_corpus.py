"""A corpus VI that draws with the Picture VIs, generated through the real pipeline,
imported and run: JKI VI Tester's Create Bar Graph.vi draws one filled bar and the
name under it per value."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from tests.corpus_graphs import OPENG_USERLIB

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
GENERATE = Path(__file__).resolve().parent.parent / "scripts" / "generate_python.py"


def test_create_bar_graph_draws_a_bar_of_each_color(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pil = pytest.importorskip("PIL.Image")
    vi = (
        SAMPLES
        / "JKI-VI-Tester/source/User Interfaces/Estimate Test Coverage"
        / "Estimate Test Coverage Support/Create Bar Graph.vi"
    )
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    done = subprocess.run(
        [
            sys.executable,
            str(GENERATE),
            str(vi),
            "-o",
            str(tmp_path),
            "--userlib",
            str(OPENG_USERLIB),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        encoding="utf-8",
    )
    assert done.returncode in (0, 3), f"{done.stdout}\n{done.stderr}"
    package = next(p for p in tmp_path.iterdir() if p.is_dir())
    assert "Pillow" in (package / "requirements.txt").read_text(encoding="utf-8")
    monkeypatch.syspath_prepend(str(package.parent))
    module = importlib.import_module(
        f"{package.name}.user_interfaces.estimate_test_coverage"
        ".estimate_test_coverage_support.create_bar_graph"
    )
    image = module.create_bar_graph(
        values=[100.0, 50.0], names=["a", "b"], colors=[0xFF0000, 0x00FF00]
    ).bar_graph
    assert isinstance(image, pil.Image)
    # Bar 0 spans x 25..50 over the whole 200-pixel height; bar 1 (x 175..200)
    # the lower half.
    assert image.getpixel((30, 100)) == (255, 0, 0, 255)
    assert image.getpixel((180, 150)) == (0, 255, 0, 255)
    assert image.getpixel((180, 50))[3] == 0
