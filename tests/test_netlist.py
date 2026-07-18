"""Tests for lvkit netlist -- graph -> text netlist projection.

Loads the JKI VI-Tester ``run.vi`` pair staged in the scratchpad (mirrors
``tests/test_diff.py``'s load-and-skip-if-absent convention: these VIs are
not part of the repo's sample corpus, so tests degrade gracefully when
they're not present in a given environment).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.netlist import build_netlist, render_netlist

SCRATCHPAD = Path(
    "/tmp/claude-1000/-home-ryanf-repos-lvkit/3a7f874f-386b-432d-9712-edf3bc6c995e"
    "/scratchpad"
)
RUN_OLD = SCRATCHPAD / "run_OLD.vi"
RUN_NEW = SCRATCHPAD / "run_NEW.vi"
DEMO_SEARCH_PATH = Path(__file__).resolve().parent.parent / ".tmp" / "vi-tester-demo"


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(str(vi_path), search_paths=[DEMO_SEARCH_PATH], layout=False)
    vi_name = graph.resolve_vi_name(vi_path.name)
    return graph, vi_name


def _require_vis() -> None:
    if not RUN_OLD.exists() or not RUN_NEW.exists():
        pytest.skip("JKI run_OLD.vi/run_NEW.vi pair not staged in scratchpad")


class TestBuildNetlist:
    """Execute build_netlist + render_netlist against the real run_NEW.vi
    and assert on real substrings/invariants -- never syntax-only, and
    never a hand-written full-text golden (too brittle)."""

    def test_contains_known_real_elements(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))

        assert "CallTestMethod" in out
        assert "addSkipped" in out
        assert "case (" in out

    def test_signature_line_has_arrow(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))

        header = out.splitlines()[0]
        assert "->" in header
        assert header.startswith(vi_name.split(":")[-1].rsplit(".vi", 1)[0]) or (
            vi_name in header
        )

    def test_output_is_ascii(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        assert out.isascii()

    def test_no_unicode_arrows_or_boxes(self):
        """The only arrow is '->' -- no '<-', no unicode box-drawing/arrows
        (the syntax is locked to plain ASCII)."""
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        assert "<-" not in out
        assert "→" not in out  # "->" unicode arrow
        assert "─" not in out  # box-drawing

    def test_every_case_scope_has_at_least_one_frame_line(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out = render_netlist(build_netlist(graph, vi_name))
        lines = out.splitlines()

        found_case = False
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if not (stripped.startswith("case (") or stripped == "case:"):
                continue
            found_case = True
            indent = len(ln) - len(ln.lstrip(" "))
            frame_lines = 0
            for nxt in lines[i + 1:]:
                nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                if nxt.strip() and nxt_indent <= indent:
                    break
                if nxt.strip().startswith('"'):
                    frame_lines += 1
            assert frame_lines >= 1, f"case scope at line {i} has no frame lines"
        assert found_case, "expected at least one 'case (' scope in run_NEW.vi"

    def test_build_and_render_repeatable_same_process(self):
        _require_vis()
        graph, vi_name = _load(RUN_NEW)
        out1 = render_netlist(build_netlist(graph, vi_name))
        out2 = render_netlist(build_netlist(graph, vi_name))
        assert out1 == out2

    def test_run_old_also_builds(self):
        """Sanity check the other half of the pair builds cleanly too."""
        _require_vis()
        graph, vi_name = _load(RUN_OLD)
        out = render_netlist(build_netlist(graph, vi_name))
        assert out.isascii()
        assert "->" in out.splitlines()[0]


def test_netlist_deterministic_across_hash_seeds():
    """Node UIDs live in hash-randomized sets (_vi_nodes), so occurrence
    numbering and node order must be checked across separate interpreters,
    not just re-running in-process (see test_render.py's identical pattern
    for render_vi_file)."""
    _require_vis()

    script = (
        "import hashlib\n"
        "from pathlib import Path\n"
        "from lvkit.graph.core import InMemoryVIGraph\n"
        "from lvkit.graph.netlist import build_netlist, render_netlist\n"
        f"graph = InMemoryVIGraph()\n"
        f"graph.load_vi({str(RUN_NEW)!r}, "
        f"search_paths=[Path({str(DEMO_SEARCH_PATH)!r})], layout=False)\n"
        f"vi_name = graph.resolve_vi_name({RUN_NEW.name!r})\n"
        "out = render_netlist(build_netlist(graph, vi_name))\n"
        "assert out.isascii()\n"
        "print(hashlib.sha256(out.encode()).hexdigest())\n"
    )

    digests = []
    for seed in ("0", "1234567"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]
