"""Codegen must be byte-for-byte reproducible across processes.

Node UIDs live in per-VI sets whose iteration order is randomized by
PYTHONHASHSEED between processes. If any ordering-sensitive step (operation
order, parallel-tier membership, inner-structure ordering, variable naming)
iterates such a set, the generated source changes from run to run. This test
generates the same VI under several hash seeds and asserts identical output.

``test TCX read (installed 71).vi`` is chosen because it emits a
concurrent.futures parallel tier — the exact path where independent
operations used to come out in set order. (It's a plain VI, not a class
member — .lvclass entry points make generate_python.py expand and emit a
wrapper for the *whole* class, which as of this writing has a real,
independently-tracked hashseed-order bug in one sibling method's parallel-
tier grouping; using a plain VI here keeps this test scoped to the ordering
path it's meant to cover.)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "generate_python.py"
# A permissively-licensed (BSD-3-Clause) VI that emits a parallel
# (ThreadPoolExecutor) tier.
PARALLEL_VI = (
    REPO
    / ".lvkit"
    / "cache"
    / "samples"
    / "JKI-EasyXML"
    / "Source"
    / "Fast Parser"
    / "test TCX read (installed 71).vi"
)
PARALLEL_VI_SEARCH_PATH = (
    REPO / ".lvkit" / "cache" / "samples" / "JKI-EasyXML" / "Source"
)  # noqa: E501


def _generate(vi: Path, out_dir: Path, hashseed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(vi),
            "-o",
            str(out_dir),
            "--search-path",
            str(PARALLEL_VI_SEARCH_PATH),
        ],
        check=True,
        capture_output=True,
        env=env,
        cwd=REPO,
    )
    return "\n".join(
        f"# === {p.relative_to(out_dir)} ===\n{p.read_text()}"
        for p in sorted(out_dir.rglob("*.py"))
    )


@pytest.mark.skipif(not PARALLEL_VI.exists(), reason="sample VI not present")
def test_codegen_is_hashseed_independent(tmp_path):
    outputs = [
        _generate(PARALLEL_VI, tmp_path / f"seed_{seed}", seed)
        for seed in ("0", "1", "12345")
    ]
    assert outputs[0], "expected generated Python output"
    assert "ThreadPoolExecutor" in outputs[0], (
        "fixture should exercise the parallel-tier ordering path"
    )
    # Every seed must produce identical source.
    assert outputs[1] == outputs[0]
    assert outputs[2] == outputs[0]
